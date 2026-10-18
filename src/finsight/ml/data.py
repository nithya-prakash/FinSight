"""Labeled frames for offline modelling, from the generator or from the database."""
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd
import psycopg

from ..generator import Dataset
from ..generator.models import Transaction
from ..scoring.rules import combine, evaluate
from .features import FEATURES, OfflineContexts, featurize


@dataclass
class Frame:
    transactions: list[Transaction]
    labels: dict[str, bool]
    merchants: dict[str, tuple[str, str]]          # merchant_id -> (category, risk_tier)
    devices: dict[str, tuple[str | None, str]]     # device_id -> (owner customer, type)
    dataset_id: str


def frame_from_dataset(ds: Dataset) -> Frame:
    tier = {c.category_code: c.risk_tier for c in ds.categories}
    return Frame(ds.transactions, {l.transaction_id: l.is_fraud for l in ds.labels},
                 {m.merchant_id: (m.category_code, tier[m.category_code]) for m in ds.merchants},
                 {d.device_id: (d.customer_id, d.device_type) for d in ds.devices}, ds.dataset_id)


def frame_from_db(conn: psycopg.Connection) -> Frame:
    cols = list(Transaction.model_fields)
    txns = [Transaction(**dict(zip(cols, r, strict=True))) for r in conn.execute(
        f"SELECT {','.join(cols)} FROM core.transactions ORDER BY event_time, transaction_id")]
    labels: dict[str, bool] = dict(conn.execute("SELECT transaction_id, is_fraud FROM eval.fraud_labels").fetchall())
    mer = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT m.merchant_id, m.category_code, c.risk_tier FROM core.merchants m JOIN core.merchant_categories c USING (category_code)")}
    dev = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT device_id, customer_id, device_type FROM core.devices")}
    did = conn.execute("SELECT dataset_id FROM eval.datasets ORDER BY created_at DESC LIMIT 1").fetchone()
    return Frame(txns, {k: v for k, v in labels.items() if k in {t.transaction_id for t in txns}}, mer, dev,
                 did[0] if did else "db")


def build_table(frame: Frame) -> pd.DataFrame:
    """One row per transaction: model features, rules-baseline score, label, amount, event_time."""
    rows = []
    for t, ctx in OfflineContexts(frame.merchants, frame.devices).iterate(frame.transactions):
        f = featurize(ctx)
        f["rules_score"] = combine(evaluate(ctx))
        f["transaction_id"], f["event_time"] = t.transaction_id, t.event_time
        f["label"] = int(frame.labels.get(t.transaction_id, False))
        rows.append(f)
    return pd.DataFrame(rows)


def temporal_split(df: pd.DataFrame, train_frac=0.6, val_frac=0.2, gap=timedelta(hours=24)):
    """Chronological train / validation / holdout with a label-maturity gap dropped at each boundary.

    Rows within `gap` after a boundary are discarded so episodes (and their delayed labels) that
    straddle the boundary cannot leak across splits. Holdout is only for the final report.
    """
    d = df.sort_values(["event_time", "transaction_id"]).reset_index(drop=True)
    t0, t1 = d.event_time.iloc[0], d.event_time.iloc[-1]
    span = t1 - t0
    b1, b2 = t0 + span * train_frac, t0 + span * (train_frac + val_frac)
    train = d[d.event_time < b1]
    val = d[(d.event_time >= b1 + gap) & (d.event_time < b2)]
    hold = d[d.event_time >= b2 + gap]
    return train, val, hold


def xy(df: pd.DataFrame, features: list[str] | None = None):
    return df[features or FEATURES].to_numpy(dtype=float), df["label"].to_numpy(dtype=int)


__all__ = ["Frame", "frame_from_dataset", "frame_from_db", "build_table", "temporal_split", "xy", "np"]
