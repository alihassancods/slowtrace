"""SQLCommenter and Rails Marginalia comment parser.

Extracts code-location metadata embedded by ORMs in SQL comments so that
slow queries can be traced back to their source file and line.

Supported formats
-----------------
SQLCommenter (Django, SQLAlchemy, Spring, …):
    SELECT 1 /*file='app/orders.py',line='47',action='index',db_driver='django'*/

Rails Marginalia:
    SELECT 1 /*application:MyApp,controller:orders,action:index,
               line:/app/services/order_service.rb:47*/
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Capture the first /*…*/ block in a query (non-greedy, dot matches newline).
_COMMENT_RE = re.compile(r"/\*(.*?)\*/", re.DOTALL)

# SQLCommenter pair:   key='value'   (value may be URL-encoded)
_SQLCOMMENTER_PAIR_RE = re.compile(r"(\w+)='([^']*)'")

# Marginalia pair:     key:value   (no quotes; value ends at , or end-of-string)
# The value may itself contain colons (e.g. file paths like /app/service.rb:47)
_MARGINALIA_PAIR_RE = re.compile(r"(\w+):([^,]+)")


def _is_sqlcommenter(comment_body: str) -> bool:
    """Return True when the comment uses key='value' syntax."""
    return bool(_SQLCOMMENTER_PAIR_RE.search(comment_body))


class SQLCommentParser:
    """Parse SQLCommenter / Marginalia metadata from SQL query strings."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, query: str) -> dict[str, Any]:
        """Extract metadata from the first ``/*…*/`` comment in *query*.

        Returns ``{"has_trace": False}`` when no comment is present or the
        comment contains no recognisable key-value pairs.
        """
        match = _COMMENT_RE.search(query)
        if not match:
            return {"has_trace": False}

        body = match.group(1)

        if _is_sqlcommenter(body):
            pairs = _SQLCOMMENTER_PAIR_RE.findall(body)
            if not pairs:
                return {"has_trace": False}
            result: dict[str, Any] = {"has_trace": True}
            for key, value in pairs:
                decoded = unquote(value)
                result[key] = int(decoded) if key == "line" else decoded
            return result

        # Fall back to Marginalia format
        pairs_m = _MARGINALIA_PAIR_RE.findall(body)
        if not pairs_m:
            return {"has_trace": False}

        result = {"has_trace": True}
        for key, raw_value in pairs_m:
            value = raw_value.strip()
            # Marginalia encodes the line number inside the path for the
            # "line" key (e.g. line:/app/service.rb:47).  Keep the full
            # value as a string — callers can split on ":" themselves.
            result[key] = value
        return result

    def check_if_active(self, queries: list[dict[str, Any]]) -> bool:
        """Return True if *any* query in the list carries SQLCommenter tags."""
        for q in queries:
            raw_query = q.get("query", "")
            if raw_query and self.parse(raw_query).get("has_trace"):
                return True
        return False

    def enrich_queries(
        self, queries: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Add ``code_location`` and ``is_traced`` fields to each query dict.

        The original dicts are *not* mutated; enriched copies are returned.
        """
        enriched = []
        for q in queries:
            q = dict(q)  # shallow copy — don't mutate the caller's data
            meta = self.parse(q.get("query", ""))
            q["is_traced"] = meta.get("has_trace", False)
            if q["is_traced"]:
                file_path = meta.get("file", "")
                line = meta.get("line", "")
                q["code_location"] = f"{file_path}:{line}" if line else file_path
            else:
                q["code_location"] = None
            enriched.append(q)
        return enriched

    def get_code_snippet(
        self,
        file_path: str,
        line_number: int,
        context_lines: int = 3,
    ) -> dict[str, Any]:
        """Return a code snippet centred on *line_number* in *file_path*.

        GitHub integration is not yet implemented; a placeholder is returned
        so that callers can already depend on the stable interface.
        """
        return {
            "file_path": file_path,
            "line_number": line_number,
            "context_lines": context_lines,
            "snippet": None,
            "available": False,
            "message": "GitHub repo integration not yet configured.",
        }
