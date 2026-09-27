"""Runs `semgrep --test` against the rule fixtures and checks rule coverage.

The fixtures in ``tests/semgrep_test_cases/`` are inert to pytest on purpose
(they contain intentionally bad code and define no tests). This module is what
actually executes them.

Two failure modes this guards against, both verified on Semgrep 1.178.0:

* ``--test --config <file> <directory>`` crashes with
  ``tuple index out of range``, so the harness is invoked per fixture file.
* ``--test --config <directory> <directory>`` prints "No unit tests found" and
  **exits 0** while testing nothing — because Semgrep only pairs a rule file
  ``x.yml`` with a test file whose stem is also ``x``. A bare exit-code check
  would therefore pass on an empty run, hence the explicit counts below.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

BACKEND_DIR = Path(__file__).resolve().parents[1]
RULES_DIR = BACKEND_DIR / "src" / "agent" / "codebase" / "rules"
RULES_FILE = RULES_DIR / "slowtrace-rules.yml"
FIXTURES_DIR = Path(__file__).resolve().parent / "semgrep_test_cases"

FIXTURES = [
    FIXTURES_DIR / "test_django_smells.py",
    FIXTURES_DIR / "test_js_smells.js",
]

# Semgrep prints "<passed>/<total>: ..."; e.g. "12/12: All tests passed".
RESULT_RE = re.compile(r"(\d+)/(\d+)")
# Annotation lines look like: `# ruleid: a, b` / `// ok: c`
ANNOTATION_RE = re.compile(
    r"^\s*(?:#|//)\s*(ruleid|ok)\s*:\s*(?P<ids>[A-Za-z0-9_\-,\s]+)$"
)


def semgrep_binary() -> str | None:
    """The CLI lives in the venv next to the interpreter, not on $PATH."""
    candidate = Path(sys.executable).parent / "semgrep"
    return str(candidate) if candidate.exists() else shutil.which("semgrep")


def rule_ids() -> list[str]:
    config = yaml.safe_load(RULES_FILE.read_text())
    return [rule["id"] for rule in config["rules"]]


def annotated_rule_ids() -> set[str]:
    """Every rule id named by a ruleid/ok annotation in any fixture."""
    named: set[str] = set()
    for fixture in FIXTURES:
        for line in fixture.read_text().splitlines():
            match = ANNOTATION_RE.match(line)
            if not match:
                continue
            tokens = [t.strip() for t in match["ids"].split(",")]
            named.update(t for t in tokens if t)
    return named


@pytest.fixture(scope="module")
def semgrep() -> str:
    binary = semgrep_binary()
    if binary is None:
        pytest.skip("semgrep is not installed (pip install -r requirements.txt)")
    return binary


def test_rules_file_exists_and_loads() -> None:
    assert RULES_FILE.is_file(), f"missing rules file: {RULES_FILE}"
    assert len(rule_ids()) == 12, "expected the 12 documented rules"


def test_fixtures_exist() -> None:
    for fixture in FIXTURES:
        assert fixture.is_file(), f"missing fixture: {fixture}"


def test_every_rule_is_covered_by_an_annotation() -> None:
    """A new rule with no fixture line must fail here, not pass unnoticed."""
    missing = sorted(set(rule_ids()) - annotated_rule_ids())
    assert not missing, f"rules with no test annotation: {missing}"


def test_annotations_only_name_real_rules() -> None:
    unknown = sorted(annotated_rule_ids() - set(rule_ids()))
    assert not unknown, f"annotations naming rules that do not exist: {unknown}"


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_semgrep_test_harness_passes(semgrep: str, fixture: Path) -> None:
    """Semgrep reports per-rule missed/incorrect lines; exit 0 means all matched."""
    args = [
        semgrep,
        "--test",
        "--metrics=off",
        "--config",
        str(RULES_FILE),
        str(fixture),
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
    output = proc.stdout + proc.stderr
    counts = RESULT_RE.findall(output)
    assert counts, f"no '<passed>/<total>' line in Semgrep output:\n{output}"
    passed, total = (int(counts[-1][0]), int(counts[-1][1]))

    # Guard the silent-empty-run failure mode explicitly.
    name = fixture.name
    assert total > 0, f"Semgrep checked no rules for {name}:\n{output}"
    assert proc.returncode == 0, f"{name} failed:\n{output}"
    assert passed == total, f"{name}: only {passed}/{total} checks passed\n{output}"
