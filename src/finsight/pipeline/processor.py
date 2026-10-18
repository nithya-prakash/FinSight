"""Idempotent event processing.

Strategy: one DB transaction per event writes the transaction row, prediction, alert and the
`processed_events` ledger row together. The Kafka offset is committed *after* that DB commit
(at-least-once delivery). If the consumer crashes between the two, the event is redelivered and
the `ON CONFLICT DO NOTHING` insert on transaction_id detects it as a duplicate: no second
transaction, prediction or alert is written.
"""
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import psycopg
from pydantic import ValidationError

from ..events import TransactionEvent
from ..logs import log
from ..observability import CONSUMER_EVENTS, DQ_FAILURES, SCORING_SECONDS
from ..scoring.context import fetch_context
from ..scoring.rules import Context, RuleConfig, combine, evaluate, severity

logger = logging.getLogger("finsight.processor")
Outcome = Literal["processed", "duplicate", "dead_letter"]


@dataclass(frozen=True)
class Meta:
    topic: str
    partition: int
    offset: int


class Scorer(Protocol):
    threshold: float
    version: str

    def score(self, ctx: Context) -> tuple[float, dict[str, Any]]: ...


@dataclass(frozen=True)
class ScoringConfig:
    alert_threshold: float = 0.5
    model_version: str = "rules-v1"
    rules: RuleConfig = field(default_factory=RuleConfig)
    scorer: "Scorer | None" = None      # ml.inference.ScoringModel; None = explicit rules-only baseline
    ml_threshold: float | None = None   # overrides the model's validation-tuned threshold


_INSERT_TXN = """INSERT INTO core.transactions (transaction_id, event_time, customer_id, account_id,
  merchant_id, counterparty_account_id, amount, currency, transaction_type, channel, device_id,
  ip_region, lat, lon, status) VALUES (%(transaction_id)s, %(event_time)s, %(customer_id)s,
  %(account_id)s, %(merchant_id)s, %(counterparty_account_id)s, %(amount)s, %(currency)s,
  %(transaction_type)s, %(channel)s, %(device_id)s, %(ip_region)s, %(lat)s, %(lon)s, %(status)s)
  ON CONFLICT (transaction_id) DO NOTHING RETURNING transaction_id"""


def dead_letter(conn: psycopg.Connection, raw: bytes | str, meta: Meta, error: str) -> None:
    payload = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    DQ_FAILURES.labels("malformed" if error.startswith("invalid event") else "db_rejected").inc()
    CONSUMER_EVENTS.labels("dead_letter").inc()
    with conn.transaction():
        conn.execute("INSERT INTO core.dead_letter_events (topic, kafka_partition, kafka_offset, payload, "
                     "error) VALUES (%s,%s,%s,%s,%s)",
                     (meta.topic, meta.partition, meta.offset, payload.replace("\x00", ""), error[:500]))
    log(logger, logging.WARNING, "dead_letter", offset=meta.offset, partition=meta.partition, error=error[:200])


def process_event(conn: psycopg.Connection, raw: bytes | str, meta: Meta,
                  cfg: ScoringConfig | None = None) -> Outcome:
    """Returns the outcome; raises psycopg.OperationalError for transient DB failures (caller retries)."""
    cfg = cfg or ScoringConfig()
    try:
        ev = TransactionEvent.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as e:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        dead_letter(conn, raw, meta, f"invalid event: {type(e).__name__}: {e}")
        return "dead_letter"
    t = ev.transaction
    started = time.perf_counter()
    try:
        with conn.transaction():
            row = conn.execute(_INSERT_TXN, t.model_dump()).fetchone()
            if row is None:  # redelivery of an already-persisted event
                cur = conn.execute("UPDATE core.processed_events SET duplicate_deliveries = "
                                   "duplicate_deliveries + 1 WHERE event_id=%s", (ev.event_id,))
                if cur.rowcount == 0:
                    conn.execute("INSERT INTO core.processed_events (event_id, transaction_id, topic, "
                                 "kafka_partition, kafka_offset, outcome, event_time) VALUES "
                                 "(%s,%s,%s,%s,%s,'duplicate',%s) ON CONFLICT DO NOTHING",
                                 (ev.event_id, t.transaction_id, meta.topic, meta.partition, meta.offset,
                                  ev.event_time))
                outcome: Outcome = "duplicate"
            else:
                ctx = fetch_context(conn, t)
                signals = evaluate(ctx, cfg.rules)
                sig_list = [x.model_dump() for x in signals]
                evidence: dict = {}
                if cfg.scorer is not None:
                    score, ml = cfg.scorer.score(ctx)
                    threshold = cfg.ml_threshold if cfg.ml_threshold is not None else cfg.scorer.threshold
                    version = cfg.scorer.version
                    sig_list.append({"rule": "ml_model", "score": score, "evidence": {"top_contributions": ml["top_contributions"]},
                                     "explanation": f"model score {score:.3f} vs alert threshold {threshold:.3f}"})
                    evidence = {"ml": ml, "rules_score": combine(signals)}
                else:
                    score, threshold, version = combine(signals), cfg.alert_threshold, cfg.model_version
                sig_json = json.dumps(sig_list)
                conn.execute("INSERT INTO core.model_predictions (transaction_id, model_version, "
                             "risk_score, evidence, scored_at) VALUES (%s,%s,%s,%s::jsonb, now()) "
                             "ON CONFLICT DO NOTHING",
                             (t.transaction_id, version, score, json.dumps({"signals": sig_list, **evidence})))
                if score >= threshold:
                    conn.execute("INSERT INTO core.fraud_alerts (transaction_id, severity, signals) "
                                 "VALUES (%s,%s,%s::jsonb) ON CONFLICT (transaction_id) DO NOTHING",
                                 (t.transaction_id, severity(score), sig_json))
                conn.execute("INSERT INTO core.processed_events (event_id, transaction_id, topic, "
                             "kafka_partition, kafka_offset, outcome, event_time, scoring_latency_ms) "
                             "VALUES (%s,%s,%s,%s,%s,'processed',%s,%s)",
                             (ev.event_id, t.transaction_id, meta.topic, meta.partition, meta.offset,
                              ev.event_time, (time.perf_counter() - started) * 1000))
                outcome = "processed"
    except (psycopg.errors.IntegrityError, psycopg.errors.DataError) as e:
        # Permanent failure (e.g. unknown account, constraint violation): park it, don't loop forever.
        dead_letter(conn, raw, meta, f"rejected by database: {type(e).__name__}: {e}")
        return "dead_letter"
    CONSUMER_EVENTS.labels(outcome).inc()
    if outcome == "processed":
        SCORING_SECONDS.observe(time.perf_counter() - started)
    log(logger, logging.INFO, outcome, event_id=ev.event_id, offset=meta.offset)
    return outcome
