"""Deterministic evidence retrieval + relationship analysis from PostgreSQL. Every item has an id and provenance.
Runs through the least-privilege `finsight_investigator` role (no labels, no customer names)."""
import json
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from ...db import one
from ...generator.models import Transaction
from ...ml.features import featurize
from ...scoring.context import fetch_context


def jsonable(o):
    return json.loads(json.dumps(o, default=lambda x: float(x) if isinstance(x, Decimal) else x.isoformat() if hasattr(x, "isoformat") else str(x)))


class Bundle:
    def __init__(self):
        self.items: list[dict] = []
        self.facts: list[dict] = []
        self.missing: list[str] = []

    def add(self, type_: str, source: str, data, basis: str = "database_fact") -> str:
        eid = f"E{len(self.items) + 1}"
        self.items.append({"id": eid, "type": type_, "source": source, "basis": basis, "data": jsonable(data)})
        return eid

    def ids(self) -> set[str]:
        return {i["id"] for i in self.items}


def gather(dsn: str, alert_id: int, alert_threshold: float = 0.5) -> dict:
    b = Bundle()
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        a = c.execute("SELECT * FROM core.fraud_alerts WHERE alert_id=%s", (alert_id,)).fetchone()
        if not a:
            raise LookupError(f"alert {alert_id} not found")
        t = one(c.execute("SELECT * FROM core.transactions WHERE transaction_id=%s", (a["transaction_id"],)))
        e_alert = b.add("alert", f"core.fraud_alerts:{alert_id}", a)
        e_txn = b.add("trigger_transaction", f"core.transactions:{t['transaction_id']}", t)
        pred = c.execute("SELECT model_version, risk_score, scored_at, evidence FROM core.model_predictions WHERE transaction_id=%s "
                         "ORDER BY scored_at DESC LIMIT 1", (t["transaction_id"],)).fetchone()
        e_pred = None
        if pred:
            ev = pred["evidence"] or {}
            e_pred = b.add("model_output", f"core.model_predictions:{t['transaction_id']}", {
                "model_version": pred["model_version"], "risk_score": pred["risk_score"], "alert_threshold_for_rules": alert_threshold,
                "top_contributions": (ev.get("ml") or {}).get("top_contributions"), "rules_score": ev.get("rules_score")}, "model_output")
        b.facts.append({"statement": f"Alert {alert_id} ({a['severity']}) was raised on transaction {t['transaction_id']} for {float(t['amount']):.2f} {t['currency']}.",
                        "evidence_ids": [e_alert, e_txn]})
        if pred:
            b.facts.append({"statement": f"Scored {float(pred['risk_score']):.3f} by {pred['model_version']} (model output, not a confirmed outcome).",
                            "evidence_ids": [e_pred]})
        sigs = [{"rule": s["rule"], "score": s["score"], "explanation": s["explanation"]} for s in (a["signals"] or [])]
        e_sig = b.add("risk_signals", f"core.fraud_alerts:{alert_id}.signals", sigs, "model_output")
        for s in sigs:
            b.facts.append({"statement": f"Signal '{s['rule']}' fired: {s['explanation']}.", "evidence_ids": [e_sig]})

        # merchant
        if t["merchant_id"]:
            m = one(c.execute("SELECT m.merchant_id, m.merchant_name, m.category_code, m.region, k.risk_tier FROM core.merchants m "
                          "JOIN core.merchant_categories k USING (category_code) WHERE merchant_id=%s", (t["merchant_id"],)))
            ms = one(c.execute("SELECT count(*) AS txns, count(fa.alert_id) AS alerts FROM core.transactions x LEFT JOIN core.fraud_alerts fa "
                           "USING (transaction_id) WHERE x.merchant_id=%s", (t["merchant_id"],)))
            b.add("merchant", f"core.merchants:{t['merchant_id']}", {**m, **ms})
        else:
            b.missing.append("No merchant is attached to this transaction type.")

        # customer profile + history
        cust = one(c.execute("SELECT customer_id, home_region, segment, created_at FROM core.customers WHERE customer_id=%s", (t["customer_id"],)))
        h = one(c.execute("SELECT count(*) AS prior_count, avg(amount) AS avg_amount, max(amount) AS max_amount, count(DISTINCT m.category_code) AS categories "
                      "FROM core.transactions x LEFT JOIN core.merchants m USING (merchant_id) WHERE x.customer_id=%s "
                      "AND (x.event_time, x.transaction_id) < (%s, %s)", (t["customer_id"], t["event_time"], t["transaction_id"])))
        b.add("customer_profile", f"core.customers:{t['customer_id']}", {**cust, **h})
        if h["prior_count"] < 5:
            b.missing.append(f"Little or no prior history for this customer ({h['prior_count']} earlier transactions).")

        # account activity before the trigger
        recent = c.execute(
            "SELECT x.transaction_id, x.event_time, x.amount, x.channel, x.transaction_type, x.status, m.category_code, x.ip_region "
            "FROM core.transactions x LEFT JOIN core.merchants m USING (merchant_id) WHERE x.account_id=%s AND (x.event_time, x.transaction_id) < (%s, %s) "
            "ORDER BY x.event_time DESC, x.transaction_id DESC LIMIT 15", (t["account_id"], t["event_time"], t["transaction_id"])).fetchall()
        e_recent = b.add("account_recent_transactions", f"core.transactions:account={t['account_id']}", recent)
        vel = one(c.execute("SELECT count(*) FILTER (WHERE event_time >= %(ts)s - interval '10 minutes') AS last_10m, "
                        "count(*) FILTER (WHERE event_time >= %(ts)s - interval '1 hour') AS last_1h, coalesce(sum(amount) FILTER (WHERE event_time >= %(ts)s - interval '1 hour'),0) AS amount_1h "
                        "FROM core.transactions WHERE account_id=%(acc)s AND (event_time, transaction_id) <= (%(ts)s, %(id)s)",
                        {"ts": t["event_time"], "acc": t["account_id"], "id": t["transaction_id"]}))
        e_vel = b.add("velocity", f"core.transactions:account={t['account_id']}", vel)
        b.facts.append({"statement": f"The account had {vel['last_10m']} transaction(s) in the 10 minutes and {vel['last_1h']} in the hour up to and including this one.",
                        "evidence_ids": [e_vel, e_recent]})

        # benign / contextual indicators (same feature definition used by the model)
        tx = Transaction(**{k: t[k] for k in Transaction.model_fields})
        with psycopg.connect(dsn) as plain:  # fetch_context expects tuple rows
            f = featurize(fetch_context(plain, tx))
        b.add("contextual_indicators", "ml.features.featurize(fs-1)", {
            "device_known_to_customer": bool(f["device_known"]), "category_seen_before_by_customer": bool(f["category_seen"]),
            "amount_vs_customer_average_ratio": round(f["amount_ratio"], 2), "km_from_previous_geolocated_txn": round(f["geo_km"]),
            "minutes_since_previous_geolocated_txn": round(f["geo_minutes"], 1), "implied_speed_kmh": round(f["geo_speed"])}, "database_fact")
        if t["lat"] is None:
            b.missing.append("No geolocation for this transaction.")

        # other alerts on the account
        rel_alerts = c.execute("SELECT fa.alert_id, fa.severity, fa.transaction_id, x.amount, x.event_time FROM core.fraud_alerts fa JOIN core.transactions x USING (transaction_id) "
                               "WHERE x.account_id=%s AND fa.alert_id<>%s ORDER BY fa.alert_id DESC LIMIT 10", (t["account_id"], alert_id)).fetchall()
        e_rel = b.add("related_alerts", f"core.fraud_alerts:account={t['account_id']}", rel_alerts)

        # --- network / relationship analysis -------------------------------------------------
        flows = c.execute("SELECT transaction_id, event_time, account_id, counterparty_account_id, amount, transaction_type FROM core.transactions "
                          "WHERE (account_id=%(a)s OR counterparty_account_id=%(a)s) AND transaction_type='transfer' "
                          "AND event_time BETWEEN %(ts)s - interval '48 hours' AND %(ts)s + interval '48 hours' ORDER BY event_time", {"a": t["account_id"], "ts": t["event_time"]}).fetchall()
        e_flow = b.add("account_transfers_48h", f"core.transactions:transfers account={t['account_id']}", flows, "observed_relationship")
        incoming = sum(float(x["amount"]) for x in flows if x["counterparty_account_id"] == t["account_id"])
        outgoing = sum(float(x["amount"]) for x in flows if x["account_id"] == t["account_id"])
        if incoming and outgoing >= 0.8 * incoming:
            b.add("inferred_pattern", "rule: pass-through >= 80% within 48h", {
                "pattern": "possible pass-through account", "received": round(incoming, 2), "sent_or_withdrawn": round(outgoing, 2),
                "caveat": "Inferred from amounts and timing only; legitimate pass-through (e.g. rent splitting) is possible."}, "inferred_suspicious")
        shared = c.execute("SELECT DISTINCT x.customer_id, x.account_id FROM core.transactions x WHERE x.device_id=%s AND x.customer_id<>%s LIMIT 10",
                           (t["device_id"], t["customer_id"])).fetchall() if t["device_id"] else []
        b.add("shared_device", f"core.transactions:device={t['device_id']}", {"other_customers_on_device": len(shared), "accounts": [s["account_id"] for s in shared]}, "observed_relationship")
        cp = [x for x in flows if x["counterparty_account_id"]]
        if cp:
            b.facts.append({"statement": f"{len(cp)} transfer(s) involving this account within 48 hours of the trigger.", "evidence_ids": [e_flow]})

        # potential exposure (deterministic): trigger + other alerted transactions on the account within 48h
        exp_rows = c.execute("SELECT x.transaction_id, x.amount FROM core.fraud_alerts fa JOIN core.transactions x USING (transaction_id) WHERE x.account_id=%(a)s "
                             "AND x.event_time BETWEEN %(ts)s - interval '48 hours' AND %(ts)s + interval '48 hours'", {"a": t["account_id"], "ts": t["event_time"]}).fetchall()
        exposure = round(sum(float(r["amount"]) for r in exp_rows), 2)
        e_exp = b.add("potential_exposure", "core.fraud_alerts x core.transactions (48h window)", {"amount": exposure, "transactions": [r["transaction_id"] for r in exp_rows]})
        b.facts.append({"statement": f"Alerted transactions on this account within 48 hours total {exposure:.2f} EUR (gross, not a confirmed loss).", "evidence_ids": [e_exp]})
        b.missing += ["No confirmed fraud outcome or customer contact is available for this case.", "No KYC, device-fingerprint or merchant-dispute data exists in this dataset."]
        timeline = sorted(
            [{"time": r["event_time"].isoformat(), "event": f"{r['transaction_type']} {float(r['amount']):.2f} via {r['channel']}", "evidence_ids": [e_recent]} for r in recent]
            + [{"time": t["event_time"].isoformat(), "event": f"TRIGGER {t['transaction_type']} {float(t['amount']):.2f} via {t['channel']}", "evidence_ids": [e_txn]},
               {"time": a["created_at"].isoformat(), "event": f"Alert {alert_id} created", "evidence_ids": [e_alert]}], key=lambda x: x["time"])
        _ = e_rel
    return {"items": b.items, "facts": b.facts, "missing": b.missing, "exposure": exposure, "timeline": timeline,
            "account_id": t["account_id"], "transaction_id": t["transaction_id"],
            "prior_count": h["prior_count"], "relevant_transactions": [r["transaction_id"] for r in exp_rows]}
