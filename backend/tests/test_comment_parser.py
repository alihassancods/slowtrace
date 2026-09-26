"""Unit tests for src/agent/code_linker/comment_parser.py."""

from __future__ import annotations

import pytest

from src.agent.code_linker.comment_parser import SQLCommentParser


@pytest.fixture()
def parser() -> SQLCommentParser:
    return SQLCommentParser()


# ---------------------------------------------------------------------------
# SQLCommentParser.parse — SQLCommenter format
# ---------------------------------------------------------------------------


class TestParseSQLCommenter:
    QUERY = (
        "SELECT * FROM orders WHERE user_id = $1\n"
        "/*file='order_service.py',line='47',"
        "action='get_pending_orders',"
        "controller='OrdersController',"
        "route='GET %2Fapi%2Forders%2Fpending',"
        "db_driver='django'*/"
    )

    def test_has_trace_is_true(self, parser):
        result = parser.parse(self.QUERY)
        assert result["has_trace"] is True

    def test_file_extracted(self, parser):
        result = parser.parse(self.QUERY)
        assert result["file"] == "order_service.py"

    def test_line_converted_to_int(self, parser):
        result = parser.parse(self.QUERY)
        assert result["line"] == 47
        assert isinstance(result["line"], int)

    def test_action_extracted(self, parser):
        result = parser.parse(self.QUERY)
        assert result["action"] == "get_pending_orders"

    def test_controller_extracted(self, parser):
        result = parser.parse(self.QUERY)
        assert result["controller"] == "OrdersController"

    def test_db_driver_extracted(self, parser):
        result = parser.parse(self.QUERY)
        assert result["db_driver"] == "django"

    def test_url_encoded_route_decoded(self, parser):
        result = parser.parse(self.QUERY)
        assert result["route"] == "GET /api/orders/pending"

    def test_all_expected_keys_present(self, parser):
        result = parser.parse(self.QUERY)
        for key in ("has_trace", "file", "line", "action", "controller", "route", "db_driver"):
            assert key in result, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# SQLCommentParser.parse — no comment present
# ---------------------------------------------------------------------------


class TestParseNoComment:
    def test_plain_query_returns_no_trace(self, parser):
        result = parser.parse("SELECT * FROM users WHERE id = $1")
        assert result == {"has_trace": False}

    def test_empty_string_returns_no_trace(self, parser):
        assert parser.parse("") == {"has_trace": False}

    def test_query_with_dash_comment_ignored(self, parser):
        # SQL -- comments are not SQLCommenter; no trace expected
        result = parser.parse("SELECT 1 -- just a comment")
        assert result == {"has_trace": False}

    def test_empty_block_comment_returns_no_trace(self, parser):
        result = parser.parse("SELECT 1 /**/")
        assert result == {"has_trace": False}


# ---------------------------------------------------------------------------
# SQLCommentParser.parse — Rails Marginalia format
# ---------------------------------------------------------------------------


class TestParseMarginalia:
    QUERY = (
        "SELECT * FROM users\n"
        "/*application:MyApp,controller:orders,action:pending,"
        "line:/app/services/order_service.rb:47*/"
    )

    def test_has_trace_is_true(self, parser):
        result = parser.parse(self.QUERY)
        assert result["has_trace"] is True

    def test_application_extracted(self, parser):
        result = parser.parse(self.QUERY)
        assert result["application"] == "MyApp"

    def test_controller_extracted(self, parser):
        result = parser.parse(self.QUERY)
        assert result["controller"] == "orders"

    def test_action_extracted(self, parser):
        result = parser.parse(self.QUERY)
        assert result["action"] == "pending"

    def test_line_path_extracted(self, parser):
        result = parser.parse(self.QUERY)
        # The full path including embedded line number is preserved as string
        assert result["line"] == "/app/services/order_service.rb:47"

    def test_does_not_cast_line_to_int_for_marginalia(self, parser):
        result = parser.parse(self.QUERY)
        # Marginalia "line" is a file path string, not a bare integer
        assert isinstance(result["line"], str)


# ---------------------------------------------------------------------------
# SQLCommentParser.parse — URL-encoded special characters
# ---------------------------------------------------------------------------


