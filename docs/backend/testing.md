# Testing — `backend/tests/`

## Overview

The test suite lives under [`backend/tests/`](../../backend/tests/) and covers all five API modules. Tests are written with [pytest](https://docs.pytest.org/) and [pytest-asyncio](https://pytest-asyncio.readthedocs.io/). Async tests use the `asyncio` mode (or per-test `@pytest.mark.asyncio` decoration).

HTTP-level tests use [httpx](https://www.python-httpx.org/) with `ASGITransport` to exercise the full FastAPI application in-process without starting a real server.

---

## Running tests

```bash
# from backend/
pytest                                         # all tests
pytest tests/test_queries.py                   # one file
pytest tests/test_queries.py::TestFingerprint  # one class
pytest tests/test_queries.py::TestFingerprint::test_lowercases  # one test
```

---

## Test files

| File | Module under test |
|------|-------------------|
| [`test_health.py`](../../backend/tests/test_health.py) | `api.health` |
| [`test_queries.py`](../../backend/tests/test_queries.py) | `api.queries` |
| [`test_explain.py`](../../backend/tests/test_explain.py) | `api.explain` |
| [`test_fix.py`](../../backend/tests/test_fix.py) | `api.fix` |
| [`test_dashboard.py`](../../backend/tests/test_dashboard.py) | `api.dashboard` |

---

## Patterns and conventions

### No real database required

All tests mock asyncpg at the step-function level using `unittest.mock.AsyncMock` and `patch`. No Postgres instance is needed.

### Step-function unit tests

Internal step functions (e.g. `_step_check_extension`, `_fingerprint`, `_score_query`) are tested independently with a mock `asyncpg.Connection`. This keeps tests fast and focused on a single behaviour.

### HTTP endpoint tests

The FastAPI `app` is exercised via `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`. `patch` replaces `_step_connect` so no network call is made.

### SSE stream tests

The `_collect_sse(path)` helper streams a full SSE response and returns a list of parsed event dicts. It verifies both the HTTP-level response (status code, content-type) and the business logic (event order, final `result` event).

### Fake asyncpg records

asyncpg `Record` objects are simulated with `MagicMock` subclasses that implement `__getitem__`, `keys()`, `items()`, and `__iter__` — enough to satisfy both direct key access and `dict(row)` conversion used in the production code.

---

## Key test classes

### `test_queries.py`

| Class | What it tests |
|-------|--------------|
| `TestFingerprint` | All normalisation rules of `_fingerprint` |
| `TestScoreQuery` | Individual score signal contributions |
| `TestStepCheckExtension` | Extension installed / missing / DB error |
| `TestStepCheckPermissions` | Read access ok / privilege denied / undefined table |
| `TestStepFetchQueries` | Normal fetch, empty result, timeout, sort order |
| `TestGetQueriesEndpoint` | One-shot JSON endpoint happy/fail paths |
| `TestStreamQueriesEndpoint` | SSE content-type, event order, result structure |

### `test_health.py`

| Class | What it tests |
|-------|--------------|
| `TestGrade` | Grade boundaries (A/B/C/D/F) |
| `TestComputeScore` | Deduction arithmetic — all ok, all fail, warning half-deduction |
| `TestCheckConnections` | Usage thresholds (low / 70 % / 90 %) |
| `TestCheckCacheHitRatio` | Ratio thresholds (high / below 95 / below 80 / no I/O / privilege error) |
| `TestCheckReplicationLag` | No replicas / low lag / warning / fail |
| `TestCheckTableBloat` | No tables / low bloat / Postgres error |
| `TestCheckLockContention` | No locks / one lock / five locks |
| `TestCheckLongTransactions` | None / one / three or more |
| `TestCheckIndexUsage` | No issues / one table / three tables |
| `TestGetHealthEndpoint` | JSON endpoint happy/fail paths, all check names present |
| `TestStreamHealthEndpoint` | Event-stream type, event order, score/grade in result |

---

## Dependencies

```
fastapi
uvicorn[standard]
asyncpg
python-dotenv
httpx
pytest
pytest-asyncio
```

See [`requirements.txt`](../../backend/requirements.txt) and [`requirements-dev.txt`](../../backend/requirements-dev.txt) for pinned versions.
