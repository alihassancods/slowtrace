# Fix API — `backend/src/api/fix.py`

## Overview

[`backend/src/api/fix.py`](../../backend/src/api/fix.py) analyses a single slow query and returns a ranked list of actionable fix recommendations. It reuses the connection, stats-fetch, and EXPLAIN steps from the Explain module and adds a rule-based analysis engine on top.

**Base prefix:** `/api/fix`

---

## Route

### `GET /api/fix/{connection_id}/{queryid}`

Returns a `FixReport` containing ranked recommendations for the query identified by `queryid`.

**Path parameters:**

| Parameter | Description |
|-----------|-------------|
| `connection_id` | URL-encoded PostgreSQL DSN |
| `queryid` | Query ID string from `pg_stat_statements` |

**Response:** `application/json` — a `FixReport` object.

---

## Pydantic models

### `FixError`

```python
class FixError(BaseModel):
    step: str
    message: str
```

### `FixRecommendation`

```python
class FixRecommendation(BaseModel):
    id: str          # stable identifier for the rule, e.g. "seq_scan_large_table"
    title: str       # short human-readable title
    severity: str    # "high" | "medium" | "low"
    explanation: str # paragraph explaining the problem and proposed fix
    sql: str         # ready-to-run SQL snippet (may contain placeholder comments)
```

### `FixReport`

```python
class FixReport(BaseModel):
    connection_id: str
    queryid: str
    query_fingerprint: str | None
    score: float | None
    mean_exec_time_ms: float | None
    total_exec_time_ms: float | None
    cache_hit_ratio: float | None
    recommendations: list[FixRecommendation]
    errors: list[FixError]
```

`recommendations` is sorted **ascending by severity** (high → medium → low).

---

## Step sequence

| Step | Description |
|------|-------------|
| 1 `connect` | Opens a connection (`_step_connect` from Queries module) |
| 2 `fetch_query` | Fetches query stats from `pg_stat_statements` (`_step_fetch_query` from Explain module) |
| 3 `explain` | Runs `EXPLAIN (ANALYZE false, BUFFERS, FORMAT JSON)` (`_step_explain` from Explain module) |

If `EXPLAIN` fails, `plan` is set to `None` and the rules that depend on it are skipped, but the report is still returned.

---

## Analysis engine — `_analyse(row, plan)`

Runs all recommendation rules in sequence and returns the non-`None` results sorted by severity order (`high=0, medium=1, low=2`).

---

## Recommendation rules

### Rule 1 — `_rule_seq_scan` · `seq_scan_large_table` · **high**

**Trigger:** EXPLAIN plan contains a `Seq Scan` node with `Plan Rows >= 1000`.

Recommends creating a `CONCURRENTLY` index on the filtered column(s). The SQL snippet includes the actual table name when available from `Relation Name` in the plan node.

### Rule 2 — `_rule_low_cache` · `low_cache_hit` · **high** / **medium**

**Trigger:** `cache_hit_ratio < 95 %`

- `< 80 %` → severity `"high"`
- `80–94 %` → severity `"medium"`

Recommends increasing `shared_buffers` and running `VACUUM ANALYZE`.

### Rule 3 — `_rule_high_mean` · `high_mean_exec_time` · **high** / **medium**

**Trigger:** `mean_exec_time_ms >= 500`

- `>= 2000 ms` → severity `"high"`
- `500–1999 ms` → severity `"medium"`

Recommends reviewing the EXPLAIN plan for expensive nodes and tuning `work_mem` to prevent sort/hash spills to disk.

### Rule 4 — `_rule_high_stddev` · `high_stddev` · **medium**

**Trigger:** `mean_exec_time_ms >= 100` **and** `stddev_ms / mean_ms >= 0.5` (coefficient of variation ≥ 50 %).

Indicates lock contention, autovacuum interference, or plan instability. Recommends checking `pg_stat_activity` and resetting `pg_stat_statements`.

### Rule 5 — `_rule_missing_vacuum` · `missing_vacuum` · **low**

**Trigger:** Plan contains a `Seq Scan` **and** `shared_blks_read > shared_blks_hit`.

High physical I/O relative to cache hits on a sequential scan suggests table bloat or stale statistics. Recommends `VACUUM ANALYZE` on the scanned table.

---

## Plan traversal — `_walk_plan(plan)`

Performs a depth-first traversal of the EXPLAIN JSON tree and returns a flat list of all plan nodes. Used by rules that need to inspect every node type in the plan.

```python
def _walk_plan(plan: list[dict]) -> list[dict]:
    ...
```

---

## Shared utilities (imported)

From [`api.queries`](api-queries.md):
- `_fingerprint` — query normalisation
- `_score_query` — badness score
- `_step_connect` — connection helper

From [`api.explain`](api-explain.md):
- `_step_fetch_query` — single-query stats fetch
- `_step_explain` — EXPLAIN execution
