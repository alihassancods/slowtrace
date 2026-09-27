"""Codebase analysis now uses Semgrep — this module is the entire scanner.

It replaces the deleted custom implementation (GitHub REST client, framework
detector, regex pattern matcher, ORM-smell orchestrator) with:

    shallow clone -> run Semgrep with rules/slowtrace-rules.yml -> parse + enrich

Adding a smell means adding a YAML rule, not Python. Behaviour verified against
Semgrep 1.178.0; the gotchas this code defends against are recorded in
docs/backend/semgrep.md:

* ``semgrep`` is installed in the project venv, not on ``$PATH``.
* ``check_id`` is namespaced with the config path; only the last dotted segment
  is the rule id.
* ``extra.lines`` is the literal ``"requires login"`` without a Semgrep account,
  so matched code is read back out of the clone.
* Exit code is ``0`` with and without findings, so an unparseable stdout — not
  the return code — is the failure signal.
* ``scan()`` never raises: every failure is a fixed-shape dict so the UI can
  render guidance.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

RULES_FILE = Path(__file__).parent / "rules" / "slowtrace-rules.yml"
SEMGREPIGNORE_TEMPLATE = Path(__file__).parent / "semgrepignore_template"

CLONE_TIMEOUT = 60
SCAN_TIMEOUT = 120
PUBLIC_SCAN_TIMEOUT = 90
PER_FILE_TIMEOUT = 30
MAX_TARGET_BYTES = 500_000
MAX_PUBLIC_FINDINGS = 25  # keep registry noise from burying our own rules
SNIPPET_CAP = 2000
SIZE_CHECK_TIMEOUT = 10  # seconds; advisory GitHub lookup before cloning
LARGE_REPO_KB = 50_000  # 50 MB, above which a scan gets noticeably slow

# Shown verbatim when Semgrep is missing; `which` is not used because the CLI is
# a venv dependency that is normally absent from $PATH (see __init__).
INSTALL_COMMAND = 'uv pip install --python .venv/bin/python "semgrep>=1.70.0"'

# Only these forms are accepted. The clone URL is rebuilt from the validated
# owner/repo, so a crafted repo_url can never reach git as an `ext::`/`file://`
# command, and a stored token can never be sent to a non-GitHub host.
_REPO_PATTERNS = (
    re.compile(
        r"^https://(?:www\.)?github\.com/"
        r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$",
        re.I,
    ),
    re.compile(
        r"^git@github\.com:(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?$", re.I
    ),
)

ERRORS = {
    "invalid_url": "Please enter a valid GitHub URL (https://github.com/owner/repo).",
    "private": "This repo is private. Add a GitHub token to scan it.",
    "not_found": "Repository not found. Check the URL and try again.",
    "rate_limited": "GitHub rate limit reached. Add a token to continue.",
    "semgrep_not_installed": "Semgrep is not installed on the server.",
    "network": "Could not reach GitHub. Check your connection and try again.",
    "semgrep_error": "The scanner failed to run. Try again.",
    "scan_timeout": "The scan took too long and was stopped.",
    "rules_missing": "The Semgrep rule file is missing from this installation.",
}


def _fail(kind: str, detail: str = "") -> dict[str, Any]:
    """Fixed-shape error. `message` is display-safe, `detail` is for logs."""
    result: dict[str, Any] = {
        "success": False,
        "error": kind,
        "message": ERRORS.get(kind, ERRORS["semgrep_error"]),
        "detail": detail,
    }
    if kind == "semgrep_not_installed":
        # Lets the UI render a copyable setup step instead of a dead end.
        result["install_command"] = INSTALL_COMMAND
    return result


def _count(findings: list[dict[str, Any]], severity: str) -> int:
    return sum(1 for f in findings if f["severity"] == severity)


def _redact(text: str, secret: str | None) -> str:
    return text.replace(secret, "<redacted>") if secret else text


class SemgrepScanner:
    """Scan a GitHub repository for database smells with Semgrep."""

    RULES_FILE = RULES_FILE

    # smell_id (from each rule's metadata) -> UI-facing description.
    SMELL_INFO = {
        "n_plus_one": {
            "name": "N+1 Query Pattern",
            "severity": "critical",
            "impact": "Multiplies database queries by row count",
            "docs_url": "https://docs.djangoproject.com/en/stable/"
            "ref/models/querysets/#prefetch-related",
        },
        "select_star": {
            "name": "Fetching All Columns",
            "severity": "warning",
            "impact": "Wastes memory and prevents index-only scans",
        },
        "missing_pagination": {
            "name": "Unbounded Query",
            "severity": "critical",
            "impact": "Returns all rows — will crash with large datasets",
        },
        "string_sql": {
            "name": "SQL Injection Risk",
            "severity": "critical",
            "impact": "Security vulnerability and broken query caching",
        },
        "lazy_loading": {
            "name": "Lazy Loading Relationship",
            "severity": "warning",
            "impact": "Extra DB roundtrip per row",
        },
        "inefficient_exists": {
            "name": "Inefficient Existence Check",
            "severity": "warning",
            "impact": "Loads all rows just to check if any exist",
        },
    }

    def __init__(self, semgrep_binary: str | None = None) -> None:
        # Resolution order matters: Semgrep is a *venv* dependency on this
        # project, so the CLI normally sits next to the running interpreter and
        # is NOT on $PATH. A `subprocess.run(["which", "semgrep"])` probe would
        # therefore report a correctly installed server as broken — instead we
        # resolve the path ourselves and expose it via `available`.
        sibling = Path(sys.executable).parent / "semgrep"
        if semgrep_binary:
            resolved = semgrep_binary
        elif sibling.is_file():
            resolved = str(sibling)
        else:
            resolved = shutil.which("semgrep") or ""

        self._semgrep = resolved or "semgrep"
        self.semgrep_path = resolved
        self.available = bool(resolved) and Path(resolved).is_file()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def scan(
        self,
        repo_url: str,
        github_token: str | None = None,
        use_public_rules: bool = True,
        size_warning: str | None = None,
    ) -> dict[str, Any]:
        """Clone ``repo_url``, scan it, and return findings. Never raises.

        ``size_warning`` is threaded through from the caller's pre-clone
        ``check_repo_size`` so the final payload can repeat it; the scanner does
        not make that request itself.
        """
        started = time.time()
        if not RULES_FILE.is_file():
            return _fail("rules_missing", str(RULES_FILE))
        if not self.available:
            return _fail("semgrep_not_installed", "semgrep CLI not found on this host")

        ref = _parse_repo_ref(repo_url)
        if ref is None:
            return _fail("invalid_url", repo_url)
        owner, repo = ref

        with tempfile.TemporaryDirectory(prefix="slowtrace-scan-") as tmpdir:
            clone = await self._clone_repo(owner, repo, tmpdir, github_token)
            if not clone["success"]:
                return clone

            # Written after the clone so it becomes the project-root ignore file
            # that Semgrep honours (verified: excludes node_modules/ fixtures/).
            _write_semgrepignore(tmpdir)

            custom = await self._run_semgrep(tmpdir, str(RULES_FILE), SCAN_TIMEOUT)
            if not custom["success"]:
                return custom
            findings = self._parse_findings(custom["output"], tmpdir, owner, repo)

            if use_public_rules:
                public = await self._run_semgrep_public(tmpdir)
                if public["success"]:
                    extra = self._parse_findings(public["output"], tmpdir, owner, repo)
                    findings.extend(_prioritise(extra)[:MAX_PUBLIC_FINDINGS])

            enriched = self._enrich_findings(_dedupe(findings))
            grouped = self._group_findings(enriched)
            return {
                "success": True,
                "repo_url": f"https://github.com/{owner}/{repo}",
                "scan_duration_seconds": round(time.time() - started, 1),
                "files_scanned": clone["file_count"],
                "total_findings": len(enriched),
                "critical_count": _count(enriched, "critical"),
                "warning_count": _count(enriched, "warning"),
                "info_count": _count(enriched, "info"),
                "findings": enriched,
                "grouped_by_type": grouped,
                "priority_fixes": self._get_priority_fixes(grouped),
                "scanner": "semgrep",
                "size_warning": size_warning,
            }

    # ------------------------------------------------------------------
    # Step 1: clone
    # ------------------------------------------------------------------

    async def _clone_repo(
        self, owner: str, repo: str, target_dir: str, github_token: str | None = None
    ) -> dict[str, Any]:
        """Shallow-clone the default branch into ``target_dir``."""
        credentials = f"{github_token}@" if github_token else ""
        url = f"https://{credentials}github.com/{owner}/{repo}.git"
        args = [
            "git", "clone", "--quiet", "--depth=1", "--single-branch",
            url, target_dir,
        ]

        # GIT_TERMINAL_PROMPT=0 stops git blocking on a credential prompt, which
        # would otherwise stall the request for the whole timeout.
        stdout, stderr, code, timed_out = await _run(
            args, CLONE_TIMEOUT, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        )
        # Never log or return text that could contain the token.
        detail = _redact((stdout + stderr).decode(errors="replace"), github_token)

        if timed_out:
            return _fail("network", "git clone timed out")
        if code == 0:
            return {"success": True, "file_count": _count_files(target_dir)}
        return _fail(_classify_clone_error(detail), detail.strip()[-500:])

    # ------------------------------------------------------------------
    # Steps 2 & 3: semgrep
    # ------------------------------------------------------------------

    async def _run_semgrep(
        self,
        target_dir: str,
        config: str,
        timeout: int = SCAN_TIMEOUT,
        soft: bool = False,
    ) -> dict[str, Any]:
        """Scan with an explicit rules config and return raw JSON stdout.

        ``soft`` downgrades any failure to ``{"success": False}`` for callers
        that treat the ruleset as optional.
        """
        args = [
            self._semgrep, "scan", "--config", config, "--json", "--quiet",
            "--metrics=off", "--timeout", str(PER_FILE_TIMEOUT),
            "--max-target-bytes", str(MAX_TARGET_BYTES),
            # Vendored/generated paths are noise, not smells.
            "--exclude", ".git", "--exclude", "node_modules", "--exclude", "migrations",
            "--exclude", "__pycache__", "--exclude", "*.min.js", "--exclude", "*.pyc",
            target_dir,
        ]
        stdout, stderr, _code, timed_out = await _run(args, timeout)
        payload = None if timed_out else _json_object(stdout.decode(errors="replace"))
        if payload is not None and "results" not in payload:
            payload = None
        if payload is None:
            if soft:
                return {"success": False, "output": "{}"}
            if timed_out:
                return _fail("scan_timeout", "semgrep timed out")
            detail = stderr.decode(errors="replace").strip()
            return _fail("semgrep_error", detail[-500:] or "semgrep returned no JSON")
        return {"success": True, "output": json.dumps(payload)}

    async def _run_semgrep_public(self, target_dir: str) -> dict[str, Any]:
        """Semgrep's public ``p/python`` ruleset — optional, best-effort.

        Needs network access to fetch registry rules, so any failure degrades to
        "no public findings" instead of failing the scan.
        """
        return await self._run_semgrep(
            target_dir, "p/python", PUBLIC_SCAN_TIMEOUT, soft=True
        )

    # ------------------------------------------------------------------
    # Steps 4 & 5: parse, dedupe, enrich, group
    # ------------------------------------------------------------------

    def _parse_findings(
        self, semgrep_json: str, clone_dir: str, owner: str, repo: str
    ) -> list[dict[str, Any]]:
        """Turn Semgrep JSON into finding dicts.

        ``clone_dir`` is needed to make paths repo-relative and to read the
        matched source (``extra.lines`` is redacted without a Semgrep login).
        """
        data = _json_object(semgrep_json) or {}
        root = Path(clone_dir).resolve()
        findings = []
        for result in data.get("results") or []:
            extra = result.get("extra") or {}
            metadata = extra.get("metadata") or {}
            start = int((result.get("start") or {}).get("line", 0) or 0)
            end = int((result.get("end") or {}).get("line", start) or start)
            path = _relative_path(str(result.get("path", "")), root)
            findings.append(
                {
                    "rule_id": str(result.get("check_id", "")).rsplit(".", 1)[-1],
                    "smell_id": metadata.get("smell_id", "general"),
                    "file": path,
                    "line_start": start,
                    "line_end": max(end, start),
                    "code": _source_lines(root / path, start, end),
                    "message": " ".join(str(extra.get("message", "")).split()),
                    "raw_severity": extra.get("severity", "WARNING"),
                    "fix_code": metadata.get("fix_code", ""),
                    "category": metadata.get("category", "performance"),
                    "github_url": _github_url(owner, repo, path, start, end),
                }
            )
        return findings

    def _enrich_findings(self, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach the name/impact/severity from SMELL_INFO; worst findings first."""
        enriched = []
        for finding in findings:
            info = self.SMELL_INFO.get(finding["smell_id"], {})
            enriched.append(
                {
                    **finding,
                    "name": info.get("name", finding["rule_id"]),
                    "severity": info.get("severity", "warning"),
                    "impact": info.get("impact", ""),
                    "docs_url": info.get("docs_url", ""),
                }
            )

        def rank(finding: dict[str, Any]) -> tuple[int, str, int]:
            return (
                _SEVERITY_ORDER.get(finding["severity"], 3),
                finding["file"],
                finding["line_start"],
            )

        enriched.sort(key=rank)
        return enriched

    def _group_findings(
        self, findings: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Group by smell_id for the dashboard's per-type cards."""
        grouped: dict[str, dict[str, Any]] = {}
        for finding in findings:
            group = grouped.setdefault(
                finding["smell_id"],
                {
                    "smell_id": finding["smell_id"],
                    "name": finding["name"],
                    "severity": finding["severity"],
                    "impact": finding["impact"],
                    "fix_code": finding.get("fix_code", ""),
                    "count": 0,
                    "findings": [],
                },
            )
            group["findings"].append(finding)
            group["count"] += 1
        return grouped

    @staticmethod
    def _get_priority_fixes(
        grouped: dict[str, dict[str, Any]], limit: int = 3
    ) -> list[dict[str, Any]]:
        """Top 3 issues to fix first: severity rank, then most occurrences.

        Each group carries a capped preview rather than its whole finding list,
        so the payload stays small for the dashboard.
        """
        ranked = sorted(
            grouped.values(),
            key=lambda g: (_SEVERITY_ORDER.get(g["severity"], 3), -g["count"]),
        )
        return [
            {**group, "findings": group["findings"][:5]} for group in ranked[:limit]
        ]


_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _parse_repo_ref(repo_url: str) -> tuple[str, str] | None:
    """Return ``(owner, repo)`` for a GitHub URL, else None."""
    url = (repo_url or "").strip()
    for pattern in _REPO_PATTERNS:
        match = pattern.match(url)
        if match:
            owner, repo = match["owner"], match["repo"]
            if owner in {".", ".."} or repo in {".", ".."}:
                return None
            return owner, repo
    return None


def format_size_warning(size_kb: int) -> str | None:
    """Advisory copy for a big repo, or None when the size is unremarkable."""
    if size_kb < LARGE_REPO_KB:
        return None
    return (
        f"Repository is large ({size_kb / 1024:.1f}mb). Scan may take 2-3 minutes. "
        "Consider scanning a subdirectory instead."
    )


async def check_repo_size(
    owner: str, repo: str, github_token: str | None = None
) -> str | None:
    """Ask GitHub for the repo size (KB) before spending time on a clone.

    Advisory only: any failure — offline, rate-limited, or a private repo this
    token cannot see — returns None so a scan is never blocked by this call.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "slowtrace-codebase-scanner",
    }
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    try:
        async with httpx.AsyncClient(timeout=SIZE_CHECK_TIMEOUT) as client:
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}", headers=headers
            )
        if response.status_code != 200:
            return None
        size_kb = int((response.json() or {}).get("size") or 0)
    except (httpx.HTTPError, ValueError, TypeError):
        return None

    return format_size_warning(size_kb)


def _write_semgrepignore(target_dir: str) -> bool:
    """Drop the ignore template at the scan root; False when unavailable."""
    try:
        template = SEMGREPIGNORE_TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        (Path(target_dir) / ".semgrepignore").write_text(template, encoding="utf-8")
    except OSError:
        return False
    return True


def _classify_clone_error(detail: str) -> str:
    """Map git's stderr onto an error kind the frontend already renders.

    GitHub answers an unauthenticated clone of a *missing* repository with the
    same credential prompt it uses for a *private* one ("could not read
    Username"), so the two cannot be told apart without a token; reporting that
    as "private" is the honest next step either way. Order matters: a real 404
    only appears once git is authenticated.
    """
    text = detail.lower()
    if "rate limit" in text or "too many requests" in text:
        return "rate_limited"
    if "repository not found" in text or "does not exist" in text:
        return "not_found"
    auth = ("could not read username", "authentication failed", "invalid token",
            "access denied", "permission denied", "terminal prompts disabled")
    if any(marker in text for marker in auth):
        return "private"
    offline = ("could not resolve host", "timed out", "connection", "network",
               "unable to", "rpc failed", "early eof")
    if any(marker in text for marker in offline):
        return "network"
    return "semgrep_error"


def _dedupe(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeats of the same smell on the same line.

    Rules 1, 2 and 12 deliberately overlap, so one loop can produce three hits
    under the same ``smell_id``. Earlier findings win, which is why the custom
    ruleset is parsed before the public one.
    """
    seen: set[tuple[str, str, int]] = set()
    unique = []
    for finding in findings:
        key = (finding["smell_id"], finding["file"], finding["line_start"])
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


def _prioritise(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order registry findings so the capped slice keeps the worst ones."""
    rank = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    return sorted(findings, key=lambda f: rank.get(str(f["raw_severity"]).upper(), 3))


def _count_files(target_dir: str) -> int:
    root = Path(target_dir)
    return sum(1 for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)


def _relative_path(reported: str, root: Path) -> str:
    """Semgrep echoes paths as given; make them repo-relative."""
    try:
        return str(Path(reported).resolve().relative_to(root))
    except (ValueError, OSError):
        return reported.lstrip("./") or reported


def _source_lines(path: Path, start: int, end: int) -> str:
    """Read the matched lines from the clone; "" if unreadable."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if start > len(lines):
        return ""
    return "\n".join(lines[max(1, start) - 1 : max(end, start)])[:SNIPPET_CAP]


def _github_url(owner: str, repo: str, path: str, start: int, end: int) -> str:
    """Link straight to the lines. HEAD is a valid GitHub ref."""
    if not path or not start:
        return ""
    anchor = f"#L{start}" if start == end else f"#L{start}-L{end}"
    return f"https://github.com/{owner}/{repo}/blob/HEAD/{path}{anchor}"


def _json_object(text: str) -> dict[str, Any] | None:
    """Parse stdout, tolerating a banner line before the JSON object."""
    text = (text or "").strip()
    if not text:
        return None
    for candidate in (text, text[text.find("{") :] if "{" in text else ""):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


async def _run(
    args: list[str], timeout: int, env: dict[str, str] | None = None
) -> tuple[bytes, bytes, int | None, bool]:
    """Run a subprocess with a wall-clock cap, always reaping it.

    Returns ``(stdout, stderr, returncode, timed_out)``. A missing binary comes
    back as a non-zero run with the reason on stderr instead of raising
    ``FileNotFoundError`` into the caller's response.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as exc:  # FileNotFoundError, PermissionError
        return b"", f"{args[0]} could not be run: {exc}".encode(), 127, False

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout, stderr, proc.returncode, False
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return b"", b"", proc.returncode, True
    except asyncio.CancelledError:
        # Cancelling the awaiting task does not stop the child on its own, so a
        # disconnected client would otherwise leave git/semgrep running.
        proc.kill()
        await proc.wait()
        raise
