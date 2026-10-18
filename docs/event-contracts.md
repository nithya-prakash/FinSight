# Event contracts

**Topic** `finsight.transactions.v1` · key = `account_id` (per-account ordering) · value = JSON `TransactionEvent`
(`src/finsight/events.py`, schema_version 1).

| Field | Meaning |
|---|---|
| event_id | `evt_<transaction_id>`; stable idempotency key |
| schema_version | currently `1`; unknown versions are rejected to the dead-letter table |
| event_time | when the transaction happened (event time) |
| produced_at | when the producer emitted it (processing-side time) |
| transaction | the model-visible `Transaction` fields; **no labels or scenario metadata** |

Event time vs processing time: `core.transactions.event_time` is the business time; `ingested_at` and
`core.processed_events.processed_at` are processing time. Rules use only events with a smaller
`(event_time, transaction_id)`, so out-of-order arrival can make a rule see less history than a later
replay would (documented limitation).

## Delivery semantics
* Producer: idempotent, `acks=all`; flush failures raise.
* Consumer: **at-least-once**. Auto-commit is off; offsets are committed (batch of 100 or on idle/exit)
  only after the per-event DB transaction committed.
* Per event, one DB transaction writes transaction + prediction + alert + `processed_events` row.
  `INSERT ... ON CONFLICT DO NOTHING` on `transaction_id` (and a unique `fraud_alerts.transaction_id`)
  makes redelivery a no-op; duplicates increment `processed_events.duplicate_deliveries`.
* Crash after DB commit but before offset commit: the event is redelivered and detected as duplicate,
  so no second transaction, prediction or alert exists. Crash before DB commit: nothing persisted, event reprocessed.
* Malformed JSON/schema, unknown account/merchant, or constraint violations: stored in
  `core.dead_letter_events`, offset advances (no poison-pill loop).
* Transient DB errors: up to 4 retries with exponential backoff, then the consumer exits without
  committing (the container restarts and resumes). Broker outages are retried by librdkafka.
