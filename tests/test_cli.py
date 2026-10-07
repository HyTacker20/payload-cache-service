import io
import json
from pathlib import Path

import httpx
import pytest

from cache_service.cli import main

PAYLOAD_ID = "a" * 64
REQUEST = '{"list_1":["first"],"list_2":["other"]}'


@pytest.fixture
def requests(monkeypatch):
    captured: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "POST":
            return httpx.Response(
                201,
                json={"id": PAYLOAD_ID, "created": True, "message": "Payload created"},
            )
        return httpx.Response(200, json={"output": "FIRST, OTHER"})

    client_class = httpx.Client
    monkeypatch.setattr(
        "cache_service.cli.httpx.Client",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs),
    )
    return captured


def test_json_repeat_outputs_json_lines(requests, capsys) -> None:
    assert main(["-j", REQUEST, "-r", "3"]) == 0
    captured = capsys.readouterr()
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert len(rows) == 3
    assert rows[0] == {"id": PAYLOAD_ID, "created": True, "output": "FIRST, OTHER"}
    assert captured.err == ""
    assert [request.method for request in requests] == ["POST", "GET"] * 3
    assert json.loads(requests[0].content) == json.loads(REQUEST)


def test_file_input_and_output(requests, tmp_path: Path, capsys) -> None:
    source = tmp_path / "input.json"
    target = tmp_path / "result.jsonl"
    source.write_text(REQUEST, encoding="utf-8")
    assert main(["-i", str(source), "-o", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["output"] == "FIRST, OTHER"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("args", [[], ["--input", "-"], ["-i", "-", "-o", "-"]])
def test_stdin_and_stdout(requests, monkeypatch, capsys, args) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(REQUEST))
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["id"] == PAYLOAD_ID


def test_host_supports_path_prefix_and_shortcut(requests) -> None:
    assert main(["-H", "http://example.test/api/", "-j", REQUEST]) == 0
    assert str(requests[0].url) == "http://example.test/api/payload"
    assert str(requests[1].url) == f"http://example.test/api/payload/{PAYLOAD_ID}"


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_does_not_contact_server(requests, capsys, flag) -> None:
    with pytest.raises(SystemExit) as caught:
        main([flag])
    assert caught.value.code == 0
    output = capsys.readouterr().out
    assert "\0" not in output
    assert "--host" in output
    assert "--repeat" in output
    assert "--json" in output
    assert requests == []


@pytest.mark.parametrize(
    "args",
    [
        ["-j", REQUEST, "-r", "0"],
        ["-j", REQUEST, "-r", "-2"],
        ["-j", REQUEST, "-r", "invalid"],
        ["-j", REQUEST, "--host", "invalid"],
        ["-j", REQUEST, "--host", "ftp://example.test"],
        ["-j", REQUEST, "--host", "http://example.test?query=1"],
        ["-j", REQUEST, "-i", "-"],
        ["-j", "not json"],
        ["-j", '{"list_1":[1],"list_2":["a"]}'],
        ["--unknown"],
    ],
)
def test_invalid_arguments_fail_before_network(requests, capsys, args) -> None:
    assert main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("cache-cli:")
    assert requests == []


def test_missing_input_file(requests, tmp_path: Path, capsys) -> None:
    assert main(["-i", str(tmp_path / "missing.json")]) == 2
    assert "cache-cli:" in capsys.readouterr().err
    assert requests == []


def test_network_failure(monkeypatch, capsys) -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client_class = httpx.Client
    monkeypatch.setattr(
        "cache_service.cli.httpx.Client",
        lambda **kwargs: client_class(
            transport=httpx.MockTransport(unavailable), **kwargs
        ),
    )
    assert main(["-j", REQUEST]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "connection refused" in output.err


@pytest.mark.parametrize(
    "failure", ["http", "json", "schema", "changed_id", "changed_output"]
)
def test_server_failures(monkeypatch, capsys, failure) -> None:
    posts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if failure == "http":
            return httpx.Response(503, json={"detail": "Storage unavailable"})
        if failure == "json":
            return httpx.Response(200, text="{bad")
        if failure == "schema":
            return httpx.Response(201, json={"id": "not-an-id"})
        if request.method == "POST":
            posts += 1
            payload_id = (
                "b" * 64 if failure == "changed_id" and posts == 2 else PAYLOAD_ID
            )
            return httpx.Response(
                201,
                json={"id": payload_id, "created": True, "message": "Payload created"},
            )
        output = (
            "changed" if failure == "changed_output" and posts == 2 else "FIRST, OTHER"
        )
        return httpx.Response(200, json={"output": output})

    client_class = httpx.Client
    monkeypatch.setattr(
        "cache_service.cli.httpx.Client",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs),
    )
    assert main(["-j", REQUEST, "-r", "2"]) == 1
    assert "cache-cli:" in capsys.readouterr().err


def test_output_failure(requests, tmp_path: Path, capsys) -> None:
    assert main(["-j", REQUEST, "-o", str(tmp_path)]) == 1
    assert "cache-cli:" in capsys.readouterr().err


def test_unrelated_environment_does_not_override_defaults(
    requests, monkeypatch, tmp_path: Path, capsys
) -> None:
    target = tmp_path / "unrelated-output.txt"
    target.write_text("keep this file", encoding="utf-8")
    monkeypatch.setenv("input", str(tmp_path / "missing.json"))
    monkeypatch.setenv("output", str(target))
    monkeypatch.setenv("json", "invalid unrelated JSON")
    monkeypatch.setattr("sys.stdin", io.StringIO(REQUEST))
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["output"] == "FIRST, OTHER"
    assert target.read_text(encoding="utf-8") == "keep this file"


def test_prefixed_environment_and_cli_precedence(requests, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CACHE_CLI_HOST", "http://example.test/base/")
    monkeypatch.setenv("CACHE_CLI_REPEAT", "3")
    monkeypatch.setenv("CACHE_CLI_JSON", REQUEST)
    assert main(["-r", "2"]) == 0
    assert len(requests) == 4
    assert str(requests[0].url) == "http://example.test/base/payload"
    assert len(capsys.readouterr().out.splitlines()) == 2


def test_prefixed_file_options(requests, monkeypatch, tmp_path: Path, capsys) -> None:
    source = tmp_path / "request.json"
    target = tmp_path / "result.jsonl"
    source.write_text(REQUEST, encoding="utf-8")
    monkeypatch.setenv("CACHE_CLI_INPUT", str(source))
    monkeypatch.setenv("CACHE_CLI_OUTPUT", str(target))
    assert main([]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["output"] == "FIRST, OTHER"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"])
def test_redirected_stdin_uses_utf8_independently_of_locale(
    requests, monkeypatch, bom
) -> None:
    data = {"list_1": ["Привіт"], "list_2": ["straße"]}
    stream = io.TextIOWrapper(
        io.BytesIO(bom + json.dumps(data, ensure_ascii=False).encode("utf-8")),
        encoding="cp1251",
    )
    monkeypatch.setattr("sys.stdin", stream)
    assert main([]) == 0
    assert json.loads(requests[0].content) == data


def test_literal_json_null_does_not_fall_back_to_stdin(
    requests, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(REQUEST))
    assert main(["--json", "null"]) == 2
    assert "cache-cli:" in capsys.readouterr().err
    assert requests == []


def test_literal_json_null_does_not_bypass_exclusive_inputs(
    requests, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(REQUEST))
    assert main(["--json", "null", "--input", "-"]) == 2
    assert "mutually exclusive" in capsys.readouterr().err
    assert requests == []


@pytest.mark.parametrize("filename", ["null", "None"])
def test_literal_null_filenames_are_preserved(
    requests, monkeypatch, tmp_path: Path, filename
) -> None:
    monkeypatch.chdir(tmp_path)
    Path(filename).write_text(REQUEST, encoding="utf-8")
    assert main(["-i", filename, "-o", filename]) == 0
    assert (
        json.loads(Path(filename).read_text(encoding="utf-8"))["output"]
        == "FIRST, OTHER"
    )
