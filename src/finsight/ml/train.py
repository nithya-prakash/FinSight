"""Reproducible training + evaluation + registration of a CANDIDATE model.

Never promotes. Selection (threshold, acceptance) uses the validation period only; the holdout period is
evaluated once and reported."""
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

import mlflow
import mlflow.xgboost
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from ..logs import log
from .data import Frame, build_table, temporal_split, xy
from .evaluate import CostModel, best_threshold, evaluate
from .features import FEATURE_SET_VERSION, FEATURES, FORBIDDEN
from .inference import ScoringModel, load_model
from .registry import MODEL_NAME, approved_version, configure, label, set_candidate

logger = logging.getLogger("finsight.train")
RULES_PROD_THRESHOLD = 0.5
DEFAULT_XGB = dict(n_estimators=250, max_depth=4, learning_rate=0.08, subsample=0.8, colsample_bytree=0.8,
                   min_child_weight=2, eval_metric="aucpr", tree_method="hist", n_jobs=2)


def _period(df):
    return [df.event_time.min().isoformat(), df.event_time.max().isoformat()] if len(df) else None


def accept(cand_val: dict, ref_val: dict, ref_name: str, leakage_ok: bool, holdout_recorded: bool) -> dict:
    """Documented acceptance criteria (validation period, same data for candidate and reference)."""
    reasons = []
    if not leakage_ok:
        reasons.append("leakage check failed")
    if not holdout_recorded:
        reasons.append("holdout evaluation not recorded")
    if cand_val["expected_cost"] > ref_val["expected_cost"]:
        reasons.append(f"expected cost {cand_val['expected_cost']:.0f} > {ref_name} {ref_val['expected_cost']:.0f}")
    if cand_val["pr_auc"] < ref_val["pr_auc"] - 0.01:
        reasons.append(f"PR-AUC {cand_val['pr_auc']:.3f} < {ref_name} {ref_val['pr_auc']:.3f} - 0.01")
    return {"passed": not reasons, "reasons": reasons or [f"meets criteria vs {ref_name}"], "reference": ref_name}


