"""Dataset invariants checked before anything is written to the database."""
from collections import Counter

from .build import Dataset


def validate(ds: Dataset) -> dict:
    errs: list[str] = []
    cust = {c.customer_id for c in ds.customers}
    acc = {a.account_id: a.customer_id for a in ds.accounts}
    mer = {m.merchant_id for m in ds.merchants}
    dev = {d.device_id for d in ds.devices}
    ids = [t.transaction_id for t in ds.transactions]
    if len(set(ids)) != len(ids):
        errs.append("duplicate transaction ids")
    if [t.event_time for t in ds.transactions] != sorted(t.event_time for t in ds.transactions):
        errs.append("transactions not chronological")
    for t in ds.transactions:
        if t.customer_id not in cust or acc.get(t.account_id) != t.customer_id:
            errs.append(f"{t.transaction_id}: account/customer mismatch")
        if t.merchant_id and t.merchant_id not in mer:
            errs.append(f"{t.transaction_id}: unknown merchant")
        if t.counterparty_account_id and t.counterparty_account_id not in acc:
            errs.append(f"{t.transaction_id}: unknown counterparty")
        if t.device_id and t.device_id not in dev:
            errs.append(f"{t.transaction_id}: unknown device")
        if t.amount <= 0:
            errs.append(f"{t.transaction_id}: non-positive amount")
        if (t.transaction_type == "purchase") != (t.merchant_id is not None):
            errs.append(f"{t.transaction_id}: merchant/type inconsistency")
        if (t.transaction_type == "transfer") != (t.counterparty_account_id is not None):
            errs.append(f"{t.transaction_id}: counterparty/type inconsistency")
    if {l.transaction_id for l in ds.labels} != set(ids):
        errs.append("labels do not match transactions 1:1")
    n_fraud = sum(l.is_fraud for l in ds.labels)
    return {"ok": not errs, "errors": errs[:20], "transactions": len(ids), "fraud": n_fraud,
            "fraud_rate": n_fraud / max(len(ids), 1),
            "by_scenario": {f"{s}:{'fraud' if f else 'legit'}": n
                            for (s, f), n in Counter((l.scenario, l.is_fraud) for l in ds.labels).items()}}
