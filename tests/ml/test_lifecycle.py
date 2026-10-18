import numpy as np
import pytest

from finsight.events import make_event
from finsight.generator.seed import seed
from finsight.ml import registry
from finsight.ml.inference import ModelProvider, load_approved
from finsight.ml.registry import PromotionError
from finsight.ml.train import train_candidate
from finsight.pipeline.processor import Meta, ScoringConfig, process_event

from .conftest import FAST_XGB

pytestmark = pytest.mark.integration
_UNEVAL: list[int] = []


@pytest.fixture(scope="module")
def trained(mframe, tmp_path_factory):
    d = tmp_path_factory.mktemp("mlf")
    uri, art = f"sqlite:///{d}/mlflow.db", str(d / "artifacts")
    res = train_candidate(mframe, tracking_uri=uri, artifact_location=art, xgb_params=FAST_XGB)
    return uri, art, res


def test_training_reports_all_required_metrics_and_periods(trained):
    _, _, res = trained
    r = res["report"]
    assert set(r["models"]) == {"rules", "logreg", "xgb", "xgb_no_device"}
    for name in r["models"]:
        for split in ("validation", "holdout"):
            m = r["models"][name][split]
            for k in ("precision", "recall", "f1", "pr_auc", "false_positive_rate", "tp", "fp", "fn", "tn",
                      "precision_at_budget", "recall_at_budget", "expected_cost", "threshold"):
                assert k in m
    assert r["dataset_id"] and r["periods"]["holdout"] and r["cost_model"]["investigation_cost"] == 5.0
    assert r["models"]["xgb"]["validation"]["pr_auc"] > 0.1  # sanity: learns something on synthetic data


def test_training_is_reproducible(mframe, tmp_path):
    runs = []
    for i in range(2):
        uri = f"sqlite:///{tmp_path}/m{i}.db"
        r = train_candidate(mframe, tracking_uri=uri, artifact_location=str(tmp_path / f"a{i}"), xgb_params=FAST_XGB)
        runs.append(r["report"]["models"]["xgb"])
    assert runs[0] == runs[1]


def test_candidate_is_not_approved_and_unevaluated_cannot_be_promoted(trained):
    uri, _, res = trained
    client = registry.configure(uri)
    assert registry.approved_version(client) is None
    assert str(client.get_model_version_by_alias(registry.MODEL_NAME, "candidate").version) == res["version"]
    with pytest.raises(PromotionError, match="approver"):
        registry.promote(client, res["version"], approver="")
    # a version registered without any recorded evaluation cannot be promoted
    client.create_model_version(registry.MODEL_NAME, f"{client.get_model_version(registry.MODEL_NAME, res['version']).source}")
    raw = max(int(v.version) for v in client.search_model_versions(f"name='{registry.MODEL_NAME}'"))
    with pytest.raises(PromotionError, match="unevaluated"):
        registry.promote(client, raw, approver="analyst")
    with pytest.raises(PromotionError, match="unknown"):
        registry.promote(client, 999, approver="analyst")
    _UNEVAL.append(raw)
    assert registry.approved_version(client) is None


def test_failed_acceptance_blocks_promotion_then_pass_promotes_and_rolls_back(trained, conn):
    uri, _, res = trained
    client = registry.configure(uri)
    v1 = res["version"]
    registry.set_candidate(client, v1, {"passed": False, "reasons": ["forced failure"]}, conn)
    with pytest.raises(PromotionError, match="forced failure"):
        registry.promote(client, v1, "analyst", conn)
    registry.set_candidate(client, v1, {"passed": True, "reasons": ["ok"]}, conn)
    registry.promote(client, v1, "analyst", conn)
    assert registry.approved_version(client) == v1
    # second version -> promote -> rollback to first
    r2 = train_candidate(res["_frame"] if "_frame" in res else _frame(), tracking_uri=uri, xgb_params=FAST_XGB, conn=conn)
    registry.set_candidate(client, r2["version"], {"passed": True, "reasons": ["ok"]}, conn)
    registry.promote(client, r2["version"], "analyst", conn)
    assert registry.approved_version(client) == r2["version"]
    with pytest.raises(PromotionError, match="never approved"):
        registry.rollback(client, str(_UNEVAL[0]), "analyst", conn)
    registry.rollback(client, v1, "analyst", conn)
    assert registry.approved_version(client) == v1
    types = [r[0] for r in conn.execute("SELECT event_type FROM core.model_events ORDER BY event_id")]
    assert {"evaluated", "promoted", "promotion_refused", "rolled_back"} <= set(types)


def _frame():
    from finsight.generator import GeneratorConfig, generate
    from finsight.ml.data import frame_from_dataset
    return frame_from_dataset(generate(GeneratorConfig(seed=21, n_customers=250, days=30, fraud_prevalence=0.03)))


def test_online_scorer_uses_approved_model_and_falls_back_to_rules(trained, conn, mds):
    uri, _, res = trained
    client = registry.configure(uri)
    seed(conn, mds, entities_only=True)
    conn.commit()
    # nothing approved yet for a fresh registry copy? ensure rules-only fallback is explicit
    empty_uri = uri.replace("mlflow.db", "none.db")
    assert load_approved(empty_uri) is None and ModelProvider(empty_uri).get() is None
    registry.set_candidate(client, res["version"], {"passed": True, "reasons": ["ok"]})
    registry.promote(client, res["version"], "analyst")
    model = load_approved(uri)
    assert model.version == f"finsight-fraud/v{res['version']}"
    n_rules = n_ml = 0
    for t in mds.transactions[:600]:
        raw = make_event(t).model_dump_json().encode()
        process_event(conn, raw, Meta("t", 0, 0), ScoringConfig(scorer=model) if n_ml < 300 else None)
        n_ml += 1
    versions = dict(conn.execute("SELECT model_version, count(*) FROM core.model_predictions GROUP BY 1").fetchall())
    assert versions[model.version] == 300 and versions["rules-v1"] == 300
    ev = conn.execute("SELECT evidence FROM core.model_predictions WHERE model_version=%s LIMIT 1", (model.version,)).fetchone()[0]
    assert set(ev["ml"]["features"]) >= {"amount", "txn_count_10m"} and len(ev["ml"]["top_contributions"]) == 3
    assert n_rules == 0
    assert np.isfinite(model.threshold)
