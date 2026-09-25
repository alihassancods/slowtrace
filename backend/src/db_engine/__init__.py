"""db_engine – async PostgreSQL connection pool for SlowTrace."""

from .connector import DBConnector

__all__ = ["DBConnector"]
