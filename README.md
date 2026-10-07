# Cache Service

A FastAPI service that transforms two equally sized lists of strings, interleaves
the results, and persists both transformation results and generated JSON payloads.

## Contract and assumptions

- `POST /payload` accepts exactly `list_1` and `list_2`, both arrays of strings
  with equal lengths. Numbers, nulls, and unknown fields are rejected.
- The simulated external transformer is Python's Unicode-aware `str.upper()`.
- Order, whitespace, commas, and empty strings are preserved. Empty lists produce
  an empty output string. The separator is exactly `", "`.
- Transformation cache keys use the exact original string and a transformer
  version. No trimming or case folding occurs before looking up a string.
- Payload identity is the exact final output string. Identical outputs reuse an
  ID even when their original inputs differ. IDs are SHA-256 of UTF-8 output.
- New payloads return HTTP 201; reused payloads return HTTP 200. Both responses
  include `id`, `created`, and a confirmation `message`, plus a `Location` header.
- `GET /payload/{id}` returns `{"output": "..."}`. Unknown IDs return 404;
  malformed IDs and invalid input return 422.
- Generated payload files are stored as UTF-8 JSON in a persistent data directory.
  SQLite is the source of truth; missing or damaged files can be rebuilt without
  calling the transformer.
- A SQLite write transaction serializes cache lookup, generation, and insertion.
  This prevents simultaneous cache misses from duplicating successful work in
  processes sharing the same local SQLite database. It limits write throughput.
- No TTL is needed for this deterministic transformer. Change its cache version
  when changing its behavior. Failed requests may be retried; external side
  effects cannot be made exactly-once across a crash by this local cache.
- CLI parameters are parsed and validated through Pydantic Settings. `--input`
  and `--json` are mutually exclusive; absent input defaults to stdin. Each repeat
  performs POST then GET and emits one JSON object per line (JSON Lines).
- The task assigns `-h` twice. Here `-h/--help` means help, and `-H/--host` selects
  the server. The other short flags are `-r`, `-i`, `-j`, and `-o`.

## Run with Docker

Requires Docker with Compose. From the repository root:

```sh
docker compose up --build --wait
```

The API is at `http://localhost:8000`; interactive API documentation is at
`http://localhost:8000/docs`. The container runs as an unprivileged user.
A healthcheck waits for the application to serve its OpenAPI document.

```sh
docker compose down
```

Stopping the service preserves the named volume, including the SQLite database
and generated files. Running `docker compose down --volumes` deletes that data.

## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
The checked-in lock file pins runtime and development dependencies.

```sh
uv sync --locked --python 3.12
uv run --no-sync uvicorn cache_service.api:create_app --factory --host 127.0.0.1 --port 8000
```

Local data defaults to `./data`. Set `CACHE_DATA_DIR` to change it:

```sh
# Linux/macOS
export CACHE_DATA_DIR=/path/to/data
```

```powershell
# PowerShell
$env:CACHE_DATA_DIR = 'C:\path\to\data'
```

## API example

```sh
curl -i -X POST http://localhost:8000/payload \
  -H 'Content-Type: application/json' --data-binary @examples/input.json
```

First response: HTTP 201 with this body and a `Location` header:

```json
{
  "id": "c41c0f742843161d0649a7dba58752d11e30e35e9fa016e7ccdfb3f45f96682f",
  "created": true,
  "message": "Payload created"
}
```

```sh
curl http://localhost:8000/payload/c41c0f742843161d0649a7dba58752d11e30e35e9fa016e7ccdfb3f45f96682f
```

```json
{
  "output": "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"
}
```

Repeating the POST returns HTTP 200 with the same ID, `created: false`, and
`message: "Payload already exists"`. GET never calls the transformer.

| Status | Meaning |
| --- | --- |
| 201 | A new output was stored |
| 200 | Output read, or an existing output reused |
| 404 | A well-formed ID is unknown |
| 422 | Invalid JSON, input schema, or ID format |
| 502 | Transformer failed; request can be retried |
| 503 | SQLite or file storage is unavailable, including a lock timeout |

## CLI

Install locally with `uv sync --locked --python 3.12`, then use:

```text
cache-cli [-H|--host URL] [-r|--repeat N]
          [-i|--input FILE|-] [-j|--json JSON]
          [-o|--output FILE|-] [-h|--help]
```

```sh
uv run --no-sync cache-cli --host http://localhost:8000 --input examples/input.json --repeat 3
uv run --no-sync cache-cli --json '{"list_1":["first"],"list_2":["other"]}'
uv run --no-sync cache-cli --input examples/input.json --output results.jsonl
cat examples/input.json | uv run --no-sync cache-cli --input - --output -
```

PowerShell stdin example:

```powershell
Get-Content -Raw examples/input.json | uv run --no-sync cache-cli --input -
```

The same installed CLI is available inside the API container:

```sh
docker compose exec api cache-cli --json '{"list_1":["first"],"list_2":["other"]}' --repeat 3
```

