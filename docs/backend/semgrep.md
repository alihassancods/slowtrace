# Semgrep — environment setup and JSON output contract

SlowTrace's codebase scanner runs [Semgrep](https://semgrep.dev) as a subprocess
over a cloned repository and parses its `--json` output. This page records the
**verified** output shape that the parser is built against.

Verified on 2026-09-27 with **Semgrep 1.178.0**, **Python 3.14.5**, **git 2.54.0**,
Linux x86-64.

---

## Installation

`semgrep>=1.70.0` is declared in [`backend/requirements.txt`](../../backend/requirements.txt).

The backend environment is a `uv`-managed virtualenv, and this host has **no pip**
(`python3 -m pip` → `No module named pip`), so install with `uv`:

```bash
cd backend
uv pip install --python .venv/bin/python "semgrep>=1.70.0"
```

The CLI is then `.venv/bin/semgrep` — **not** on the ambient `PATH`. Anything that
shells out must resolve the binary inside the venv (e.g. `Path(sys.executable).parent / "semgrep"`)
rather than calling bare `semgrep`, which is how `sys.executable`-relative resolution
works for `uvicorn`/`pytest` in this project.

```console
$ backend/.venv/bin/semgrep --version
1.178.0
```

### Host requirements

| Requirement | Status on dev host | Notes |
|---|---|---|
| `git` | ✅ 2.54.0 | Needed to clone target repositories. **Requires git to be installed on the host** — add it to any future backend image (`apt-get install git`). `docker-compose.yml` only defines Postgres; the backend runs on the host. |
| `semgrep-core` binary | ✅ bundled | Ships inside the wheel at `semgrep/bin/semgrep-core` (~100 MB); no separate download, no network needed at scan time. |
| Semgrep account | ❌ not required | Except for two redacted fields — see [Login-gated fields](#login-gated-fields-lines--fingerprint--metavars). |

---

## Invoking it

Both the inline-pattern and rule-file forms were verified:

```bash
# inline pattern (single-quoted! "$X" expands to nothing inside double quotes)
semgrep --pattern 'for $X in $Y: ...' --lang python --json --metrics=off -q <path>

# custom YAML rules — a directory of *.yml works too, for the real ruleset
semgrep --config rules/n_plus_one.yml --json --metrics=off -q <path>
```

Always pass `--json --metrics=off -q`. Scanning a directory recurses and honours
`.semgrepignore` (and `.gitignore` inside a git repo).

### Exit codes

| Exit | Meaning | Parser behaviour |
|---|---|---|
| `0` | Ran to completion — **whether or not findings exist** | Do **not** treat exit code as "found issues"; read `results[]`. |
| `7` | Invalid/missing config file | Surface as a scan error; `results[]` is empty. |
| `2` | Error entry inside `errors[]` (e.g. config path not found) | Informational, in the JSON. |

A file that fails to parse (e.g. `def broken(:`) is listed under `paths.scanned`
and produces **no** error entry and **no** results — Semgrep's Python parser is
error-tolerant, so a malformed file silently yields nothing rather than aborting the scan.

---

## Full JSON output

Rule under test (`test_rule.yml`), against `test_app.py` whose lines 5–7 contain
the N+1 pattern:

```yaml
rules:
  - id: test-n-plus-one
    patterns:
      - pattern: |
          for $X in $Y:
              $Z = $X.$REL.all()
    message: "N+1 pattern found"
    languages: [python]
    severity: ERROR
```

```json
{
  "version": "1.178.0",
  "results": [
    {
      "check_id": "test-n-plus-one",
      "path": "test_app.py",
      "start": { "line": 6, "col": 5, "offset": 127 },
      "end":   { "line": 7, "col": 34, "offset": 181 },
      "extra": {
        "message": "N+1 pattern found",
        "metadata": {},
        "severity": "ERROR",
        "fingerprint": "requires login",
        "lines": "requires login",
        "validation_state": "NO_VALIDATOR",
        "engine_kind": "OSS"
      }
    }
  ],
  "errors": [],
  "paths": { "scanned": ["test_app.py"] },
  "time": {
    "rules": [],
    "rules_parse_time": 0.00022602081298828125,
    "profiling_times": {
      "config_time": 0.08505916595458984,
      "core_time": 0.10741066932678223,
      "ignores_time": 8.273124694824219e-05,
      "total_time": 0.1966404914855957
    },
    "max_memory_bytes": 89932224
  },
  "engine_requested": "OSS",
  "skipped_rules": [],
  "profiling_results": []
}
```

`time` is abbreviated above; it also carries `parsing_time`, `scanning_time`,
`matching_time`, `tainting_time`, `prefiltering` sub-objects of pure profiling data.
Ignore them.

---

## Fields the parser reads

| Field | Type | Notes |
|---|---|---|
| `results[].check_id` | `str` | The rule's `id`, verbatim. With a registry config it is namespaced (`rules.n-plus-one`). This is the join key onto our rule catalogue. |
| `results[].path` | `str` | Relative to the **scan root** (`dirtest/deep.py` when scanning `dirtest`). Join onto the clone dir before reading. |
| `results[].start.line` | `int` | 1-indexed, inclusive. |
| `results[].start.col` | `int` | 1-indexed. |
| `results[].start.offset` | `int` | Absolute byte offset — usually unnecessary. |
| `results[].end.line` | `int` | 1-indexed, inclusive. A single-line match has `end.line == start.line`. |
| `results[].extra.message` | `str` | The rule's `message`. |
| `results[].extra.severity` | `str` | Exactly `"ERROR" \| "WARNING" \| "INFO"`. Map to our `critical \| warning \| info` tiers per rule, not implicitly. |
| `results[].extra.lines` | `str` | Matched source, `\n`-joined, no trailing newline. **Login-gated** — see below. |
| `results[].extra.metadata` | `object` | Our custom per-rule metadata, passed through **verbatim** — always present, never gated. |
| `results[].extra.validation_state` | `str` | Always `"NO_VALIDATOR"` for OSS runs. |
| `results[].extra.engine_kind` | `str` | `"OSS"` (pro engine needs a login + separate binary). |
| `errors[]` | `array` | `{code, level, type, message}`. Non-empty does not mean "no results". |
| `paths.scanned` | `array<str>` | Every file considered, including those with zero findings — useful for `files_scanned`. |

### Custom metadata passthrough (verified)

```yaml
metadata:
  category: performance
  technology: ["django", "orm"]
  smell: "n-plus-one"
  confidence: HIGH
  cwe: "CWE-N+1"
```

arrives as `results[0].extra.metadata`, byte-for-byte, **without** any login. Nested
JSON is fine. This is where each rule carries its own `name` / `severity` rationale /
`fix` text, so a finding is self-describing.

---

## Login-gated fields: `lines`, `fingerprint`, `metavars`

Semgrep 1.178.0 redacts three `extra` fields unless it believes the user is logged in:

- `extra.lines` → the literal string `"requires login"`
- `extra.fingerprint` → the literal string `"requires login"`
- `extra.metavars` → **key absent entirely** (bindings like `$X = "order"`)
- `extra.is_ignored` → key absent

The gate is simply the presence of a `SEMGREP_APP_TOKEN` (a dummy 64-hex-char value
un-redacts them; no network validation occurs). `metadata`, `message`, `severity`,
`check_id` and the positions are **never** gated.

**SlowTrace does not depend on this.** The scan runs against a local clone, so the
matched code is recovered from `path` + `start.line`/`end.line`:

```python
src = (clone_dir / result["path"]).read_text().splitlines()
code = "\n".join(src[result["start"]["line"] - 1 : result["end"]["line"]])
```

Verified — returns `'    for order in orders:\n        items = order.items.all()  # N+1 here'`
for the fixture above, where `extra.lines` returned `"requires login"`.

Two consequences worth remembering:

1. **Never render `extra.lines` directly** — for unauthenticated runs it prints
   "requires login" into the UI. Treat the literal as "unavailable" and fall back
   to reading the file.
2. If a rule ever needs **metavariable bindings**, either require a `SEMGREP_APP_TOKEN`
   or express them as separate narrower patterns. `--max-lines-per-finding` (default
   10) also truncates long matched snippets, so multi-line snippets should be read
   from the file with an explicit window rather than trusted from `lines`.

---

## The SlowTrace ruleset

[`backend/src/agent/codebase/rules/slowtrace-rules.yml`](../../backend/src/agent/codebase/rules/slowtrace-rules.yml)
holds all 12 rules in one file — no Python regex. Validate after any edit:

```bash
backend/.venv/bin/semgrep \
  --config backend/src/agent/codebase/rules/slowtrace-rules.yml --validate
# → Configuration is valid - found 0 configuration error(s), and 12 rule(s).
```

### `check_id` is namespaced — the parser must strip the prefix

Passing `--config <path>` makes Semgrep prefix every rule id with the **config path,
dot-separated**, and the prefix changes with how the path was written:

| Invocation | `check_id` |
|---|---|
| `--config probe_rules.yml` (no directory part) | `django-n-plus-one-loop` |
| `--config src/agent/.../slowtrace-rules.yml` | `src.agent.codebase.rules.django-n-plus-one-loop` |
| `--config /home/.../slowtrace-rules.yml` | `home.ali.Projects...rules.django-n-plus-one-loop` |

Rule ids never contain a dot, so group on:

```python
rule_id = result["check_id"].rsplit(".", 1)[-1]
```

(In `--test` mode Semgrep sets `no_rewrite_rule_ids=True`, so ids come back bare —
don't let that mislead tests into assuming the production form is bare.)

### Syntax traps that cost real debugging time

Every one of these either fails to load or fails to match **silently**:

| Trap | Symptom | Fix |
|---|---|---|
| Inline ellipsis: `... $EXPR ...` | `Rule parse error: Invalid pattern for Python` | Put `...` on its own line. |
| Ellipsis omitted entirely | Matches only when the target is the *sole* statement in the block | Always write own-line `...` before (and after) the target. |
| Trailing member access: `$ITEM.$FK.` | Parse error | Write the full chain `$ITEM.$FK.$FIELD`. |
| `if cond:` with no body | Loads fine, **matches nothing** | Add an ellipsis body. |
| Backtick pattern `` $DB.query(`SELECT ... ${$X}`) `` | Loads fine, **matches nothing** | Use `metavariable-regex` on the argument's raw text. |
| Empty object pattern `findMany({})` | Matches **every** object arg (patterns are subset-matches), so `findMany({ take: 25 })` is flagged | Add `pattern-not: "...findMany({ take: $LIMIT, ... })"`. |
| C-style loop `for ($INIT; $C; $U)` | Misses `let`/`var` declarations in init | Use `for (...) { ... }`. |
| Pattern containing `": "` | `mapping values are not allowed here` (YAML error) | Quote it, or use a `\|` block scalar. |

### Precision notes

- **Own-line `...` is what prevents false negatives** — `if`-wrapped or
  assignment-wrapped queries still match, because Semgrep matches sub-expressions
  through statements.
- `python-raw-sql-fstring` requires actual interpolation
  (`regex: "^f['\"].*\\{.*\\}"`). Matching on `f"..."` alone also flags
  `f"SELECT 1"`, which is harmless SQL.
- Rules 1, 2 and 12 intentionally overlap: `order.items.all()` inside a loop
  satisfies both `n_plus_one` patterns. Deduplicate by `(file, line, smell_id)`
  upstream, or the same line is reported several times.
- Semgrep has no dataflow analysis, so `django-n-plus-one-loop` **cannot** know a
  `prefetch_related()` was applied earlier in the function — correctly optimised
  code still matches. Do not treat this rule's ERROR as proof of a bug.
- Comprehensions (`[o.name for o in qs]`) are *not* matched by the `for` patterns.
- `django-count-instead-of-exists`'s `if len($Q) > 0:` branch matches any
  `len()`-on-anything check, not just querysets — the noisiest rule here.

### Rule test harness (`semgrep --test`)

Fixtures live in [`backend/tests/semgrep_test_cases/`](../../backend/tests/semgrep_test_cases):
`test_django_smells.py` (8 rules) and `test_js_smells.js` (4 rules). They are
scanned, never imported — pytest collects **zero** items from that directory, so
[`tests/test_semgrep_rules.py`](../../backend/tests/test_semgrep_rules.py) is the
module that actually runs the harness. Do the same for every new rule.

```bash
# canonical: run it all through pytest
cd backend && .venv/bin/python -m pytest tests/test_semgrep_rules.py

# or per fixture file, directly
backend/.venv/bin/semgrep --test --metrics=off \
  --config backend/src/agent/codebase/rules/slowtrace-rules.yml \
  backend/tests/semgrep_test_cases/test_django_smells.py
# → 8/8: ✓ All tests passed      (JS fixture → 4/4: ✓ All tests passed)
```

Two ways to get a **false green**, both verified:

| Mistake | What happens |
|---|---|
| `--test --config <file> <directory>` | Crashes: `tuple index out of range` (`config.relative_to(config)` is empty). |
| `--test --config <directory> <directory>` | Prints `No unit tests found.` and **exits 0** while checking nothing, because Semgrep only pairs `x.yml` with a test file whose stem is also `x` (our fixtures are deliberately named per-language, not `slowtrace-rules.py`). |

So: pass the rules file with `--config` and name **one fixture file** as the
target. `test_semgrep_rules.py` additionally asserts a non-zero rule count and
that every rule id appears in an annotation, which is what turns either mistake
into a failing test.

Annotation rules that bind:

```python
    # ruleid: django-select-star, django-unbounded-queryset
    return User.objects.all()      # both rules fire on this one line
    # ruleid: django-n-plus-one-loop, django-n-plus-one-fk-access
    for order in orders:           # loop/if rules report the HEADER line
        items = order.items.all()
    # ok: django-count-instead-of-exists
    if User.objects.filter(email=email).exists():
```

* The annotation applies to the **immediately following line** — put it above the
  `for`/`if` header or the matching statement, never above the `def`.
* List **every** rule that fires on a line; an unlisted extra match is reported as
  an "incorrect line" and fails.
* The parser scans prose too: writing a literal `# ruleid: <something>` inside a
  docstring makes Semgrep complain about a rule id that does not exist.
* `prefetch_related()` does **not** suppress `django-n-plus-one-loop` (no dataflow
  analysis), so prefetched-then-still-`.all()` code cannot be an `# ok:` case.

---

## Deployment edge cases

All of the following are covered by `tests/test_semgrep_scanner.py` and
`tests/test_codebase_api.py` (offline — no clone, no network).

### Semgrep is missing

`SemgrepScanner` resolves the CLI in this order: an explicit `semgrep_binary`,
then `<sys.executable>/../bin/semgrep` (the venv copy — the normal case here),
then `$PATH`. It records the outcome in `scanner.available` and
`scanner.semgrep_path`, and **never raises**: `scan()` returns

```json
{"success": false, "error": "semgrep_not_installed",
 "message": "Semgrep is not installed on the server.",
 "install_command": "uv pip install --python .venv/bin/python \"semgrep>=1.70.0\"",
 "detail": "semgrep CLI not found on this host"}
```

Do **not** replace this with `subprocess.run(["which", "semgrep"])`: the CLI is a
venv dependency and is absent from `$PATH`, so a PATH probe reports a correctly
installed server as broken. Likewise the copy must not say `pip install semgrep`
— this project's environment has no pip. The API forwards the payload unchanged
as an SSE `error` stage, and `CodebasePage` renders `install_command` in a sky-blue
**setup card** (`SetupInstruction`) instead of the red failure card, because the
operator has nothing to retry.

### Repository is too large to scan quickly

`GET https://api.github.com/repos/{owner}/{repo}` is consulted **before** the
clone; `size` is in KB, and `>= 50_000` KB yields

> Repository is large (479.8mb). Scan may take 2-3 minutes. Consider scanning a
> subdirectory instead.

The API emits it as the second SSE event (`progress` with `warning: true`, before
any cloning chatter) so the wait is explained up front, and threads the same
string into `scan(size_warning=...)` so it persists in the cached result and the
results header. It is advisory: the scan proceeds. Any failure — offline,
rate-limited, or a private repo the token cannot read — returns `None` and the
scan is never blocked by it.

### `.semgrepignore`

`semgrepignore_template` is copied to `<clone>/.semgrepignore` immediately after
the clone, so it sits at the scan root where Semgrep honours it. Measured effect:
Semgrep **already** excludes `node_modules/`, `vendor/`, `dist/`, `build/`,
`__pycache__/` and `migrations/*` by default, so what the template genuinely adds
is `fixtures/` (plus `.venv/`, `venv/`, `*.bundle.js`). It is kept because it
documents intent and defends against Semgrep changing its defaults; the scanner's
own `--exclude` flags overlap it deliberately.

### Other guards worth knowing

- Paths are made repo-relative with `Path.relative_to(clone_root)`; the clone root
  **is** the repository (`git clone <url> <tmpdir>`), so there is no repo-name
  directory to strip. An earlier suggestion to drop the first path component would
  turn `aggregator/tests.py` into `tests.py` and 404 every GitHub link —
  `test_relative_path_keeps_every_directory_level` locks this down.
- Zero findings is a success state: the UI shows "✅ Clean codebase!" with the
  scanned file count. The one exception is `files_scanned == 0`, which says the
  repository may be empty or entirely ignored rather than celebrating it.
- Duration is surfaced as "Scanned in 23.4 seconds using Semgrep"
  (`scanDurationLabel`), minutes past 60s, `null` when unknown.

---

## Verification log

| Step | Command | Result |
|---|---|---|
| Install | `uv pip install --python .venv/bin/python "semgrep>=1.70.0"` | ✅ `semgrep==1.178.0` + 39 deps |
| Version | `semgrep --version` | ✅ `1.178.0` (≥ 1.70.0) |
| git | `git --version` | ✅ `git version 2.54.0` |
| Inline pattern | `--pattern 'for $X in $Y: ...' --lang python --json` | ✅ 1 result, `errors: []` |
| Custom YAML rule | `--config test_rule.yml --json` | ✅ 1 result at `test_app.py:6-7` |
| JSON structure | parsed with `json.load` | ✅ documented above |
| Directory scan | recursive, incl. a syntactically broken file | ✅ 2 results, exit 0, no errors |
| Metadata passthrough | rule with `metadata:` block | ✅ verbatim in `extra.metadata` |
| Exit codes | findings / no findings / bad config | ✅ `0` / `0` / `7` |
| Ruleset loads | `--config slowtrace-rules.yml --validate` | ✅ `0 errors, 12 rules` |
| Rules fire on bad code | scan of `bad.py` / `bad.ts` fixtures | ✅ 12/12 rules, 21 findings, 0 errors |
| Rules silent on good code | scan of `good.py` / `good.ts` fixtures | ✅ 0 findings |
| `--test` python fixture | 8 annotated rules | ✅ `8/8: ✓ All tests passed` |
| `--test` js fixture | 4 annotated rules | ✅ `4/4: ✓ All tests passed` |
| Coverage guard bites | added a 13th untested rule | ✅ `test_every_rule_is_covered_by_an_annotation` failed, then restored |
| Live scan, normal repo | `django/djangoproject.com` via POST /scan | ✅ 671 files, 33 findings, 23.4s, `size_warning: null` |
| Live scan, 480 MB repo | `matplotlib/matplotlib` | ✅ warning as SSE event #2, then complete: 4595 files, 134 findings, 49.2s |
| Large-repo cache | `GET /results?repo_url=…matplotlib` | ✅ `size_warning` still present |
| Disconnect mid-scan | client aborted at 4s | ✅ 0 orphaned git/semgrep, 0 leftover temp dirs |
| Ignore template | real binary over app/ + fixtures/ + node_modules/ | ✅ before `{app, fixtures}` → after `{app}` |
| API contract, offline | `tests/test_codebase_api.py` (ASGITransport) | ✅ 9 tests: envelope, 400, 422, cache, setup error, size warning, correlation |
| Scanner edge cases, offline | `tests/test_semgrep_scanner.py` | ✅ 20 tests |

The permanent rule fixtures are checked in under `backend/tests/semgrep_test_cases/`.
The one-off environment probes from the setup session (`test_app.py`,
`test_rule.yml`, `verify_semgrep.py`) were throwaway and live only in a scratch dir.