class TestParseURLEncoding:
    def test_space_encoded_as_plus(self, parser):
        query = "SELECT 1 /*action='get+pending+orders'*/"
        result = parser.parse(query)
        # %2B would be a plus sign; '+' in query strings means space only via
        # unquote_plus — unquote leaves '+' as-is which is the SQLCommenter spec.
        assert result["action"] == "get+pending+orders"

    def test_percent_encoded_slash(self, parser):
        query = "SELECT 1 /*route='GET %2Fapi%2Fusers'*/"
        result = parser.parse(query)
        assert result["route"] == "GET /api/users"

    def test_percent_encoded_colon(self, parser):
        query = "SELECT 1 /*db_driver='psycopg2%3A2.9'*/"
        result = parser.parse(query)
        assert result["db_driver"] == "psycopg2:2.9"

    def test_percent_encoded_space(self, parser):
        query = "SELECT 1 /*controller='My%20Controller'*/"
        result = parser.parse(query)
        assert result["controller"] == "My Controller"

    def test_multiple_encoded_values(self, parser):
        query = (
            "SELECT 1 "
            "/*file='app%2Fservices%2Forder.py',line='10',"
            "route='GET %2Forders%2F%7Bid%7D'*/"
        )
        result = parser.parse(query)
        assert result["file"] == "app/services/order.py"
        assert result["line"] == 10
        assert result["route"] == "GET /orders/{id}"


# ---------------------------------------------------------------------------
# SQLCommentParser.check_if_active
# ---------------------------------------------------------------------------


class TestCheckIfActive:
    def test_returns_true_when_one_query_is_traced(self, parser):
        queries = [
            {"query": "SELECT 1"},
            {"query": "SELECT 1 /*file='a.py',line='1'*/"},
        ]
        assert parser.check_if_active(queries) is True

    def test_returns_false_when_no_queries_are_traced(self, parser):
        queries = [
            {"query": "SELECT 1"},
            {"query": "SELECT 2"},
        ]
        assert parser.check_if_active(queries) is False

    def test_empty_list_returns_false(self, parser):
        assert parser.check_if_active([]) is False

    def test_missing_query_key_does_not_raise(self, parser):
        queries = [{"calls": 5}, {"query": "SELECT 1 /*file='a.py',line='1'*/"}]
        assert parser.check_if_active(queries) is True

    def test_all_traced_returns_true(self, parser):
        queries = [
            {"query": "SELECT 1 /*file='a.py',line='1'*/"},
            {"query": "SELECT 2 /*file='b.py',line='2'*/"},
        ]
        assert parser.check_if_active(queries) is True


# ---------------------------------------------------------------------------
# SQLCommentParser.enrich_queries
# ---------------------------------------------------------------------------


class TestEnrichQueries:
    def test_traced_query_gets_code_location(self, parser):
        queries = [{"query": "SELECT 1 /*file='orders.py',line='10'*/"}]
        result = parser.enrich_queries(queries)
        assert result[0]["is_traced"] is True
        assert result[0]["code_location"] == "orders.py:10"

    def test_untraced_query_gets_none_code_location(self, parser):
        queries = [{"query": "SELECT 1"}]
        result = parser.enrich_queries(queries)
        assert result[0]["is_traced"] is False
        assert result[0]["code_location"] is None

    def test_original_dict_not_mutated(self, parser):
        original = {"query": "SELECT 1 /*file='a.py',line='5'*/", "calls": 3}
        parser.enrich_queries([original])
        assert "is_traced" not in original
        assert "code_location" not in original

    def test_empty_list_returns_empty_list(self, parser):
        assert parser.enrich_queries([]) == []

    def test_preserves_existing_fields(self, parser):
        queries = [{"query": "SELECT 1 /*file='a.py',line='1'*/", "calls": 99}]
        result = parser.enrich_queries(queries)
        assert result[0]["calls"] == 99

    def test_mixed_traced_and_untraced(self, parser):
        queries = [
            {"query": "SELECT 1 /*file='a.py',line='1'*/"},
            {"query": "SELECT 2"},
        ]
        result = parser.enrich_queries(queries)
        assert result[0]["is_traced"] is True
        assert result[1]["is_traced"] is False


# ---------------------------------------------------------------------------
# SQLCommentParser.get_code_snippet — placeholder
# ---------------------------------------------------------------------------


class TestGetCodeSnippet:
    def test_returns_dict(self, parser):
        result = parser.get_code_snippet("app/orders.py", 47)
        assert isinstance(result, dict)

    def test_available_is_false(self, parser):
        result = parser.get_code_snippet("app/orders.py", 47)
        assert result["available"] is False

    def test_snippet_is_none(self, parser):
        result = parser.get_code_snippet("app/orders.py", 47)
        assert result["snippet"] is None

    def test_echoes_file_path_and_line(self, parser):
        result = parser.get_code_snippet("app/orders.py", 47, context_lines=5)
        assert result["file_path"] == "app/orders.py"
        assert result["line_number"] == 47
        assert result["context_lines"] == 5
