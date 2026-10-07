# Video walkthrough outline

Record in English with screen sharing and the camera on. Aim for 10–13 minutes,
leaving room below the 15-minute limit. Explain the code in your own words and
only claim decisions and checks you understand.

| Time | Open | Explain |
| --- | --- | --- |
| 0:00–1:00 | README | Task, assumptions, SQLite choice, exact string cache keys, output identity |
| 1:00–2:00 | schemas.py and generation.py | Strict input, equal lengths, Unicode, uppercase simulation, ordering |
| 2:00–3:00 | api.py and settings.py | Factory, lifespan, sync routes, status codes, error handling |
| 3:00–6:00 | service.py and database.py | Follow one POST: transaction, cache lookup, misses, interleave, ID, commit |
| 6:00–7:00 | storage.py and GET route | Atomic file write, DB authority, read and recovery |
| 7:00–9:00 | cli.py | Pydantic Settings parsing, inputs, POST/GET, repeats, JSON Lines, exit codes |
| 9:00–11:00 | tests/ | Call-count test, restart, rollback, simultaneous processes, HTTP and CLI failures |
| 11:00–12:00 | Dockerfile, compose.yaml, CI | Locked dependencies, non-root runtime, persistent volume, checks |
| 12:00–13:00 | README tradeoffs | Write serialization, crashes, version invalidation, future migrations |

Use this request to follow the implementation end to end:

```json
{"list_1":["first","first"],"list_2":["other","first"]}
```

Explain why only two unique original strings need transformation and why a repeat
needs none. Explain why input `FIRST` is a different transformation-cache key,
but may still produce an existing payload ID.

Optional live commands:

```sh
docker compose up --build --wait
uv run --no-sync cache-cli --input examples/input.json --repeat 3
uv run --no-sync pytest --cov=cache_service --cov-report=term-missing
```

Practice answering these without reading a script:

1. Why does a unique database key alone not prevent duplicate transformer calls?
2. Why does the SQLite lock start before reading cached results?
3. What happens if generation succeeds but the database commit fails?
4. Why does GET rebuild a missing file instead of transforming the inputs again?
5. What changes if the transformer becomes a slow remote service?
6. How would you change the separator or transformer and handle existing data?

The recording must be made by the candidate. This document is preparation,
not a video or a claim that the candidate has already reviewed the implementation.
