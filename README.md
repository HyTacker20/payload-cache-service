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

## Implementation sequence

1. Define the contract and package configuration.
2. Add validated input and payload generation with unit tests.
3. Add durable caching and JSON storage with integration tests.
4. Expose the FastAPI endpoints and verify HTTP behavior.
5. Add the CLI and test its inputs, outputs, and failures.
6. Add Docker deployment, CI, and complete the usage documentation.

Tests accompany each behavior. Commit history records completed, reviewed changes.
