import json
import uuid
from datetime import timedelta

import pytest
from confluent_kafka.admin import AdminClient, NewTopic
from fastapi.testclient import TestClient

from finsight.api.app import create_app
from finsight.config import Settings
from finsight.events import make_event
from finsight.generator import GeneratorConfig, generate
from finsight.generator.seed import seed
from finsight.pipeline.consumer import run_consumer
from finsight.pipeline.processor import Meta, ScoringConfig, process_event
from finsight.pipeline.producer import publish_dataset

pytestmark = pytest.mark.integration
META = Meta("t", 0, 1)


@pytest.fixture()
def seeded(conn, ds):
    seed(conn, ds, entities_only=True)
    conn.commit()
    return ds


def raw(txn):
    return make_event(txn).model_dump_json().encode()


def scenario_txns(ds, scenario):
    ids = {l.transaction_id for l in ds.labels if l.scenario == scenario and l.is_fraud}
    return [t for t in ds.transactions if t.transaction_id in ids]


def counts(conn):
    q = lambda t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: E731
    return {t: q(t) for t in ("core.transactions", "core.fraud_alerts", "core.model_predictions",
                              "core.processed_events", "core.dead_letter_events")}


def test_redelivery_creates_no_duplicates(conn, seeded):
    burst = scenario_txns(seeded, "velocity_burst")
    prior = [t for t in seeded.transactions if t.customer_id == burst[0].customer_id
             and t.event_time < burst[0].event_time]
    for t in prior + burst:
        assert process_event(conn, raw(t), META) == "processed"
    before = counts(conn)
    assert before["core.fraud_alerts"] >= 1
    for t in prior + burst:  # whole batch redelivered (e.g. crash before offset commit)
        assert process_event(conn, raw(t), META) == "duplicate"
    after = counts(conn)
    assert after == before
    dup = conn.execute("SELECT sum(duplicate_deliveries) FROM core.processed_events").fetchone()[0]
    assert dup == len(prior + burst)


def test_known_fraud_patterns_raise_expected_signals(conn, seeded):
    ds = seeded
    for t in ds.transactions:  # stream everything in event order
        process_event(conn, raw(t), META)
    sig_by_txn = {r[0]: {s["rule"] for s in r[1]} for r in conn.execute(
        "SELECT transaction_id, evidence->'signals' FROM core.model_predictions")}
    bursts = scenario_txns(ds, "velocity_burst")
    assert any("velocity" in sig_by_txn[t.transaction_id] for t in bursts)
    assert any("geo_inconsistency" in sig_by_txn[t.transaction_id] for t in scenario_txns(ds, "impossible_travel"))
    assert any("repeated_small_payments" in sig_by_txn[t.transaction_id] for t in scenario_txns(ds, "card_testing"))
    assert any("transfer_passthrough" in sig_by_txn[t.transaction_id] for t in scenario_txns(ds, "mule_ring"))
    # benign data still produces few alerts relative to volume
    n_alerts = conn.execute("SELECT count(*) FROM core.fraud_alerts").fetchone()[0]
    assert 0 < n_alerts < len(ds.transactions) * 0.1
    # alerts always carry model version + scoring timestamp
    assert conn.execute("SELECT count(*) FROM core.model_predictions WHERE model_version IS NULL "
                        "OR scored_at IS NULL").fetchone()[0] == 0


def test_malformed_and_unknown_entity_events_go_to_dead_letter(conn, seeded):
    t = seeded.transactions[0]
    assert process_event(conn, b"{not json", META) == "dead_letter"
    assert process_event(conn, b"\xff\xfe", META) == "dead_letter"
    assert process_event(conn, json.dumps({"event_id": "x"}), META) == "dead_letter"
    unknown = json.loads(raw(t))
    unknown["transaction"]["account_id"] = "acc_missing"
    assert process_event(conn, json.dumps(unknown), META) == "dead_letter"
    assert counts(conn)["core.dead_letter_events"] == 4
    assert counts(conn)["core.transactions"] == 0  # failed event left no partial rows


def test_threshold_is_configurable(conn, seeded):
    burst = scenario_txns(seeded, "velocity_burst")
    for t in burst:
        process_event(conn, raw(t), META, ScoringConfig(alert_threshold=0.999, model_version="rules-test"))
    assert counts(conn)["core.fraud_alerts"] == 0
    assert conn.execute("SELECT DISTINCT model_version FROM core.model_predictions").fetchall() == [("rules-test",)]


@pytest.fixture()
def kafka_env(conn, seeded):
    import os
    base, _, name = os.environ["DATABASE_URL"].rpartition("/")
    s = Settings(database_url=f"{base}/{name}_test", kafka_bootstrap=os.environ["KAFKA_BOOTSTRAP"])
    topic = f"test.{uuid.uuid4().hex[:8]}"
    admin = AdminClient({"bootstrap.servers": s.kafka_bootstrap})
    admin.create_topics([NewTopic(topic, 1, 1)])[topic].result(10)
    return s, topic


def test_end_to_end_stream_redelivery_restart_and_api(conn, kafka_env):
    s, topic = kafka_env
    ds = generate(GeneratorConfig(seed=7, n_customers=150, days=21, fraud_prevalence=0.03))
    n = publish_dataset(ds, s.kafka_bootstrap, topic, limit=1500)
    c1 = run_consumer(s, group_id="g1", topic=topic, max_messages=n, idle_exit_s=20)
    assert c1["processed"] == n
    # restart with the same group: offsets were committed -> nothing to re-read
    assert sum(run_consumer(s, group_id="g1", topic=topic, idle_exit_s=3).values()) == 0
    # broker redelivery (new group re-reads from start; simulates crash before offset commit)
    publish_dataset(ds, s.kafka_bootstrap, topic, limit=200)  # plus explicit duplicate publishes
    before = counts(conn)
    c2 = run_consumer(s, group_id="g2", topic=topic, max_messages=n + 200, idle_exit_s=20)
    assert c2["duplicate"] == n + 200 and c2["processed"] == 0
    assert counts(conn) == before
    # API reflects persisted data
    api = TestClient(create_app(s.database_url))
    stats = api.get("/pipeline/stats").json()
    assert stats["processed"] == n and stats["duplicate_redeliveries"] == n + 200
    page = api.get("/transactions", params={"limit": 10}).json()
    assert page["total"] == n and len(page["items"]) == 10 and page["items"][0]["model_version"] == "rules-v1"
    al = api.get("/alerts", params={"severity": "high"}).json()
    assert al["total"] == conn.execute("SELECT count(*) FROM core.fraud_alerts WHERE severity='high'").fetchone()[0]
    if al["items"]:
        d = api.get(f"/alerts/{al['items'][0]['alert_id']}").json()
        assert d["transaction"]["transaction_id"] == al["items"][0]["transaction_id"] and d["signals"]
    assert api.get("/alerts/999999").status_code == 404
    assert api.get("/transactions", params={"limit": 1000}).status_code == 422
    assert api.get("/transactions", params={"since": "garbage"}).status_code == 422
    assert api.get("/ready").status_code == 200 and api.get("/health").json()["status"] == "ok"
    r = api.get("/nope")
    assert r.status_code == 404 and r.json()["error"]["request_id"] == r.headers["x-request-id"]


def test_timestamps_distinguish_event_and_processing_time(conn, seeded):
    t = seeded.transactions[0]
    process_event(conn, raw(t), META)
    ev, ing = conn.execute("SELECT event_time, ingested_at FROM core.transactions").fetchone()
    assert ing - ev > timedelta(days=30)  # synthetic event time is in the past; ingestion time is "now"
