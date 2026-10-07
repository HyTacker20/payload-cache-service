import errno
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from cache_service.storage import PayloadFiles

PAYLOAD_ID = "a" * 64


def sharing_violation() -> PermissionError:
    error = PermissionError(errno.EACCES, "file is open in another reader")
    error.winerror = 5
    return error


def test_retries_a_transient_windows_sharing_violation(tmp_path, monkeypatch) -> None:
    files = PayloadFiles(tmp_path)
    original_replace = os.replace
    attempts = 0

    def replace(source, destination) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sharing_violation()
        original_replace(source, destination)

    monkeypatch.setattr("cache_service.storage.os.replace", replace)
    assert files.materialize(PAYLOAD_ID, "A").output == "A"
    assert attempts == 2
    assert list(tmp_path.glob("*.tmp")) == []


def test_accepts_a_file_repaired_by_another_caller(tmp_path, monkeypatch) -> None:
    files = PayloadFiles(tmp_path)

    def another_writer_finished(source, destination) -> None:
        Path(destination).write_text('{"output":"A"}\n', encoding="utf-8")
        raise sharing_violation()

    monkeypatch.setattr("cache_service.storage.os.replace", another_writer_finished)
    assert files.materialize(PAYLOAD_ID, "A").output == "A"
    assert list(tmp_path.glob("*.tmp")) == []


def test_persistent_sharing_violation_has_bounded_retries(
    tmp_path, monkeypatch
) -> None:
    files = PayloadFiles(tmp_path)
    replace = Mock(side_effect=sharing_violation())
    monkeypatch.setattr("cache_service.storage.os.replace", replace)
    monkeypatch.setattr("cache_service.storage.time.sleep", lambda _: None)
    with pytest.raises(PermissionError):
        files.materialize(PAYLOAD_ID, "A")
    assert 1 < replace.call_count <= 10
    assert list(tmp_path.glob("*.tmp")) == []


def test_real_permission_errors_are_not_retried(tmp_path, monkeypatch) -> None:
    files = PayloadFiles(tmp_path)
    replace = Mock(side_effect=PermissionError(errno.EACCES, "permission denied"))
    monkeypatch.setattr("cache_service.storage.os.replace", replace)
    with pytest.raises(PermissionError):
        files.materialize(PAYLOAD_ID, "A")
    assert replace.call_count == 1
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("damage", [None, '{"output":"wrong"}'])
def test_concurrent_callers_repair_without_errors(tmp_path: Path, damage) -> None:
    # Separate storage instances also cover callers sharing files across services.
    stores = [PayloadFiles(tmp_path) for _ in range(20)]
    path = tmp_path / f"{PAYLOAD_ID}.json"
    with ThreadPoolExecutor(max_workers=20) as executor:
        for _ in range(5):
            if damage is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(damage, encoding="utf-8")
            results = list(
                executor.map(lambda store: store.materialize(PAYLOAD_ID, "A"), stores)
            )
            assert all(result.output == "A" for result in results)
    assert path.read_text(encoding="utf-8") == '{"output":"A"}\n'
    assert list(tmp_path.glob("*.tmp")) == []
