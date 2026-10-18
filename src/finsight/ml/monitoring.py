"""Drift monitoring with Evidently. Three separate signals:
  * feature drift      - input distributions moved vs the training reference
  * prediction drift   - the score distribution moved
  * performance        - only when matured labels exist; the only signal that shows real degradation
Drift alone does NOT prove the model is wrong; it triggers a retraining *candidate*, never deployment."""
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg
from pydantic import BaseModel

from ..logs import log
from .evaluate import evaluate
from .features import FEATURES
from .registry import MODEL_NAME

logger = logging.getLogger("finsight.monitoring")


class MonitorConfig(BaseModel):
    window: int = 2000                 # most recent scored transactions
    min_current: int = 200
    p_value: float = 0.01              # K-S significance per column
    drift_share: float = 0.3           # share of drifted features that flags feature drift
    min_labeled_positives: int = 20
    performance_drop: float = 0.25     # relative PR-AUC drop vs validation that flags degradation
    out_of_range_share: float = 0.05
    html_dir: str | None = None


def load_reference(client, version: str) -> pd.DataFrame:
    run_id = client.get_model_version(MODEL_NAME, version).tags["run_id"]
    with tempfile.TemporaryDirectory() as d:
        p = client.download_artifacts(run_id, "reference.csv.gz", d)
        return pd.read_csv(p)


def current_window(conn: psycopg.Connection, model_label: str, n: int) -> pd.DataFrame:
    rows = conn.execute(
        "SELECT mp.transaction_id, mp.risk_score, mp.evidence->'ml'->'features', t.event_time, l.is_fraud "
        "FROM core.model_predictions mp JOIN core.transactions t USING (transaction_id) "
        "LEFT JOIN eval.fraud_labels l USING (transaction_id) "
        "WHERE mp.model_version=%s AND mp.evidence ? 'ml' ORDER BY t.event_time DESC, mp.transaction_id DESC LIMIT %s",
        (model_label, n)).fetchall()
    recs = [{**r[2], "score": float(r[1]), "transaction_id": r[0], "event_time": r[3], "label": r[4]} for r in rows]
    return pd.DataFrame(recs)


def data_quality(cur: pd.DataFrame, ref: pd.DataFrame, cfg: MonitorConfig) -> list[dict]:
    issues = []
    for c in FEATURES:
        bad = int((~np.isfinite(cur[c].to_numpy(float))).sum())
        if bad:
            issues.append({"feature": c, "issue": "non_finite_values", "count": bad})
        lo, hi = ref[c].min(), ref[c].max()
        share = float(((cur[c] < lo) | (cur[c] > hi)).mean())
        if share > cfg.out_of_range_share:
            issues.append({"feature": c, "issue": "outside_training_range", "share": round(share, 4)})
    return issues


def run_drift(ref: pd.DataFrame, cur: pd.DataFrame, cfg: MonitorConfig) -> dict:
    from evidently import DataDefinition, Dataset, Report
    from evidently.presets import DataDriftPreset
    cols = FEATURES + ["score"]
    dd = DataDefinition(numerical_columns=cols)
    snap = Report([DataDriftPreset(method="ks", threshold=cfg.p_value)]).run(
        Dataset.from_pandas(cur[cols], data_definition=dd), Dataset.from_pandas(ref[cols], data_definition=dd))
    pvals: dict[str, float] = {}
    for m in snap.dict()["metrics"]:
        c = m["config"]
        if c.get("type", "").endswith("ValueDrift"):
            pvals[c["column"]] = float(m["value"])
    drifted = sorted(f for f in FEATURES if pvals.get(f, 1.0) < cfg.p_value)
    return {"pvalues": pvals, "drifted_features": drifted, "feature_drift": len(drifted) / len(FEATURES) >= cfg.drift_share,
            "prediction_drift": pvals.get("score", 1.0) < cfg.p_value, "snapshot": snap}


def performance(cur: pd.DataFrame, threshold: float, val_pr_auc: float, cfg: MonitorConfig) -> dict | None:
    lab = cur[cur.label.notna()]
    if int(lab.label.sum()) < cfg.min_labeled_positives:
        return None  # not enough matured labels: say so instead of guessing
    m = evaluate(lab.label.astype(int), lab.score, lab.amount if "amount" in lab else np.ones(len(lab)), threshold)
    out = m.dict()
    out["degraded"] = bool(m.pr_auc < val_pr_auc * (1 - cfg.performance_drop))
    out["reference_val_pr_auc"] = val_pr_auc
    return out


def monitor(conn: psycopg.Connection, client, version: str, cfg: MonitorConfig | None = None) -> dict:
    cfg = cfg or MonitorConfig()
    tags = client.get_model_version(MODEL_NAME, version).tags
    label = f"{MODEL_NAME}/v{version}"
    ref = load_reference(client, version)
    cur = current_window(conn, label, cfg.window)
    if len(cur) < cfg.min_current:
        return {"status": "insufficient_data", "n_current": len(cur), "min_current": cfg.min_current}
    amounts: dict[str, Any] = dict(conn.execute("SELECT transaction_id, amount FROM core.transactions WHERE transaction_id = ANY(%s)",
                                (list(cur.transaction_id),)).fetchall())
    cur["amount"] = cur.transaction_id.map(lambda i: float(amounts[i]))
    d = run_drift(ref, cur, cfg)
    perf = performance(cur, float(tags["threshold"]), float(tags["val_pr_auc"]), cfg)
    issues = data_quality(cur, ref, cfg)
    html = None
    if cfg.html_dir:
        Path(cfg.html_dir).mkdir(parents=True, exist_ok=True)
        html = str(Path(cfg.html_dir) / f"drift_v{version}.html")
        d["snapshot"].save_html(html)
    result = {"status": "ok", "model_version": label, "n_current": len(cur), "feature_drift": d["feature_drift"],
              "drifted_features": d["drifted_features"], "prediction_drift": d["prediction_drift"],
              "data_quality_issues": issues, "performance": perf,
              "performance_degraded": None if perf is None else perf["degraded"], "html_report": html,
              "note": "Drift does not by itself show the model is inaccurate; only labeled performance does."}
    conn.execute(
        "INSERT INTO core.monitoring_runs (model_version, window_start, window_end, n_current, feature_drift, drifted_features, "
        "prediction_drift, data_quality_issues, performance, performance_degraded, config) "
        "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb)",
        (label, cur.event_time.min(), cur.event_time.max(), len(cur), d["feature_drift"], json.dumps(d["drifted_features"]),
         d["prediction_drift"], json.dumps(issues), json.dumps(perf), result["performance_degraded"], cfg.model_dump_json()))
    if not conn.autocommit:
        conn.commit()
    log(logger, logging.INFO, "monitor_run", **{k: result[k] for k in ("feature_drift", "prediction_drift", "performance_degraded")})
    return result


def needs_retraining(result: dict) -> bool:
    """Any signal can ask for a *candidate*; promotion still requires evaluation + human approval."""
    return result.get("status") == "ok" and bool(
        result["feature_drift"] or result["prediction_drift"] or result["performance_degraded"])


__all__ = ["MonitorConfig", "monitor", "needs_retraining", "run_drift"]
