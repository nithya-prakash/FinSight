"""Plain-SQL migration runner and health check (no implicit schema creation)."""
import hashlib
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import tuple_row

from .config import MIGRATIONS_DIR

_LOCK_ID = 727001


def one(cur: psycopg.Cursor[Any]) -> Any:
    """The single row a query must return (aggregates, lookups guarded by foreign keys)."""
    row = cur.fetchone()
    if row is None:
        raise LookupError("query unexpectedly returned no rows")
    return row


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations in order; returns applied versions. Detects edited migrations."""
    applied: list[str] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_ID,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
            "version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ DEFAULT now())"
        )
        done: dict[str, str] = dict(conn.execute("SELECT version, checksum FROM public.schema_migrations").fetchall())
        for f in sorted(directory.glob("*.sql")):
            sql = f.read_text()
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            if f.stem in done:
                if done[f.stem] != checksum:
                    raise RuntimeError(f"Migration {f.stem} was modified after being applied")
                continue
            conn.execute(sql)
            conn.execute(
                "INSERT INTO public.schema_migrations (version, checksum) VALUES (%s, %s)",
                (f.stem, checksum),
            )
            applied.append(f.stem)
    return applied


def health(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> dict:
    expected = [f.stem for f in sorted(directory.glob("*.sql"))]
    try:
        cur = conn.cursor(row_factory=tuple_row)
        have = [r[0] for r in cur.execute("SELECT version FROM public.schema_migrations ORDER BY 1")]
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        have = []
    pending = [v for v in expected if v not in have]
    return {"database": "ok", "migrations_applied": have, "migrations_pending": pending,
            "ready": not pending}
