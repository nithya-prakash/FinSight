import logging
import time
from datetime import UTC, datetime

from confluent_kafka import Producer

from ..events import make_event
from ..generator import Dataset
from ..logs import log

logger = logging.getLogger("finsight.producer")


def publish_dataset(ds: Dataset, bootstrap: str, topic: str, *, limit: int | None = None,
                    rate_per_s: float | None = None) -> int:
    """Publish events in event-time order, keyed by account_id (per-account ordering)."""
    p = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True, "acks": "all",
                  "linger.ms": 5})
    errors: list[str] = []

    def on_delivery(err, _msg):
        if err:
            errors.append(str(err))

    n = 0
    for txn in ds.transactions[:limit]:
        ev = make_event(txn, datetime.now(UTC))
        while True:
            try:
                p.produce(topic, key=txn.account_id, value=ev.model_dump_json().encode(),
                          on_delivery=on_delivery)
                break
            except BufferError:
                p.poll(0.5)
        p.poll(0)
        n += 1
        if rate_per_s:
            time.sleep(1 / rate_per_s)
    remaining = p.flush(30)
    if remaining or errors:
        raise RuntimeError(f"publish failed: {remaining} undelivered, errors={errors[:3]}")
    log(logger, logging.INFO, "published", count=n, topic=topic)
    return n
