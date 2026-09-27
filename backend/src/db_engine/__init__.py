"""db_engine – async PostgreSQL connection pool for SlowTrace."""

from .connector import DBConnector
from .health_checks import (
    calculate_health_score,
    check_cache_hit_ratio,
    check_connection_count,
    check_dead_tuple_ratio,
    check_index_usage,
    check_lock_contention,
    check_long_transactions,
    check_replication_lag,
    check_table_bloat,
)

__all__ = [
    "DBConnector",
    "calculate_health_score",
    "check_cache_hit_ratio",
    "check_connection_count",
    "check_dead_tuple_ratio",
    "check_index_usage",
    "check_lock_contention",
    "check_long_transactions",
    "check_replication_lag",
    "check_table_bloat",
]
