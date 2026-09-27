"""Codebase scan API — Semgrep-driven, streamed to the client as SSE.

Routes (mounted by ``api/main.py`` under ``/api/codebase``):

* ``POST /scan``                          — clone + scan, streamed as SSE.
* ``GET  /results?repo_url=...``          — the cached scan, no re-scan.
* ``GET  /smell/{smell_id}?repo_url=...`` — one smell type and its findings.

SSE envelope follows the rest of the app: ``data: {"stage": ..., "data": {...}}``
with a matching ``event:`` line, so clients can use ``addEventListener`` or
plain ``onmessage``.

State is an in-memory dict keyed by the canonical repo URL (process lifetime),
matching ``api/store.py``.

All detection lives in ``rules/slowtrace-rules.yml`` via
:class:`~agent.codebase.semgrep_scanner.SemgrepScanner`; this module only
streams progress and correlates findings against slow queries.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, field_validator

from agent.codebase.semgrep_scanner import (
    SemgrepScanner,
    _parse_repo_ref,
    check_repo_size,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# repo_url (canonical) → full scan result. See module docstring.
scan_cache: dict[str, dict[str, Any]] = {}
MAX_CACHED_SCANS = 50

PROGRESS_INTERVAL_SECONDS = 2
# Progress ticks spent saying "Cloning..." before switching to "Analyzing...".
CLONE_PHASE_TICKS = 5


class ScanRequest(BaseModel):
    repo_url: str
    github_token: str | None = None
    connection_id: str | None = None

    @field_validator("repo_url")
    @classmethod
    def validate_repo_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("repo_url is required.")
        if len(value) > 512:
            raise ValueError("repo_url is too long (max 512 characters).")
        return value

    @field_validator("github_token")
    @classmethod
    def validate_github_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > 128:
            raise ValueError("github_token is too long (max 128 characters).")
        return value or None


# ---------------------------------------------------------------------------
# POST /scan
# ---------------------------------------------------------------------------


@router.post("/scan")
async def scan_repository(request: ScanRequest) -> Any:
    """Scan a GitHub repository for database anti-patterns using Semgrep.

    Streams ``started`` → ``progress`` (every 2s while the scan runs) →
    ``complete`` (full result, also cached) or ``error``.
    """
    # One source of truth for URL validity: the same parser the scanner uses.
    if _parse_repo_ref(request.repo_url) is None:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "invalid_url",
                "message": "Please enter a valid GitHub URL",
            },
        )

    return StreamingResponse(
        _stream_scan(request.repo_url, request.github_token, request.connection_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream",
        },
    )


async def _stream_scan(
    repo_url: str,
    github_token: str | None,
    connection_id: str | None,
) -> AsyncGenerator[str, None]:
    """Run the scan as a task while emitting progress so the socket stays live."""
    scanner = SemgrepScanner()

    def event(stage: str, data: dict[str, Any]) -> str:
        payload = json.dumps({"stage": stage, "data": data}, default=str)
        return f"event: {stage}\ndata: {payload}\n\n"

    yield event("started", {"message": "Cloning repository...", "repo_url": repo_url})

    # Large-repo heads-up, raised before the clone starts so the wait is
    # explained up front rather than discovered afterwards. Advisory only.
    size_warning: str | None = None
    ref = _parse_repo_ref(repo_url)
    if ref is not None:
        size_warning = await check_repo_size(ref[0], ref[1], github_token)
    if size_warning:
        yield event(
            "progress",
            {"message": size_warning, "elapsed_seconds": 0, "warning": True},
        )

    scan_task = asyncio.create_task(
        scanner.scan(repo_url, github_token, size_warning=size_warning)
    )
    try:
        ticks = 0
        while not scan_task.done():
            # wait_for keeps a hung scan from spinning this loop forever.
            try:
                await asyncio.wait_for(
                    asyncio.shield(scan_task), PROGRESS_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass
            ticks += 1
            message = (
                "Cloning..."
                if ticks < CLONE_PHASE_TICKS
                else "Analyzing with Semgrep..."
            )
            yield event(
                "progress",
                {
                    "message": message,
                    "elapsed_seconds": ticks * PROGRESS_INTERVAL_SECONDS,
                },
            )

        result = scan_task.result()
    finally:
        # A disconnecting client must not leave a clone + scan running.
        if not scan_task.done():
            scan_task.cancel()
            await asyncio.gather(scan_task, return_exceptions=True)

    if not result.get("success"):
        yield event("error", result)
        return

    # The scanner already returns repo-relative paths and a github_url per
    # finding, so no path rewriting happens here.
    if connection_id:
        result["correlations"] = await _correlate_with_db(
            result.get("findings", []), connection_id
        )

    _cache_store(result)
    yield event("complete", result)


# ---------------------------------------------------------------------------
# Cached reads
# ---------------------------------------------------------------------------


def _cache_key(repo_url: str) -> str | None:
    """Canonical key for a repo URL, or None when it is not a GitHub URL."""
    ref = _parse_repo_ref(repo_url or "")
    if ref is None:
        return None
    return f"https://github.com/{ref[0]}/{ref[1]}"


def _cache_store(result: dict[str, Any]) -> None:
    key = result.get("repo_url") or _cache_key(result.get("repo_url", ""))
    if not key:
        return
    scan_cache[key] = result
    # Rough bound on process-lifetime memory; oldest entries go first.
    while len(scan_cache) > MAX_CACHED_SCANS:
        scan_cache.pop(next(iter(scan_cache)))


def _cache_lookup(repo_url: str) -> dict[str, Any] | None:
    key = _cache_key(repo_url)
    return scan_cache.get(key) if key else None


@router.get("/results")
async def get_cached_results(repo_url: str) -> Any:
    """Return a previous scan's results without re-scanning."""
    data = _cache_lookup(repo_url)
    if data is None:
        return {"success": False, "error": "No results found. Run a scan first."}
    return {"success": True, "data": data}


