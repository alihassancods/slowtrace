"""API contract tests for /api/codebase — offline, no git and no network.

The scanner and the pre-clone size check are substituted, so these assert the
HTTP + SSE contract only: envelope shape, stage order, status codes, cache
behaviour, and the two deployment edge cases (Semgrep missing, repo too big).
"""

from __future__ import annotations

import json

import httpx
import pytest

from agent.codebase.semgrep_scanner import _fail
from api import codebase as cb
from api.main import app

BIG_REPO_WARNING = (
    "Repository is large (479.8mb). Scan may take 2-3 minutes. "
    "Consider scanning a subdirectory instead."
)


def clean_scan_result(**overrides) -> dict:
    result = {
        "success": True,
        "repo_url": "https://github.com/acme/shop",
        "scan_duration_seconds": 23.4,
        "files_scanned": 12,
        "total_findings": 0,
        "critical_count": 0,
        "warning_count": 0,
        "info_count": 0,
        "findings": [],
        "grouped_by_type": {},
        "priority_fixes": [],
        "scanner": "semgrep",
        "size_warning": None,
    }
    result.update(overrides)
    return result


class _FakeScanner:
    """Stands in for SemgrepScanner; records how it was called."""

    instances: list["_FakeScanner"] = []

    def __init__(self, result: dict | None = None, available: bool = True) -> None:
        self._result = result or clean_scan_result()
        self.available = available
        self.calls: list[tuple] = []
        _FakeScanner.instances.append(self)

    async def scan(
        self, repo_url, github_token=None, size_warning=None  # noqa: ANN001
    ):
        self.calls.append((repo_url, github_token, size_warning))
        if not self.available:
            # Mirror the real gate, which returns before any clone happens.
            return _fail("semgrep_not_installed", "semgrep CLI not found on this host")
        return self._result


@pytest.fixture(autouse=True)
def _reset_state() -> object:
    _FakeScanner.instances = []
    cb.scan_cache.clear()
    yield
    cb.scan_cache.clear()


