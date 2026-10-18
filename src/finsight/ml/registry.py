"""MLflow model registry with gated promotion.

* New versions only ever get the `candidate` alias.
* `approved` (what the streaming scorer loads) changes only through `promote()`, which requires a
  passing, recorded evaluation AND a named human approver. `rollback()` returns to a version that
  was approved before. Every action is written to core.model_events.
"""
import json

import mlflow
import psycopg
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

MODEL_NAME = "finsight-fraud"


class PromotionError(Exception):
    pass


def configure(tracking_uri: str) -> MlflowClient:
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_registry_uri(tracking_uri)
    return MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri)


def label(version: str | int) -> str:
    return f"{MODEL_NAME}/v{version}"


def record_event(conn: psycopg.Connection | None, event_type: str, version: str | int, actor: str,
                 detail: dict) -> None:
    if conn is None:
        return
    conn.execute("INSERT INTO core.model_events (event_type, model_name, model_version, actor, detail) "
                 "VALUES (%s,%s,%s,%s,%s::jsonb)", (event_type, MODEL_NAME, label(version), actor, json.dumps(detail)))
    if not conn.autocommit:
        conn.commit()


def _get(client: MlflowClient, version: str):
    try:
        return client.get_model_version(MODEL_NAME, version)
    except MlflowException as e:
        raise PromotionError(f"unknown model version {version}") from e


def approved_version(client: MlflowClient) -> str | None:
    try:
        return str(client.get_model_version_by_alias(MODEL_NAME, "approved").version)
    except MlflowException:
        return None


def set_candidate(client: MlflowClient, version: str, acceptance: dict, conn=None, actor="trainer") -> None:
    client.set_model_version_tag(MODEL_NAME, version, "acceptance", "pass" if acceptance["passed"] else "fail")
    client.set_model_version_tag(MODEL_NAME, version, "acceptance_reasons", json.dumps(acceptance["reasons"])[:4000])
    client.set_registered_model_alias(MODEL_NAME, "candidate", version)
    record_event(conn, "evaluated", version, actor, acceptance)


def promote(client: MlflowClient, version: str | int, approver: str | None, conn=None) -> None:
    version = str(version)
    if not approver or not approver.strip():
        record_event(conn, "promotion_refused", version, "unknown", {"reason": "no human approver"})
        raise PromotionError("promotion requires a named human approver")
    mv = _get(client, version)
    tags = mv.tags or {}
    if tags.get("acceptance") != "pass":
        why = tags.get("acceptance_reasons", "no recorded evaluation")
        record_event(conn, "promotion_refused", version, approver, {"reason": "acceptance criteria not met", "detail": why})
        raise PromotionError(f"v{version} has not passed evaluation ({tags.get('acceptance', 'unevaluated')}): {why}")
    prev = approved_version(client)
    client.set_registered_model_alias(MODEL_NAME, "approved", version)
    client.set_model_version_tag(MODEL_NAME, version, "was_approved", "1")
    record_event(conn, "promoted", version, approver, {"previous_approved": prev})


def rollback(client: MlflowClient, to_version: str | int, approver: str | None, conn=None) -> None:
    to_version = str(to_version)
    if not approver or not approver.strip():
        raise PromotionError("rollback requires a named human approver")
    mv = _get(client, to_version)
    if (mv.tags or {}).get("was_approved") != "1":
        raise PromotionError(f"v{to_version} was never approved; cannot roll back to it")
    prev = approved_version(client)
    client.set_registered_model_alias(MODEL_NAME, "approved", to_version)
    record_event(conn, "rolled_back", to_version, approver, {"from": prev})
