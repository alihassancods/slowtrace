"""Edge-case tests for the Semgrep scanner — all offline, no network.

Covers the deployment failure modes the polish pass added: a missing Semgrep
binary, a very large repository, the .semgrepignore template, and the path
handling that feeds GitHub deep links.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import httpx

from agent.codebase import semgrep_scanner as mod
from agent.codebase.semgrep_scanner import (
    ERRORS,
    INSTALL_COMMAND,
    RULES_FILE,
    SEMGREPIGNORE_TEMPLATE,
    SemgrepScanner,
    _fail,
    _relative_path,
    _write_semgrepignore,
    check_repo_size,
    format_size_warning,
)

# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def test_rules_file_ships_with_the_package() -> None:
    assert RULES_FILE.is_file()


def test_binary_resolves_inside_the_venv_not_path() -> None:
    """The CLI is a venv dependency, so it is normally absent from $PATH."""
    scanner = SemgrepScanner()
    assert scanner.available is True
    assert Path(scanner.semgrep_path).is_file()

    venv_sibling = Path(mod.sys.executable).parent / "semgrep"
    if venv_sibling.is_file():
        # The venv copy must win over any unrelated system install.
        assert scanner.semgrep_path == str(venv_sibling)


def test_missing_binary_is_detected() -> None:
    assert SemgrepScanner(semgrep_binary="/nonexistent/semgrep").available is False


async def test_scan_reports_not_installed_without_touching_the_network() -> None:
    scanner = SemgrepScanner(semgrep_binary="/nonexistent/semgrep")
    result = await scanner.scan("https://github.com/psf/requests")

    assert result["success"] is False
    assert result["error"] == "semgrep_not_installed"
    assert result["message"] == "Semgrep is not installed on the server."
    assert result["install_command"] == INSTALL_COMMAND


def test_install_command_matches_this_projects_tooling() -> None:
    """This host has no pip, so the copy must not tell users to run pip."""
    assert "uv pip install" in INSTALL_COMMAND
    assert "semgrep>=1.70.0" in INSTALL_COMMAND


def test_only_the_setup_error_carries_an_install_command() -> None:
    assert "install_command" in _fail("semgrep_not_installed")
    assert "install_command" not in _fail("private")
    assert "semgrep_not_installed" in ERRORS


# ---------------------------------------------------------------------------
# Large repositories
# ---------------------------------------------------------------------------


def test_small_repos_produce_no_warning() -> None:
    assert format_size_warning(0) is None
    assert format_size_warning(49_999) is None


def test_large_repo_warning_uses_the_specified_copy() -> None:
    warning = format_size_warning(50_000)
    assert warning is not None
    assert warning.startswith("Repository is large (48.8mb).")
    assert "Consider scanning a subdirectory instead." in warning


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _patch_client(monkeypatch, response=None, error=None) -> None:
    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def get(self, url, headers=None):
            if error is not None:
                raise error
            return response

    monkeypatch.setattr(
        mod,
        "httpx",
        types.SimpleNamespace(AsyncClient=_Client, HTTPError=httpx.HTTPError),
    )


async def test_size_check_reads_the_size_field(monkeypatch) -> None:
    _patch_client(monkeypatch, response=_FakeResponse(200, {"size": 500_000}))
    warning = await check_repo_size("psf", "requests")
    assert warning is not None and "488.3mb" in warning


async def test_size_check_returns_none_for_small_repos(monkeypatch) -> None:
    _patch_client(monkeypatch, response=_FakeResponse(200, {"size": 1_200}))
    assert await check_repo_size("psf", "requests") is None


async def test_size_check_ignores_non_200(monkeypatch) -> None:
    _patch_client(monkeypatch, response=_FakeResponse(404, {"message": "Not Found"}))
    assert await check_repo_size("acme", "hidden") is None


async def test_size_check_never_raises(monkeypatch) -> None:
    _patch_client(monkeypatch, error=httpx.ConnectError("offline"))
    assert await check_repo_size("acme", "shop") is None


async def test_size_check_survives_a_missing_size_field(monkeypatch) -> None:
    _patch_client(monkeypatch, response=_FakeResponse(200, {}))
    assert await check_repo_size("acme", "shop") is None


# ---------------------------------------------------------------------------
# .semgrepignore
# ---------------------------------------------------------------------------


def test_template_excludes_the_known_noise_directories() -> None:
    text = SEMGREPIGNORE_TEMPLATE.read_text()
    for pattern in ("node_modules/", "vendor/", ".venv/", "*.min.js", "dist/",
                    "*/migrations/*.py", "fixtures/"):
        assert pattern in text, f"missing exclusion: {pattern}"


def test_template_is_written_at_the_scan_root(tmp_path: Path) -> None:
    assert _write_semgrepignore(str(tmp_path)) is True
    written = tmp_path / ".semgrepignore"
    assert written.read_text() == SEMGREPIGNORE_TEMPLATE.read_text()


def test_missing_template_degrades_quietly(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        mod, "SEMGREPIGNORE_TEMPLATE", tmp_path / "does-not-exist"
    )
    assert _write_semgrepignore(str(tmp_path)) is False


async def test_ignore_file_takes_effect_in_a_real_scan(tmp_path: Path) -> None:
    """The template must actually suppress findings, against the real binary.

    Measured behaviour: Semgrep already excludes node_modules/, vendor/, dist/,
    build/ and migrations/* by default, so `fixtures/` is what this template
    genuinely adds (and the reason the assertion targets it).
    """
    code = "def v():\n    return User.objects.all()\n"
    for folder in ("app", "fixtures", "node_modules"):
        target = tmp_path / folder
        target.mkdir(parents=True)
        (target / "code.py").write_text(code)

    def scanned_folders(payload: str) -> set[str]:
        results = json.loads(payload)["results"]
        return {Path(r["path"]).relative_to(tmp_path).parts[0] for r in results}

    before = await SemgrepScanner()._run_semgrep(str(tmp_path), str(RULES_FILE))
    assert before["success"] is True
    assert scanned_folders(before["output"]) == {"app", "fixtures"}

    assert _write_semgrepignore(str(tmp_path)) is True
    after = await SemgrepScanner()._run_semgrep(str(tmp_path), str(RULES_FILE))
    assert scanned_folders(after["output"]) == {"app"}


# ---------------------------------------------------------------------------
# Paths — regression guard for the "strip the repo folder" idea, which would
# drop a real directory level because the clone root IS the repository.
# ---------------------------------------------------------------------------


def test_relative_path_keeps_every_directory_level(tmp_path: Path) -> None:
    deep = tmp_path / "aggregator" / "tests" / "helpers.py"
    deep.parent.mkdir(parents=True)
    deep.write_text("x = 1\n")

    rel = _relative_path(str(deep), tmp_path.resolve())
    assert rel == "aggregator/tests/helpers.py"
    assert not rel.startswith("tests/")


def test_relative_path_tolerates_an_outside_root(tmp_path: Path) -> None:
    assert _relative_path("/somewhere/else.py", tmp_path.resolve()).endswith("else.py")


def test_scanner_rejects_non_github_urls_before_any_work() -> None:
    import asyncio

    for bad in ("", "https://gitlab.com/a/b", "ext::sh -c touch% /tmp/pwn"):
        result = asyncio.run(SemgrepScanner().scan(bad))
        assert result["success"] is False
        assert result["error"] == "invalid_url"
