import numpy as np
import pandas as pd
import pytest

from finsight.ml.features import FEATURES
from finsight.ml.monitoring import MonitorConfig, needs_retraining, performance, run_drift


def frames(shift_feature=None, shift_score=False, n=800, seed=0):
    rng = np.random.default_rng(seed)
    mk = lambda: pd.DataFrame({**{f: rng.normal(10, 2, n) for f in FEATURES}, "score": rng.beta(1, 30, n)})  # noqa: E731
    ref, cur = mk(), mk()
    if shift_feature == "all":
        for f in FEATURES:
            cur[f] += 5
    elif shift_feature:
        cur[shift_feature] += 5
    if shift_score:
        cur["score"] = rng.beta(4, 10, n)
    return ref, cur


def test_no_drift_on_identically_distributed_data():
    d = run_drift(*frames(), MonitorConfig())
    assert not d["feature_drift"] and not d["prediction_drift"]


def test_single_feature_shift_is_reported_but_below_share_threshold():
    d = run_drift(*frames("amount"), MonitorConfig())
    assert d["drifted_features"] == ["amount"] and not d["feature_drift"]  # 1/26 < 30%


def test_broad_feature_drift_and_prediction_drift_are_separate_signals():
    d = run_drift(*frames("all"), MonitorConfig())
    assert d["feature_drift"] and not d["prediction_drift"]
    d2 = run_drift(*frames(shift_score=True), MonitorConfig())
    assert d2["prediction_drift"] and not d2["feature_drift"]


def test_drift_threshold_is_configurable():
    d = run_drift(*frames("amount"), MonitorConfig(drift_share=0.02))
    assert d["feature_drift"]


def test_performance_requires_labels_and_flags_degradation():
    rng = np.random.default_rng(3)
    cur = pd.DataFrame({"score": rng.random(500), "label": [np.nan] * 500, "amount": 100.0})
    assert performance(cur, 0.5, 0.8, MonitorConfig()) is None  # no labels -> no claim about accuracy
    y = (rng.random(500) < 0.2).astype(int)
    cur["label"] = y
    cur["score"] = np.where(y == 1, rng.random(500) * 0.6 + 0.2, rng.random(500) * 0.6)  # weak model
    p = performance(cur, 0.5, 0.95, MonitorConfig())
    assert p is not None and p["degraded"] is True
    assert needs_retraining({"status": "ok", "feature_drift": False, "prediction_drift": False, "performance_degraded": True})
    assert not needs_retraining({"status": "ok", "feature_drift": False, "prediction_drift": False, "performance_degraded": None})


@pytest.mark.integration
def test_drift_triggers_candidate_never_promotion(conn, mds, tmp_path):
    """End to end: approved model scores a stream; shifted traffic -> drift -> retrain candidate; approved unchanged."""
    from finsight.events import make_event
    from finsight.generator import GeneratorConfig, generate
    from finsight.generator.seed import seed
    from finsight.ml import registry
    from finsight.ml.data import frame_from_dataset
    from finsight.ml.inference import load_approved
    from finsight.ml.retrain import monitor_and_maybe_retrain
    from finsight.ml.train import train_candidate
    from finsight.pipeline.processor import Meta, ScoringConfig, process_event
    uri, art = f"sqlite:///{tmp_path}/m.db", str(tmp_path / "a")
    ds = generate(GeneratorConfig(seed=21, n_customers=250, days=30, fraud_prevalence=0.03))
    res = train_candidate(frame_from_dataset(ds), tracking_uri=uri, artifact_location=art, xgb_params={"n_estimators": 40, "max_depth": 3})
    client = registry.configure(uri)
    registry.set_candidate(client, res["version"], {"passed": True, "reasons": ["ok"]})
    registry.promote(client, res["version"], "analyst")
    model = load_approved(uri)
    seed(conn, ds, entities_only=True)
    conn.commit()
    # inflate amounts in the live stream => feature + prediction drift vs training reference
    for t in ds.transactions:
        t2 = t.model_copy(update={"amount": t.amount * 8})
        process_event(conn, make_event(t2).model_dump_json().encode(), Meta("t", 0, 0), ScoringConfig(scorer=model))
    out = monitor_and_maybe_retrain(conn, uri, MonitorConfig(min_current=200), retrain_on_drift=True,
                                    artifact_location=art, xgb_params={"n_estimators": 40, "max_depth": 3})
    assert out["feature_drift"] and "amount" in out["drifted_features"]
    assert out["performance"] is not None or out["performance_degraded"] is None
    assert out["retrain"]["candidate"] != model.version and out["retrain"]["promoted"] is False
    assert out["retrain"]["candidate"] is not None
    assert registry.approved_version(client) == res["version"]       # production model untouched
    assert conn.execute("SELECT count(*) FROM core.monitoring_runs").fetchone()[0] == 1
