"""Shared in-memory connection registry.

All routers that need to resolve a connection_id → DSN import from here.
"""

from __future__ import annotations

# { uuid_str: dsn_str }
CONNECTIONS: dict[str, str] = {}
