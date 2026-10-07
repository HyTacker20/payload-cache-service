from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock

import pytest
from sqlalchemy import Engine, event

from cache_service.database import create_database
from cache_service.schemas import PayloadInput
from cache_service.service import PayloadService
from cache_service.storage import PayloadFiles


def test_concurrent_first_start_creates_usable_database(tmp_path: Path) -> None:
    workers = 8
    starting = Barrier(workers)
    entering = Lock()
    engines: set[Engine] = set()

    def start_together(connection, cursor, statement, parameters, context, many):
        if connection.engine.url.database != str(tmp_path / "cache.sqlite3"):
            return
        with entering:
            first_statement = connection.engine not in engines
            engines.add(connection.engine)
        if first_statement:
            # Rendezvous before each connection's first statement, so a writer
            # can acquire its lock without waiting at a barrier while holding it.
            starting.wait(timeout=10)

    def initialize_and_create() -> tuple[str, bool, str]:
        engine = create_database(tmp_path)
        service = PayloadService(engine, PayloadFiles(tmp_path / "payloads"))
        result = service.create(PayloadInput(list_1=["a"], list_2=["b"]))
        return result.id, result.created, service.read(result.id).output

    event.listen(Engine, "before_cursor_execute", start_together)
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(
                executor.map(lambda _: initialize_and_create(), range(workers))
            )
        assert len({payload_id for payload_id, _, _ in results}) == 1
        assert sum(created for _, created, _ in results) == 1
        assert all(output == "A, B" for _, _, output in results)
    finally:
        event.remove(Engine, "before_cursor_execute", start_together)
        for engine in engines:
            engine.dispose()


def test_failed_initialization_closes_database_connection(tmp_path: Path) -> None:
    connections = []
    closed = []
    failed_engines: set[Engine] = set()

    def connected(connection, record):
        connections.append(connection)

    def closing(connection, record):
        closed.append(connection)

    def fail_statement(connection, cursor, statement, parameters, context, many):
        failed_engines.add(connection.engine)
        raise OSError("database unavailable")

    event.listen(Engine, "connect", connected)
    event.listen(Engine, "close", closing)
    event.listen(Engine, "before_cursor_execute", fail_statement)
    try:
        with pytest.raises(OSError, match="database unavailable"):
            create_database(tmp_path)
        assert len(connections) == 1
        assert closed == connections
    finally:
        event.remove(Engine, "connect", connected)
        event.remove(Engine, "close", closing)
        event.remove(Engine, "before_cursor_execute", fail_statement)
        for engine in failed_engines:
            engine.dispose()

    engine = create_database(tmp_path)
    try:
        service = PayloadService(engine, PayloadFiles(tmp_path / "payloads"))
        result = service.create(PayloadInput(list_1=["a"], list_2=["b"]))
        assert service.read(result.id).output == "A, B"
    finally:
        engine.dispose()