def train_candidate(frame: Frame, *, tracking_uri: str, seed: int = 42, artifact_location: str | None = None,
                    conn=None, actor: str = "trainer", xgb_params: dict | None = None,
                    cost: CostModel | None = None, tag: str | None = None) -> dict:
    cost = cost or CostModel()
    client = configure(tracking_uri)
    exp = client.get_experiment_by_name("finsight-fraud")
    exp_id = exp.experiment_id if exp else client.create_experiment("finsight-fraud", artifact_location=artifact_location)

    df = build_table(frame)
    tr, va, ho = temporal_split(df)
    if tr.label.sum() < 5 or va.label.sum() < 3 or ho.label.sum() < 3:
        raise ValueError("not enough positives per period to train and evaluate reliably")
    leakage_ok = not (FORBIDDEN & set(FEATURES))
    Xtr, ytr = xy(tr)
    Xva, yva = xy(va)
    Xho, yho = xy(ho)
    params = {**DEFAULT_XGB, **(xgb_params or {}), "random_state": seed,
              "scale_pos_weight": float((ytr == 0).sum() / max(1, (ytr == 1).sum()))}

    xgbm = XGBClassifier(**params).fit(Xtr, ytr)
    lr = make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=2000, random_state=seed)).fit(Xtr, ytr)
    nodev = [f for f in FEATURES if f != "device_known"]
    xgb_nodev = XGBClassifier(**params).fit(tr[nodev].to_numpy(float), ytr)

    scores = {"xgb": (xgbm.predict_proba(Xva)[:, 1], xgbm.predict_proba(Xho)[:, 1]),
              "logreg": (lr.predict_proba(Xva)[:, 1], lr.predict_proba(Xho)[:, 1]),
              "xgb_no_device": (xgb_nodev.predict_proba(va[nodev].to_numpy(float))[:, 1],
                                xgb_nodev.predict_proba(ho[nodev].to_numpy(float))[:, 1]),
              "rules": (va.rules_score.to_numpy(), ho.rules_score.to_numpy())}
    thr = {"rules": RULES_PROD_THRESHOLD}
    for name in ("xgb", "logreg", "xgb_no_device"):
        thr[name] = best_threshold(yva, scores[name][0], va.amount.to_numpy(), cost)  # tuned on validation only
    report: dict[str, Any] = {"dataset_id": frame.dataset_id, "feature_set_version": FEATURE_SET_VERSION, "seed": seed,
              "periods": {"train": _period(tr), "validation": _period(va), "holdout": _period(ho)},
              "label_maturity_gap_hours": 24, "cost_model": cost.model_dump(), "models": {}}
    for name, (sv, sh) in scores.items():
        report["models"][name] = {
            "threshold": thr[name],
            "validation": evaluate(yva, sv, va.amount, thr[name], cost).dict(),
            "holdout": evaluate(yho, sh, ho.amount, thr[name], cost).dict()}

    ref_name, ref_val = "rules-v1 baseline", report["models"]["rules"]["validation"]
    approved = approved_version(client)
    if approved:  # compare against the currently approved model on THIS validation period
        cur = load_model(client, approved)
        try:
            ps = cur.predict_proba(va.to_dict("records"))
            ref_name = cur.version
            ref_val = evaluate(yva, ps, va.amount, cur.threshold, cost).dict()
            report["approved_reference"] = {"version": cur.version, "validation": ref_val}
        except KeyError:
            pass
    acceptance = accept(report["models"]["xgb"]["validation"], ref_val, ref_name, leakage_ok, True)
    report["acceptance"] = acceptance

    ref_sample = tr.sample(min(5000, len(tr)), random_state=seed)[FEATURES].copy()
    ref_sample["score"] = xgbm.predict_proba(ref_sample.to_numpy(float))[:, 1]
    with mlflow.start_run(experiment_id=exp_id, run_name=tag or f"train-{frame.dataset_id}") as run:
        mlflow.log_params({**{k: v for k, v in params.items()}, "dataset_id": frame.dataset_id, "feature_set": FEATURE_SET_VERSION,
                           "n_train": len(tr), "n_val": len(va), "n_holdout": len(ho), "seed": seed})
        for name, m in report["models"].items():
            for split in ("validation", "holdout"):
                for k, v in m[split].items():
                    if isinstance(v, (int, float)):
                        mlflow.log_metric(f"{name}_{split}_{k}", float(v))
        mlflow.log_dict(report, "evaluation.json")
        mlflow.log_dict({"features": FEATURES, "feature_set_version": FEATURE_SET_VERSION}, "features.json")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "reference.csv.gz"
            ref_sample.to_csv(p, index=False)
            mlflow.log_artifact(str(p))
        info = mlflow.xgboost.log_model(xgbm, name="model", registered_model_name=MODEL_NAME)
        version = str(info.registered_model_version)
        run_id = run.info.run_id
    for k, v in {"features": json.dumps(FEATURES), "threshold": str(thr["xgb"]), "run_id": run_id,
                 "dataset_id": frame.dataset_id, "feature_set_version": FEATURE_SET_VERSION,
                 "val_pr_auc": str(report["models"]["xgb"]["validation"]["pr_auc"])}.items():
        client.set_model_version_tag(MODEL_NAME, version, k, v)
    set_candidate(client, version, acceptance, conn, actor)
    from .registry import record_event
    record_event(conn, "registered", version, actor, {"run_id": run_id, "dataset_id": frame.dataset_id})
    log(logger, logging.INFO, "candidate_registered", version=label(version), accepted=acceptance["passed"])
    return {"version": version, "label": label(version), "run_id": run_id, "report": report, "model": ScoringModel(xgbm, FEATURES, thr["xgb"], label(version))}
