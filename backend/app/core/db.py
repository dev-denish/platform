"""
Database access layer (connection pooling + transactions).

Existing implementation (MVP): `psycopg2.connect(...)` opened a brand-new
connection on every request, several handlers had no try/finally so a failing
query LEAKED the connection, and there was no pool - under load the database's
connection slots exhaust and the service falls over.

Enterprise solution: a single process-wide psycopg3 `ConnectionPool` opened at
startup and closed on graceful shutdown. Callers borrow a connection via a context
manager that ALWAYS returns it to the pool, and a `transaction()` helper that
commits on success and rolls back on any exception. `RealDictRow` gives dict rows.
A pgbouncer sidecar would sit in front of this in the cluster for cross-pod pooling;
the in-process pool handles per-pod concurrency.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger("dmrv.db")


class Database:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: ConnectionPool | None = None

    def connect(self) -> None:
        if self._pool is not None:
            return
        self._pool = ConnectionPool(
            conninfo=self._settings.dsn,
            min_size=self._settings.db_pool_min,
            max_size=self._settings.db_pool_max,
            timeout=self._settings.db_pool_timeout_s,
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=False,
        )
        self._pool.open(wait=True, timeout=self._settings.db_pool_timeout_s)
        log.info(
            "db.pool.opened",
            min=self._settings.db_pool_min,
            max=self._settings.db_pool_max,
        )

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None
            log.info("db.pool.closed")

    @property
    def pool(self) -> ConnectionPool:
        if self._pool is None:
            raise RuntimeError("Database pool is not open. Call connect() first.")
        return self._pool

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """Borrow a connection; always returned to the pool, even on error."""
        with self.pool.connection() as conn:
            yield conn

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Cursor]:
        """A cursor inside a transaction: commit on success, rollback on exception."""
        with self.pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    yield cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def check(self) -> bool:
        """Cheap readiness probe query."""
        try:
            with self.connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() is not None
        except Exception as e:  # noqa: BLE001 - readiness must never raise
            log.warning("db.check.failed", error=str(e))
            return False


def fetch_all(cur: psycopg.Cursor, sql: str, params: Any = None) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    return list(cur.fetchall())


def fetch_one(cur: psycopg.Cursor, sql: str, params: Any = None) -> dict[str, Any] | None:
    cur.execute(sql, params)
    return cur.fetchone()
