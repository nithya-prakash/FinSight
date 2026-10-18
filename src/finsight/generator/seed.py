import json
from collections.abc import Iterable

import psycopg
from pydantic import BaseModel

from ..db import one
from .build import Dataset
from .config import GENERATOR_VERSION

_TABLES = [  # (table, collection attr), FK-safe order
    ("core.customers", "customers"), ("core.accounts", "accounts"),
    ("core.merchant_categories", "categories"), ("core.merchants", "merchants"),
    ("core.devices", "devices"), ("core.transactions", "transactions"),
    ("eval.fraud_labels", "labels"),
]


def _copy(conn: psycopg.Connection, table: str, rows: Iterable[BaseModel]) -> None:
    rows = list(rows)
    if not rows:
        return
    cols = list(type(rows[0]).model_fields)
    with conn.cursor().copy(f"COPY {table} ({','.join(cols)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row([getattr(r, c) for c in cols])


def reset(conn: psycopg.Connection) -> None:
    conn.execute("TRUNCATE core.processed_events, core.dead_letter_events, core.investigation_cases, core.fraud_alerts, core.model_predictions, "
                 "eval.fraud_labels, eval.datasets, core.transactions, core.devices, core.merchants, "
                 "core.merchant_categories, core.accounts, core.customers RESTART IDENTITY CASCADE")


def seed(conn: psycopg.Connection, ds: Dataset, *, reset_first: bool = False,
         entities_only: bool = False) -> dict:
    """entities_only: skip core.transactions (they arrive via the stream); labels are always loaded."""
    with conn.transaction():
        if reset_first:
            reset(conn)
        elif one(conn.execute("SELECT count(*) FROM core.customers"))[0]:
            raise RuntimeError("Database already seeded; use --reset to replace it")
        conn.execute(
            "INSERT INTO eval.datasets (dataset_id, generator_version, seed, config) VALUES (%s,%s,%s,%s)",
            (ds.dataset_id, GENERATOR_VERSION, ds.config.seed, json.dumps(ds.config.model_dump())))
        for table, attr in _TABLES:
            if entities_only and attr == "transactions":
                continue
            _copy(conn, table, getattr(ds, attr))
    return {"dataset_id": ds.dataset_id, **{a: len(getattr(ds, a)) for _, a in _TABLES}}