@router.get("/smell/{smell_id}")
async def get_smell_details(smell_id: str, repo_url: str) -> Any:
    """Return every finding of one smell type."""
    data = _cache_lookup(repo_url)
    if data is None:
        return {"success": False, "error": "No results found"}

    grouped = data.get("grouped_by_type") or {}
    if smell_id not in grouped:
        return {"success": False, "error": "No findings for this smell"}
    return {"success": True, "data": grouped[smell_id]}


# ---------------------------------------------------------------------------
# Correlation with live slow-query data
# ---------------------------------------------------------------------------


async def _correlate_with_db(
    findings: list[dict[str, Any]], connection_id: str
) -> list[dict[str, Any]]:
    """Match findings against SQLCommenter-traced slow queries on the same file.

    Best-effort by design: an unknown connection, a dead database, or a missing
    ``pg_stat_statements`` must not fail the scan, so every problem collapses to
    "no correlations" and is logged rather than raised.
    """
    from api.queries import get_queries  # local import: avoids a cycle at startup

    try:
        report = await get_queries(connection_id)
    except Exception as exc:  # noqa: BLE001 - optional enrichment, never fatal
        logger.warning("query correlation skipped for %s: %s", connection_id, exc)
        return []

    traced = [q for q in report.queries if q.is_traced and q.code_location]
    if not traced:
        return []

    correlations: list[dict[str, Any]] = []
    for finding in findings:
        finding_file = str(finding.get("file", ""))
        if not finding_file:
            continue
        finding_base = finding_file.rsplit("/", 1)[-1]

        for query in traced:
            # code_location is the string "path/to/file.py:123".
            query_file = _strip_line_suffix(str(query.code_location))
            if not query_file:
                continue
            if (
                finding_file in query_file
                or query_file in finding_file
                or finding_base == query_file.rsplit("/", 1)[-1]
            ):
                correlations.append(
                    {
                        "finding": finding,
                        "slow_query": query.model_dump(),
                        "match_type": "same_file",
                        "confidence": "high",
                        "combined_message": (
                            f"This {finding.get('name', 'pattern')} in "
                            f"{finding_file} is in the same file as a slow "
                            f"query taking {query.mean_exec_time_ms:.0f}ms "
                            "average."
                        ),
                    }
                )
                break

    return correlations


def _strip_line_suffix(location: str) -> str:
    """``app/views.py:123`` -> ``app/views.py``; leave paths without a line as-is."""
    head, _, tail = location.rpartition(":")
    return head if head and tail.isdigit() else location
