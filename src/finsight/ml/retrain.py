"""Gated retraining: produces a CANDIDATE only. Promotion is a separate, human-approved step."""
import psycopg

from .data import frame_from_db
from .monitoring import MonitorConfig, monitor, needs_retraining
from .registry import approved_version, configure
from .train import train_candidate


def retrain_from_db(conn: psycopg.Connection, tracking_uri: str, *, seed: int = 42, artifact_location=None,
                    actor="retrain", trigger="manual", xgb_params=None) -> dict:
    res = train_candidate(frame_from_db(conn), tracking_uri=tracking_uri, seed=seed, artifact_location=artifact_location,
                          conn=conn, actor=actor, xgb_params=xgb_params, tag=f"retrain-{trigger}")
    return {"candidate": res["label"], "accepted_by_criteria": res["report"]["acceptance"]["passed"],
            "reasons": res["report"]["acceptance"]["reasons"], "promoted": False}


def monitor_and_maybe_retrain(conn: psycopg.Connection, tracking_uri: str, cfg: MonitorConfig | None = None, *,
                              retrain_on_drift: bool = False, **kw) -> dict:
    client = configure(tracking_uri)
    v = approved_version(client)
    if v is None:
        return {"status": "no_approved_model"}
    out = monitor(conn, client, v, cfg)
    if retrain_on_drift and needs_retraining(out):
        try:
            out["retrain"] = retrain_from_db(conn, tracking_uri, trigger="drift", **kw)
        except ValueError as e:  # e.g. too few labeled positives to train/evaluate honestly
            out["retrain"] = {"candidate": None, "promoted": False, "error": str(e)}
    return out
