"""Unit tests for services/ai_explainer.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.ai_explainer import (
    AIExplainer,
    _GENERIC_FIX_FALLBACK,
    _GENERIC_PROBLEM_FALLBACK,
    _FIX_FALLBACKS,
    _PROBLEM_FALLBACKS,
    _api_key,
    _chat,
)


# ---------------------------------------------------------------------------
# _api_key
# ---------------------------------------------------------------------------


class TestApiKey:
    def test_reads_deepseek_key(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert _api_key() == "ds-key"

    def test_falls_back_to_openai_key(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "oai-key")
        assert _api_key() == "oai-key"

    def test_returns_none_when_no_keys(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert _api_key() is None


# ---------------------------------------------------------------------------
# _chat — returns None when no API key
# ---------------------------------------------------------------------------


class TestChat:
    async def test_returns_none_without_api_key(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = await _chat("system", "user")
        assert result is None

    async def test_returns_none_on_http_error(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        import httpx

        with patch("services.ai_explainer.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(
                side_effect=httpx.ConnectError("connection refused")
            )
            mock_client_cls.return_value = mock_client

            result = await _chat("system", "user")

        assert result is None

    async def test_returns_none_on_bad_json(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")

        with patch("services.ai_explainer.httpx.AsyncClient") as mock_client_cls:
            mock_response = AsyncMock()
            mock_response.raise_for_status = MagicMock()  # sync method in httpx
            mock_response.json = MagicMock(return_value={"bad": "shape"})

            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await _chat("system", "user")

        assert result is None


# We need MagicMock imported
from unittest.mock import MagicMock  # noqa: E402  (after the test that uses it first)


# ---------------------------------------------------------------------------
# AIExplainer.explain_problem — fallbacks
# ---------------------------------------------------------------------------


class TestExplainProblemFallbacks:
    """When the API returns None, pre-written fallbacks are used."""

    @pytest.fixture(autouse=True)
    def no_api(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def test_missing_index_fallback(self):
        explainer = AIExplainer()
        result = await explainer.explain_problem(
            "SELECT id FROM users WHERE email = $1",
            {"type": "missing_index"},
            {"mean_exec_time_ms": 500, "calls": 1000},
        )
        assert result == _PROBLEM_FALLBACKS["missing_index"]

    async def test_select_star_fallback(self):
        explainer = AIExplainer()
        result = await explainer.explain_problem(
            "SELECT * FROM orders",
            {"type": "select_star"},
            {},
        )
        assert result == _PROBLEM_FALLBACKS["select_star"]

    async def test_n_plus_one_fallback(self):
        explainer = AIExplainer()
        result = await explainer.explain_problem(
            "SELECT id FROM t WHERE id = $1",
            {"type": "n_plus_one"},
            {"mean_exec_time_ms": 1, "calls": 100_000},
        )
        assert result == _PROBLEM_FALLBACKS["n_plus_one"]

    async def test_missing_limit_fallback(self):
        explainer = AIExplainer()
        result = await explainer.explain_problem(
            "SELECT * FROM big",
            {"type": "missing_limit"},
            {},
        )
        assert result == _PROBLEM_FALLBACKS["missing_limit"]

    async def test_seq_scan_fallback(self):
        explainer = AIExplainer()
        result = await explainer.explain_problem(
            "SELECT name FROM logs WHERE level = 'error'",
            {"type": "seq_scan"},
            {},
        )
        assert result == _PROBLEM_FALLBACKS["seq_scan"]

    async def test_unknown_type_uses_generic_fallback(self):
        explainer = AIExplainer()
        result = await explainer.explain_problem(
            "SELECT 1", {"type": "does_not_exist"}, {}
        )
        assert result == _GENERIC_PROBLEM_FALLBACK

    async def test_returns_string(self):
        explainer = AIExplainer()
        result = await explainer.explain_problem("SELECT 1", {}, {})
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# AIExplainer.explain_fix — fallbacks
# ---------------------------------------------------------------------------


class TestExplainFixFallbacks:
    @pytest.fixture(autouse=True)
    def no_api(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def test_missing_index_fix_fallback(self):
        explainer = AIExplainer()
        result = await explainer.explain_fix(
            "CREATE INDEX CONCURRENTLY idx_users_email ON users(email);",
            {"type": "missing_index"},
        )
        assert result == _FIX_FALLBACKS["missing_index"]

    async def test_select_star_fix_fallback(self):
        explainer = AIExplainer()
        result = await explainer.explain_fix("-- Replace SELECT *", {"type": "select_star"})
        assert result == _FIX_FALLBACKS["select_star"]

    async def test_n_plus_one_fix_fallback(self):
        explainer = AIExplainer()
        result = await explainer.explain_fix("-- Batch your queries", {"type": "n_plus_one"})
        assert result == _FIX_FALLBACKS["n_plus_one"]

    async def test_missing_limit_fix_fallback(self):
        explainer = AIExplainer()
        result = await explainer.explain_fix("-- Add LIMIT", {"type": "missing_limit"})
        assert result == _FIX_FALLBACKS["missing_limit"]

    async def test_seq_scan_fix_fallback(self):
        explainer = AIExplainer()
        result = await explainer.explain_fix(
            "CREATE INDEX CONCURRENTLY idx_logs_level ON logs(level);",
            {"type": "seq_scan"},
        )
        assert result == _FIX_FALLBACKS["seq_scan"]

    async def test_unknown_type_uses_generic_fallback(self):
        explainer = AIExplainer()
        result = await explainer.explain_fix("-- noop", {"type": "zz"})
        assert result == _GENERIC_FIX_FALLBACK

    async def test_returns_string(self):
        explainer = AIExplainer()
        result = await explainer.explain_fix("-- noop", {})
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# AIExplainer — uses API response when available
# ---------------------------------------------------------------------------


class TestExplainUsesApiResponse:
    async def test_explain_problem_uses_api_text(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        explainer = AIExplainer()

        with patch("services.ai_explainer._chat", new=AsyncMock(return_value="API says slow.")):
            result = await explainer.explain_problem(
                "SELECT * FROM t", {"type": "missing_index"}, {}
            )

        assert result == "API says slow."

    async def test_explain_fix_uses_api_text(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        explainer = AIExplainer()

        with patch(
            "services.ai_explainer._chat", new=AsyncMock(return_value="Index speeds it up.")
        ):
            result = await explainer.explain_fix("CREATE INDEX ...", {"type": "missing_index"})

        assert result == "Index speeds it up."

    async def test_explain_problem_falls_back_when_api_returns_none(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        explainer = AIExplainer()

        with patch("services.ai_explainer._chat", new=AsyncMock(return_value=None)):
            result = await explainer.explain_problem(
                "SELECT * FROM t", {"type": "seq_scan"}, {}
            )

        assert result == _PROBLEM_FALLBACKS["seq_scan"]
