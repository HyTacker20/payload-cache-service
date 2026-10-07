import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from cache_service.api import create_app
from cache_service.generation import transform
from cache_service.settings import Settings


@pytest.fixture
def client(tmp_path: Path) -> Iterator[tuple[TestClient, Mock]]:
    transformer = Mock(side_effect=transform)
    app = create_app(Settings(data_dir=tmp_path), transformer=transformer)
    with TestClient(app) as test_client:
        yield test_client, transformer


def test_create_read_and_reuse(client) -> None:
    http, transformer = client
    request = {"list_1": ["first", "second"], "list_2": ["other", "last"]}
    first = http.post("/payload", json=request)
    assert first.status_code == 201
    payload_id = first.json()["id"]
    assert first.json()["created"] is True
    assert first.headers["location"] == f"/payload/{payload_id}"

    read = http.get(first.headers["location"])
    assert read.status_code == 200
    assert read.json() == {"output": "FIRST, OTHER, SECOND, LAST"}

    second = http.post("/payload", json=request)
    assert second.status_code == 200
    assert second.json()["id"] == payload_id
    assert second.json()["created"] is False
    assert transformer.call_count == 4


@pytest.mark.parametrize(
    "data",
    [
        {"list_1": ["a"], "list_2": []},
        {"list_1": [1], "list_2": ["a"]},
        {"list_1": [], "list_2": [], "unexpected": "value"},
        {"list_1": [], "list_2": None},
        {},
    ],
)
def test_invalid_request_never_calls_transformer(client, data) -> None:
    http, transformer = client
    assert http.post("/payload", json=data).status_code == 422
    transformer.assert_not_called()


def test_invalid_json(client) -> None:
    http, transformer = client
    response = http.post(
        "/payload", content="{broken", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    transformer.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        r'{"list_1":["\ud800"],"list_2":["a"]}',
        r'{"list_1":[],"list_2":[],"\ud800":1}',
    ],
)
def test_invalid_unicode_errors_are_safe_json(client, body) -> None:
    http, transformer = client
    response = http.post(
        "/payload", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert "detail" in response.json()
    transformer.assert_not_called()


def test_unknown_payload(client) -> None:
    http, transformer = client
    response = http.get("/payload/" + "0" * 64)
    assert response.status_code == 404
    assert response.json() == {"detail": "Payload not found"}
    transformer.assert_not_called()


def test_malformed_id(client) -> None:
    http, _ = client
    assert http.get("/payload/not-an-id").status_code == 422


def test_empty_payload(client) -> None:
    http, transformer = client
    response = http.post("/payload", json={"list_1": [], "list_2": []})
    assert http.get(response.headers["location"]).json() == {"output": ""}
    transformer.assert_not_called()


def test_transformer_failure_is_502_and_request_can_retry(client) -> None:
    http, transformer = client
    transformer.side_effect = RuntimeError("private upstream details")
    request = {"list_1": ["a"], "list_2": ["b"]}
    failed = http.post("/payload", json=request)
    assert failed.status_code == 502
    assert failed.json() == {"detail": "Transformer unavailable"}

    transformer.side_effect = transform
    assert http.post("/payload", json=request).status_code == 201


def test_database_failure_is_503(client, monkeypatch) -> None:
    http, _ = client
    monkeypatch.setattr(
        http.app.state.service,
        "create",
        Mock(side_effect=OperationalError("statement", {}, Exception("private"))),
    )
    response = http.post("/payload", json={"list_1": [], "list_2": []})
    assert response.status_code == 503
    assert response.json() == {"detail": "Storage unavailable"}


def test_file_failure_is_503(client, monkeypatch) -> None:
    http, _ = client
    monkeypatch.setattr(
        http.app.state.service.files,
        "materialize",
        Mock(side_effect=OSError("private file path")),
    )
    response = http.post("/payload", json={"list_1": [], "list_2": []})
    assert response.status_code == 503
    assert response.json() == {"detail": "Storage unavailable"}


def test_app_restart_preserves_payload_and_cache(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    request = {"list_1": ["a"], "list_2": ["b"]}
    with TestClient(create_app(settings)) as first:
        payload_id = first.post("/payload", json=request).json()["id"]
    transformer = Mock(side_effect=AssertionError("unexpected cache miss"))
    with TestClient(create_app(settings, transformer=transformer)) as second:
        assert second.get(f"/payload/{payload_id}").json() == {"output": "A, B"}
        assert second.post("/payload", json=request).json()["id"] == payload_id
        transformer.assert_not_called()


def test_settings_accept_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CACHE_DATA_DIR", str(tmp_path))
    assert Settings().data_dir == tmp_path


@pytest.mark.parametrize("content_type", ["application/octet-stream", "text/plain"])
def test_binary_request_returns_validation_error(client, content_type) -> None:
    http, transformer = client
    response = http.post(
        "/payload", content=b"\xff", headers={"Content-Type": content_type}
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body"]
    transformer.assert_not_called()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_rejected_nonfinite_numbers_have_standard_json_errors(client, value) -> None:
    http, transformer = client
    response = http.post(
        "/payload",
        content=f'{{"list_1":[{value}],"list_2":["a"]}}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422

    def reject_constant(token: str) -> None:
        pytest.fail(f"invalid JSON constant in response: {token}")

    detail = json.loads(response.text, parse_constant=reject_constant)["detail"]
    assert detail[0]["loc"] == ["body", "list_1", 0]
    transformer.assert_not_called()


def test_database_error_logs_do_not_disclose_input_values(client, caplog) -> None:
    http, _ = client
    with http.app.state.service.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER reject_insert BEFORE INSERT ON transformations "
                "BEGIN SELECT RAISE(FAIL, 'storage failure'); END"
            )
        )
    response = http.post(
        "/payload", json={"list_1": ["private-input-value"], "list_2": ["other"]}
    )
    assert response.status_code == 503
    assert "private-input-value" not in caplog.text
    assert "PRIVATE-INPUT-VALUE" not in caplog.text
