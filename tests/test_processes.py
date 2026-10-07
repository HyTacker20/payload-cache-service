import multiprocessing
from pathlib import Path

from sqlalchemy import event

from cache_service.database import create_database
from cache_service.generation import transform
from cache_service.schemas import PayloadInput
from cache_service.service import PayloadService
from cache_service.storage import PayloadFiles


def create_in_process(directory: str, attempted, release, results) -> None:
    root = Path(directory)
    engine = create_database(root)

    @event.listens_for(engine, "before_cursor_execute")
    def entering_transaction(connection, cursor, statement, parameters, context, many):
        if statement == "BEGIN IMMEDIATE":
            attempted.put(True)

    def counted_transform(value: str) -> str:
        if not release.wait(timeout=10):
            raise TimeoutError("test did not release transformer")
        with (root / "transformer-calls.txt").open("a", encoding="utf-8") as stream:
            stream.write(value + "\n")
        return transform(value)

    try:
        service = PayloadService(
            engine, PayloadFiles(root / "payloads"), counted_transform
        )
        result = service.create(PayloadInput(list_1=["a", "a"], list_2=["b", "a"]))
        results.put((result.id, result.created))
    finally:
        engine.dispose()


def test_separate_processes_do_not_duplicate_transformations(tmp_path: Path) -> None:
    create_database(tmp_path).dispose()
    context = multiprocessing.get_context("spawn")
    attempted = context.Queue()
    results = context.Queue()
    release = context.Event()
    processes = [
        context.Process(
            target=create_in_process, args=(str(tmp_path), attempted, release, results)
        )
        for _ in range(3)
    ]
    try:
        for process in processes:
            process.start()
        # All processes must attempt the database transaction while the first
        # transformer is held. This avoids an accidentally sequential test.
        for _ in processes:
            assert attempted.get(timeout=10) is True
        release.set()
        confirmations = [results.get(timeout=10) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        assert len({payload_id for payload_id, _ in confirmations}) == 1
        assert sum(created for _, created in confirmations) == 1
        assert (tmp_path / "transformer-calls.txt").read_text().splitlines() == [
            "a",
            "b",
        ]
    finally:
        release.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        attempted.close()
        results.close()
