import logging
import os
import signal
import time
from dataclasses import replace
from pathlib import Path

import psycopg
from confluent_kafka import Consumer, KafkaError

from ..config import Settings
from ..logs import log
from .processor import Meta, ScoringConfig, process_event

logger = logging.getLogger("finsight.consumer")
HEARTBEAT = Path(os.environ.get("CONSUMER_HEARTBEAT", "/tmp/consumer.heartbeat"))
MAX_RETRIES = 4


def _process_with_retry(conn_holder: dict, dsn: str, msg, cfg: ScoringConfig):
    meta = Meta(msg.topic(), msg.partition(), msg.offset())
    for attempt in range(MAX_RETRIES + 1):
        try:
            if conn_holder.get("conn") is None or conn_holder["conn"].closed:
                conn_holder["conn"] = psycopg.connect(dsn, autocommit=True)
            return process_event(conn_holder["conn"], msg.value() or b"", meta, cfg)
        except psycopg.OperationalError as e:  # transient: DB restart / network
            conn_holder["conn"] = None
            if attempt == MAX_RETRIES:
                raise
            delay = min(2 ** attempt, 10)
            log(logger, logging.WARNING, "db_unavailable_retry", attempt=attempt + 1, delay_s=delay, error=str(e)[:120])
            time.sleep(delay)


def run_consumer(settings: Settings, *, provider=None, group_id: str | None = None, topic: str | None = None,
                 max_messages: int | None = None, idle_exit_s: float | None = None,
                 commit_every: int = 100) -> dict:
    base = ScoringConfig(alert_threshold=settings.alert_threshold, model_version=settings.model_version)
    if provider is None and settings.scoring_mode == "ml":
        from ..ml.inference import ModelProvider
        provider = ModelProvider(settings.mlflow_tracking_uri)
    if settings.scoring_mode == "ml":
        log(logger, logging.INFO, "scoring_mode", mode="ml", note="falls back to rules-only if no approved model")
    c = Consumer({"bootstrap.servers": settings.kafka_bootstrap,
                  "group.id": group_id or settings.consumer_group,
                  "enable.auto.commit": False, "auto.offset.reset": "earliest",
                  "enable.partition.eof": False})
    c.subscribe([topic or settings.kafka_topic])
    if settings.consumer_metrics_port:
        from prometheus_client import start_http_server
        start_http_server(settings.consumer_metrics_port)
    stop = {"flag": False}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda *_: stop.update(flag=True))
        except ValueError:  # not main thread (tests)
            pass
    counts = {"processed": 0, "duplicate": 0, "dead_letter": 0}
    holder: dict = {}
    since_commit, last_msg = 0, time.monotonic()
    try:
        while not stop["flag"]:
            if max_messages is not None and sum(counts.values()) >= max_messages:
                break
            HEARTBEAT.touch()  # liveness for the container healthcheck: the poll loop is running
            msg = c.poll(1.0)
            if msg is None:
                if since_commit:
                    c.commit(asynchronous=False)
                    since_commit = 0
                if idle_exit_s is not None and time.monotonic() - last_msg > idle_exit_s:
                    break
                continue
            kerr = msg.error()
            if kerr is not None:
                if kerr.code() != KafkaError._PARTITION_EOF:
                    log(logger, logging.ERROR, "kafka_error", error=str(kerr))
                continue
            last_msg = time.monotonic()
            cfg = replace(base, scorer=provider.get(), ml_threshold=settings.ml_threshold) if provider else base
            counts[_process_with_retry(holder, settings.database_url, msg, cfg)] += 1
            since_commit += 1
            if since_commit >= commit_every:
                c.commit(asynchronous=False)  # offsets are committed only AFTER the DB commit
                since_commit = 0
        if since_commit:
            c.commit(asynchronous=False)
    finally:
        c.close()
        if holder.get("conn"):
            holder["conn"].close()
    log(logger, logging.INFO, "consumer_stopped", **counts)
    return counts