Defaults: `http://127.0.0.1:8000`, one iteration, stdin input, stdout output.
Files and redirected stdin accept UTF-8 with or without a BOM, independently of
the operating system's locale. Input is fully validated before contacting the
server. Literal `--json null` is rejected; filenames named `null` or `None` are
preserved. HTTP(S) base URLs may include a path prefix, but not
credentials, a query, or a fragment. Network operations have a 30-second timeout.

CLI defaults can also be configured with `CACHE_CLI_HOST`, `CACHE_CLI_REPEAT`,
`CACHE_CLI_INPUT`, `CACHE_CLI_JSON`, and `CACHE_CLI_OUTPUT`. Command-line arguments
take precedence. Environment variable names are case-insensitive; unrelated
variables named `input`, `json`, or `output` do not configure this application.
CLI flags remain case-sensitive so `-H` and `-h` retain distinct meanings.

Each iteration performs POST followed by GET. Output is JSON Lines, suitable for
programmatic consumption:

```json
{"id":"...","created":true,"output":"FIRST, OTHER"}
{"id":"...","created":false,"output":"FIRST, OTHER"}
```

The CLI checks that the ID and output stay consistent across repeats. Diagnostics
go to stderr. Exit codes are 0 for success, 2 for arguments/input errors, and 1
for server, network, response, or output failures. On a later failure, output can
contain the successful earlier iterations; check the exit code.

## How a request works

1. FastAPI validates `PayloadInput` before starting any database or transformer work.
2. `PayloadService.create` reserves the SQLite writer using `BEGIN IMMEDIATE`.
3. It collects unique original strings and reads cached transformations in batches.
4. Only missing strings invoke the transformer. Their results are inserted into
   the same database transaction.
5. Transformed values are put back in their original positions and interleaved.
6. The service hashes the final output, looks up its payload row, and reuses or
   inserts it. A hash collision with different content is rejected.
7. The JSON file is written through a temporary file and atomic replacement.
8. The transaction commits before the endpoint returns the ID.

GET looks up the committed payload row and checks its corresponding JSON file.
Missing or damaged files are rebuilt from the stored output without transformation.

The database has two tables:

| Table | Key | Stored value |
| --- | --- | --- |
| `transformations` | `(version, original)` | Transformed string |
| `payloads` | SHA-256 output ID | Complete output string |

Source files are deliberately small: `schemas.py` defines the shared API/CLI
contract; `generation.py` contains the transformer and interleaving;
`database.py` defines ORM models and engine setup; `storage.py` writes files;
`service.py` coordinates caching; `api.py` handles HTTP; `cli.py` exercises it.

## Consistency and tradeoffs

- SQLite makes this small service easy to deploy. `BEGIN IMMEDIATE` prevents
  duplicate cache-miss work across processes sharing this local database. All POST
  transactions serialize, including transformer and file operations. The lock
  timeout is 30 seconds. A slow remote transformer would need a different
  coordination strategy for greater throughput.
- The supplied container uses one Uvicorn worker. This is not a distributed cache
  across hosts or independent database files.
- SQLite transactions and file replacement are not a shared transaction. A crash
  after file replacement but before DB commit can leave an orphan file. It cannot
  be served without a committed row; a later POST can reuse the file safely.
  Committed database output can regenerate missing files after storage failures.
- On Windows, concurrent readers can temporarily block file replacement. Repairs
  recheck whether another caller already wrote the same content, or retry a
  sharing violation up to ten times with 10 ms between attempts. Persistent
  storage failures still return 503.
- Transformer or file failures roll back newly inserted rows. Retrying a failed
  request may repeat computations from its rolled-back transaction. Existing
  committed cached results remain available.
- Identity follows the returned output string, including delimiters. Different
  element boundaries that produce the same string intentionally share an ID.
- Validation responses include error type, location, and message without
  reflecting rejected values. Database error logging hides bound SQL parameters.
- Schema creation uses SQLAlchemy `create_all` for this initial schema. Future
  schema changes would need migrations. No cache expiry or background cleanup is
  implemented; data grows with distinct strings and outputs.

## Tests and checks

```sh
uv run --no-sync pytest --cov=cache_service --cov-report=term-missing
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
```

Tests use real temporary SQLite databases and files. Only the simulated external
transformer and remote HTTP responses are replaced where necessary.

- Generation tests cover order, Unicode, whitespace, empty input, and validation.
- Service tests count transformer calls, check partial/repeated cache hits,
  output deduplication, version changes, rollback, file recovery, and database reopen.
- Concurrency tests coordinate simultaneous callers with separate engines and
  three separate Python processes; successful shared inputs transform once.
- HTTP integration tests exercise the application lifespan, endpoints, status
  codes, restart persistence, and safe error serialization.
- CLI tests exercise parsing, stdin/files, output, repeats, help, and failures.
- Regression tests cover binary/non-finite error inputs, parameter privacy,
  concurrent Windows file recovery, CLI environment isolation, literal arguments,
  and UTF-8 stdin under a non-UTF-8 locale.

CI runs tests, formatting, lint, and strict type checking on Linux and Windows,
and builds the Docker image. For submission preparation, see
[the walkthrough outline](docs/walkthrough.md) and [submission checklist](docs/submission.md).