@pytest.fixture
def no_size_check(monkeypatch) -> None:
    async def _none(owner, repo, github_token=None):  # noqa: ANN001, ANN202
        return None

    monkeypatch.setattr(cb, "check_repo_size", _none)


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _stages(client: httpx.AsyncClient, payload: dict) -> list[tuple[str, dict]]:
    """POST /scan and return [(stage, data), ...] from the SSE body."""
    events: list[tuple[str, dict]] = []
    async with client.stream("POST", "/api/codebase/scan", json=payload) as response:
        assert response.status_code == 200
        body = ""
        async for chunk in response.aiter_text():
            body += chunk
    for frame in body.split("\n\n"):
        data_line = next(
            (ln for ln in frame.split("\n") if ln.startswith("data:")), None
        )
        if data_line:
            record = json.loads(data_line[len("data:"):])
            events.append((record["stage"], record["data"]))
    return events


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_non_github_url_rejected_before_streaming(client, no_size_check) -> None:
    response = await client.post(
        "/api/codebase/scan", json={"repo_url": "https://gitlab.com/a/b"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_url"
    assert response.json()["message"] == "Please enter a valid GitHub URL"


async def test_missing_repo_url_fails_validation(client) -> None:
    response = await client.post("/api/codebase/scan", json={})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Stage envelope
# ---------------------------------------------------------------------------


async def test_stream_starts_and_completes_with_the_envelope(
    client, no_size_check, monkeypatch
) -> None:
    monkeypatch.setattr(cb, "SemgrepScanner", _FakeScanner)
    events = await _stages(
        client, {"repo_url": "https://github.com/acme/shop", "github_token": "tok"}
    )

    stages = [stage for stage, _ in events]
    assert stages[0] == "started"
    assert stages[-1] == "complete"
    assert "progress" in stages  # keeps the socket alive while scanning

    started = events[0][1]
    assert started["message"] == "Cloning repository..."
    assert started["repo_url"] == "https://github.com/acme/shop"

    complete = events[-1][1]
    assert complete["success"] is True
    assert complete["scanner"] == "semgrep"
    # The token must reach the scanner, and nothing else echoes it back.
    assert _FakeScanner.instances[0].calls[0][1] == "tok"
    assert "tok" not in json.dumps(events)


async def test_result_is_cached_for_the_canonical_url(
    client, no_size_check, monkeypatch
) -> None:
    monkeypatch.setattr(cb, "SemgrepScanner", _FakeScanner)
    await _stages(client, {"repo_url": "https://github.com/acme/shop"})

    # Same repo, different spelling: .git suffix and trailing slash.
    response = await client.get(
        "/api/codebase/results",
        params={"repo_url": "https://github.com/acme/shop.git/"},
    )
    body = response.json()
    assert body["success"] is True
    assert body["data"]["scanner"] == "semgrep"


async def test_unknown_smell_and_unscanned_repo_are_not_errors_500(
    client,
) -> None:
    missing = await client.get(
        "/api/codebase/results", params={"repo_url": "https://github.com/a/b"}
    )
    assert missing.json() == {
        "success": False,
        "error": "No results found. Run a scan first.",
    }

    unknown = await client.get(
        "/api/codebase/smell/n_plus_one",
        params={"repo_url": "https://github.com/a/b"},
    )
    assert unknown.json() == {"success": False, "error": "No results found"}


# ---------------------------------------------------------------------------
# Edge case: Semgrep not installed
# ---------------------------------------------------------------------------


async def test_missing_semgrep_streams_a_setup_error(
    client, no_size_check, monkeypatch
) -> None:
    monkeypatch.setattr(
        cb, "SemgrepScanner", lambda: _FakeScanner(available=False)
    )
    events = await _stages(client, {"repo_url": "https://github.com/acme/shop"})

    stage, data = events[-1]
    assert stage == "error"
    assert data["error"] == "semgrep_not_installed"
    assert data["message"] == "Semgrep is not installed on the server."
    assert "uv pip install" in data["install_command"]
    # Exactly one scan attempt, and the stream never claims completion. The
    # scanner itself bails before cloning (asserted in test_semgrep_scanner).
    assert len(_FakeScanner.instances[0].calls) == 1
    assert "complete" not in [name for name, _ in events]


# ---------------------------------------------------------------------------
# Edge case: very large repository
# ---------------------------------------------------------------------------


async def test_large_repo_warns_before_scanning(
    client, monkeypatch
) -> None:
    async def _big(owner, repo, github_token=None):  # noqa: ANN001, ANN202
        return BIG_REPO_WARNING

    captured = clean_scan_result(size_warning=BIG_REPO_WARNING)
    monkeypatch.setattr(cb, "check_repo_size", _big)
    monkeypatch.setattr(cb, "SemgrepScanner", lambda: _FakeScanner(result=captured))

    events = await _stages(client, {"repo_url": "https://github.com/acme/huge"})

    # The warning is an early advisory, not a phase change or an error.
    warnings = [
        data for stage, data in events
        if stage == "progress" and data.get("warning")
    ]
    assert warnings == [
        {"message": BIG_REPO_WARNING, "elapsed_seconds": 0, "warning": True}
    ]
    assert [stage for stage, _ in events][0] == "started"
    assert events[-1][0] == "complete"
    assert events[-1][1]["size_warning"] == BIG_REPO_WARNING


async def test_size_warning_is_threaded_into_the_scan(
    client, monkeypatch
) -> None:
    async def _big(owner, repo, github_token=None):  # noqa: ANN001, ANN202
        return BIG_REPO_WARNING

    monkeypatch.setattr(cb, "check_repo_size", _big)
    monkeypatch.setattr(cb, "SemgrepScanner", _FakeScanner)

    await _stages(client, {"repo_url": "https://github.com/acme/huge"})

    scanner = _FakeScanner.instances[0]
    assert scanner.calls[0][0] == "https://github.com/acme/huge"
    assert scanner.calls[0][2] == BIG_REPO_WARNING


# ---------------------------------------------------------------------------
# Correlation is optional and never fatal
# ---------------------------------------------------------------------------


async def test_unknown_connection_id_still_completes(
    client, no_size_check, monkeypatch
) -> None:
    monkeypatch.setattr(cb, "SemgrepScanner", _FakeScanner)
    events = await _stages(
        client,
        {"repo_url": "https://github.com/acme/shop", "connection_id": "nope"},
    )
    assert events[-1][0] == "complete"
    assert events[-1][1]["correlations"] == []
