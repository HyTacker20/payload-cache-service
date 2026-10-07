from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from cache_service.database import Payload, Transformation, create_database
from cache_service.generation import transform
from cache_service.schemas import PayloadInput
from cache_service.service import PayloadService, TransformationError
from cache_service.storage import PayloadFiles


@pytest.fixture
def service(tmp_path: Path):
    engine = create_database(tmp_path)
    transformer = Mock(side_effect=transform)
    instance = PayloadService(engine, PayloadFiles(tmp_path / "payloads"), transformer)
    yield instance, transformer
    engine.dispose()


def test_caches_duplicates_across_both_lists(service) -> None:
    instance, transformer = service
    request = PayloadInput(list_1=["a", "b", "a"], list_2=["b", "a", "b"])
    result = instance.create(request)

    assert result.created is True
    assert instance.read(result.id).output == "A, B, B, A, A, B"
    assert transformer.call_count == 2


def test_repeat_reuses_id_without_transforming(service) -> None:
    instance, transformer = service
    request = PayloadInput(list_1=["a"], list_2=["b"])
    first = instance.create(request)
    second = instance.create(request)

    assert second.id == first.id
    assert second.created is False
    assert transformer.call_count == 2


def test_partial_cache_only_transforms_new_strings(service) -> None:
    instance, transformer = service
    instance.create(PayloadInput(list_1=["a"], list_2=["b"]))
    transformer.reset_mock()

    instance.create(PayloadInput(list_1=["a", "c"], list_2=["c", "b"]))
    transformer.assert_called_once_with("c")


def test_different_inputs_with_same_output_reuse_payload(service) -> None:
    instance, _ = service
    first = instance.create(PayloadInput(list_1=["a"], list_2=["b"]))
    second = instance.create(PayloadInput(list_1=["A"], list_2=["B"]))
    assert second.id == first.id
    assert second.created is False


def test_cache_keys_preserve_whitespace_and_case(service) -> None:
    instance, transformer = service
    result = instance.create(PayloadInput(list_1=["a", " A"], list_2=["A", "a "]))
    assert transformer.call_count == 4
    assert instance.read(result.id).output == "A, A,  A, A "


def test_persists_across_database_reopen(tmp_path: Path) -> None:
    engine = create_database(tmp_path)
    files = PayloadFiles(tmp_path / "payloads")
    first_service = PayloadService(engine, files)
    request = PayloadInput(list_1=["a"], list_2=["b"])
    first = first_service.create(request)
    engine.dispose()

    reopened = create_database(tmp_path)
    transformer = Mock(side_effect=AssertionError("cache should survive restart"))
    try:
        second_service = PayloadService(reopened, files, transformer)
        assert second_service.create(request).id == first.id
        assert second_service.read(first.id).output == "A, B"
        transformer.assert_not_called()
    finally:
        reopened.dispose()


def test_empty_lists_do_not_call_transformer(service) -> None:
    instance, transformer = service
    result = instance.create(PayloadInput(list_1=[], list_2=[]))
    assert instance.read(result.id).output == ""
    transformer.assert_not_called()


@pytest.mark.parametrize("damage", [None, "{broken json", '{"output":"wrong"}'])
def test_restores_missing_or_damaged_files(service, damage) -> None:
    instance, transformer = service
    result = instance.create(PayloadInput(list_1=["a"], list_2=["b"]))
    path = instance.files.directory / f"{result.id}.json"
    if damage is None:
        path.unlink()
    else:
        path.write_text(damage, encoding="utf-8")

    transformer.reset_mock()
    assert instance.read(result.id).output == "A, B"
    assert path.read_text(encoding="utf-8") == '{"output":"A, B"}\n'
    transformer.assert_not_called()


def test_unknown_payload_raises_lookup_error(service) -> None:
    instance, _ = service
    with pytest.raises(LookupError):
        instance.read("0" * 64)


def test_transform_failure_rolls_back_without_payload(service) -> None:
    instance, transformer = service
    transformer.side_effect = ["A", RuntimeError("external failure")]
    with pytest.raises(TransformationError):
        instance.create(PayloadInput(list_1=["a"], list_2=["b"]))

    with Session(instance.engine) as session:
        assert session.scalar(select(func.count()).select_from(Payload)) == 0
        assert session.scalar(select(func.count()).select_from(Transformation)) == 0
    assert not list(instance.files.directory.glob("*.json"))


def test_version_change_invalidates_transform_cache(service) -> None:
    instance, _ = service
    request = PayloadInput(list_1=["a"], list_2=["b"])
    first = instance.create(request)
    transformer = Mock(side_effect=lambda value: value + "!")
    changed = PayloadService(instance.engine, instance.files, transformer, version="v2")
    second = changed.create(request)
    assert second.id != first.id
    assert changed.read(second.id).output == "a!, b!"
    assert transformer.call_count == 2


def test_concurrent_independent_services_share_cache(tmp_path: Path) -> None:
    engine_1 = create_database(tmp_path)
    engine_2 = create_database(tmp_path)
    started = Event()
    release = Event()
    second_attempted = Event()
    request = PayloadInput(list_1=["a"], list_2=["a"])

    def slow_transform(value: str) -> str:
        started.set()
        assert release.wait(timeout=5)
        return transform(value)

    transformer = Mock(side_effect=slow_transform)
    files = PayloadFiles(tmp_path / "payloads")
    first = PayloadService(engine_1, files, transformer)
    second = PayloadService(engine_2, files, transformer)

    @event.listens_for(engine_2, "before_cursor_execute")
    def second_database_attempt(
        connection, cursor, statement, parameters, context, many
    ):
        if statement == "BEGIN IMMEDIATE":
            second_attempted.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(first.create, request)
            assert started.wait(timeout=5)
            second_future = executor.submit(second.create, request)
            assert second_attempted.wait(timeout=5)
            release.set()
            results = [first_future.result(timeout=5), second_future.result(timeout=5)]
        assert results[0].id == results[1].id
        assert sum(result.created for result in results) == 1
        transformer.assert_called_once_with("a")
    finally:
        release.set()
        engine_1.dispose()
        engine_2.dispose()


def test_large_input_uses_cache_across_query_batches(service) -> None:
    instance, transformer = service
    values = [f"value-{index}" for index in range(1100)]
    request = PayloadInput(list_1=values, list_2=values)
    first = instance.create(request)
    assert transformer.call_count == 1100
    transformer.reset_mock()
    assert instance.create(request).id == first.id
    transformer.assert_not_called()


def test_file_write_failure_cleans_temporary_files_and_rolls_back(
    service, monkeypatch
) -> None:
    instance, _ = service
    monkeypatch.setattr(
        "cache_service.storage.os.replace", Mock(side_effect=OSError("disk failure"))
    )
    with pytest.raises(OSError):
        instance.create(PayloadInput(list_1=["a"], list_2=["b"]))
    assert list(instance.files.directory.iterdir()) == []
    with Session(instance.engine) as session:
        assert session.scalar(select(func.count()).select_from(Payload)) == 0
        assert session.scalar(select(func.count()).select_from(Transformation)) == 0
