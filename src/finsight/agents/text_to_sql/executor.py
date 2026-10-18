"""Executes validated SQL through the read-only role with DB-side timeout, then masks and suppresses."""
import hashlib
import hmac
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg

from .policy import Validated
from .semantic import load_catalog

_COUNTISH = re.compile(r"(^|_)(count|n|total)$|count")


class ExecutionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class ExecConfig:
    timeout_ms: int = 5000
    row_limit: int = 200
    min_group_size: int = 3
    mask_key: str = "dev-only-mask-key-change-me"


@dataclass
class QueryResult:
    columns: list[dict]
    rows: list[list]
    truncated: bool
    masked_columns: list[str] = field(default_factory=list)
    suppressed_rows: int = 0
    db_ms: float = 0.0


def pseudonym(value, key: str) -> str | None:
    if value is None:
        return None
    return "id_" + hmac.new(key.encode(), str(value).encode(), hashlib.sha256).hexdigest()[:10]


def _kind(values: list) -> str:
    v = next((x for x in values if x is not None), None)
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int | float | Decimal):
        return "number"
    if isinstance(v, date | datetime):
        return "date"
    if isinstance(v, str):
        return "text"
    return "other"


def _json(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, list | tuple):
        return [_json(x) for x in v]
    return v


def sanitize_db_error(e: Exception) -> ExecutionError:
    if isinstance(e, psycopg.errors.QueryCanceled):
        return ExecutionError("timeout", "query exceeded the time limit; simplify or add filters")
    if isinstance(e, psycopg.errors.InsufficientPrivilege):
        return ExecutionError("permission_denied", "permission denied for the requested object")
    if isinstance(e, psycopg.errors.UndefinedColumn | psycopg.errors.UndefinedTable | psycopg.errors.UndefinedFunction
                  | psycopg.errors.SyntaxError | psycopg.errors.DatatypeMismatch | psycopg.errors.AmbiguousColumn
                  | psycopg.errors.GroupingError | psycopg.errors.DivisionByZero | psycopg.errors.InvalidTextRepresentation):
        first = (str(e).splitlines() or [""])[0]
        return ExecutionError("sql_error", re.sub(r"\s+", " ", first)[:200])
    return ExecutionError("database_error", f"database error ({type(e).__name__})")


def execute(bi_dsn: str, v: Validated, cfg: ExecConfig | None = None) -> QueryResult:
    cfg = cfg or ExecConfig()
    t0 = time.perf_counter()
    try:
        with psycopg.connect(bi_dsn, connect_timeout=5) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL statement_timeout = {int(cfg.timeout_ms)}")
                cur.execute(v.sql)
                if cur.description is None:
                    raise ExecutionError("no_result", "the statement returned no result set")
                names = [d.name for d in cur.description]
                raw = cur.fetchmany(cfg.row_limit + 1)
    except psycopg.Error as e:
        raise sanitize_db_error(e) from e
    truncated = len(raw) > cfg.row_limit
    raw = raw[: cfg.row_limit]
    cols_vals: list[tuple[Any, ...]] = list(zip(*raw, strict=False)) if raw else [() for _ in names]
    kinds = [_kind(list(c)) for c in cols_vals]

    sensitive = load_catalog().sensitive_columns()
    mask_idx = {i for i in v.sensitive_projections if i >= 0}
    if -1 in v.sensitive_projections:
        mask_idx |= {i for i, n in enumerate(names) if n.lower() in sensitive}
    rows: list[list[Any]] = [[pseudonym(r[i], cfg.mask_key) if i in mask_idx else _json(r[i]) for i in range(len(names))] for r in raw]
    for i in mask_idx:
        kinds[i] = "text"
    suppressed = 0
    if v.quasi_identifiers_used:
        cidx = [i for i, n in enumerate(names) if kinds[i] == "number" and _COUNTISH.search(n.lower())]
        if cidx:
            keep = [r for r in rows if all((r[i] is None or float(r[i]) >= cfg.min_group_size) for i in cidx)]
            suppressed, rows = len(rows) - len(keep), keep
    return QueryResult([{"name": n, "type": k} for n, k in zip(names, kinds, strict=True)], rows, truncated,
                       sorted(names[i] for i in mask_idx), suppressed, (time.perf_counter() - t0) * 1000)
