"""Consumer lag (real Redpanda), heartbeat healthcheck, latency and data-quality metrics."""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg
import pytest
import yaml
from confluent_kafka.admin import AdminClient, NewTopic
from prometheus_client import REGISTRY

from finsight.config import Settings
from finsight.events import make_event
from finsight.generator import GeneratorConfig, generate
from finsight.generator.seed import seed
from finsight.pipeline import consumer as consumer_module
from finsight.pipeline.consumer import run_consumer
from finsight.pipeline.lag import consumer_lag
from finsight.pipeline.processor import Meta, process_event
from finsight.pipeline.producer import publish_dataset

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.fixture()
def kafka(conn):
    base, _, name = os.environ["DATABASE_URL"].rpartition("/")
    s = Settings(database_url=f"{base}/{name}_test", kafka_bootstrap=os.environ["KAFKA_BOOTSTRAP"])
    topic = f"lag.{uuid.uuid4().hex[:8]}"
    admin = AdminClient({"bootstrap.servers": s.kafka_bootstrap})
    admin.create_topics([NewTopic(topic, 1, 1)])[topic].result(10)
    ds = generate(GeneratorConfig(seed=5, n_customers=60, days=10, fraud_prevalence=0.03))
    seed(conn, ds, entities_only=True)
    conn.commit()
    return s, topic, ds


def test_consumer_lag_is_measured_from_the_broker(kafka):
    s, topic, ds = kafka
    group = f"g-{uuid.uuid4().hex[:6]}"
    publish_dataset(ds, s.kafka_bootstrap, topic, limit=120)
    lag = consumer_lag(s.kafka_bootstrap, group, topic)
    assert lag["total"] == 120 and lag["partitions"]["0"]["end"] == 120  # nothing consumed yet
    run_consumer(s, group_id=group, topic=topic, max_messages=50, idle_exit_s=10, commit_every=10)
    assert consumer_lag(s.kafka_bootstrap, group, topic)["total"] == 70
    run_consumer(s, group_id=group, topic=topic, idle_exit_s=5)
    assert consumer_lag(s.kafka_bootstrap, group, topic)["total"] == 0
    assert sample("finsight_consumer_lag", partition="0") == 0
    with pytest.raises(RuntimeError):
        consumer_lag(s.kafka_bootstrap, group, "no.such.topic.anywhere", timeout=3)


def test_heartbeat_is_written_and_healthcheck_detects_stale_or_missing(
    kafka, tmp_path, monkeypatch
):
    s, topic, _ = kafka
    hb = tmp_path / "hb"
    monkeypatch.setattr(consumer_module, "HEARTBEAT", hb)
    run_consumer(s, group_id=f"g-{uuid.uuid4().hex[:6]}", topic=topic, idle_exit_s=2)
    assert hb.exists() and time.time() - hb.stat().st_mtime < 30
    cmd = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["fraud-consumer"][
        "healthcheck"
    ]["test"]
    assert cmd[0] == "CMD" and cmd[1] == "python"
    code = cmd[3].replace(
        "/tmp/consumer.heartbeat", str(hb)
    )  # the exact probe from compose, pointed at the test file
    probe = lambda: subprocess.run([sys.executable, "-c", code]).returncode  # noqa: E731
    assert probe() == 0
    old = time.time() - 300
    os.utime(hb, (old, old))
    assert probe() == 1  # stale heartbeat => unhealthy
    hb.unlink()
    assert probe() != 0  # missing heartbeat => unhealthy


def test_scoring_latency_and_data_quality_metrics(conn, kafka):
    _, _, ds = kafka
    before_n = sample("finsight_scoring_seconds_count")
    before_dq = sample("finsight_data_quality_failures_total", kind="malformed")
    before_ev = sample("finsight_consumer_events_total", outcome="processed")
    for t in ds.transactions[:20]:
        process_event(conn, make_event(t).model_dump_json().encode(), Meta("t", 0, 0))
    process_event(conn, b"{broken", Meta("t", 0, 1))
    bad = make_event(ds.transactions[21]).model_dump(mode="json")
    bad["transaction"]["account_id"] = "acc_missing"
    process_event(conn, __import__("json").dumps(bad), Meta("t", 0, 2))
    assert sample("finsight_scoring_seconds_count") - before_n == 20
    assert sample("finsight_consumer_events_total", outcome="processed") - before_ev == 20
    assert sample("finsight_data_quality_failures_total", kind="malformed") - before_dq == 1
    assert sample("finsight_data_quality_failures_total", kind="db_rejected") >= 1
    assert sample("finsight_scoring_seconds_sum") > 0
    process_event(
        conn, make_event(ds.transactions[0]).model_dump_json().encode(), Meta("t", 0, 3)
    )  # duplicate
    assert sample("finsight_consumer_events_total", outcome="duplicate") >= 1


def test_summary_and_metrics_endpoints_expose_lag_latency_and_quality(conn, kafka, monkeypatch):
    from fastapi.testclient import TestClient

    from finsight.api.app import create_app

    s, topic, ds = kafka
    for t in ds.transactions[:30]:
        process_event(conn, make_event(t).model_dump_json().encode(), Meta("t", 0, 0))
    process_event(conn, b"x", Meta("t", 0, 1))
    conn.commit()
    monkeypatch.setenv("DATABASE_URL", s.database_url)
    monkeypatch.setenv("KAFKA_BOOTSTRAP", s.kafka_bootstrap)
    monkeypatch.setenv("KAFKA_TOPIC", topic)
    monkeypatch.setenv("CONSUMER_GROUP", f"g-{uuid.uuid4().hex[:6]}")
    publish_dataset(ds, s.kafka_bootstrap, topic, limit=15)
    api = TestClient(create_app(s.database_url))
    p = api.get("/observability/summary").json()["pipeline"]
    assert (
        p["consumer_lag"]["total"] == 15
        and p["scoring_ms_p95"] is not None
        and p["data_quality_failures_last_hour"] == 1
    )
    body = api.get("/metrics").text
    assert (
        "finsight_consumer_lag" in body
        and "finsight_scoring_seconds_bucket" in body
        and "finsight_data_quality_failures_total" in body
    )
    assert psycopg  # noqa: B018
