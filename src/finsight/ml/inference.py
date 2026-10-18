"""Online scoring with a registered model. Offline training and online inference are separate code paths
that share only `featurize` (see features.py)."""
import json
import logging
import time

import mlflow
import mlflow.xgboost
import numpy as np
import xgboost as xgb

from ..logs import log
from ..scoring.rules import Context
from .features import featurize
from .registry import MODEL_NAME, approved_version, configure, label

logger = logging.getLogger("finsight.inference")


class ScoringModel:
    def __init__(self, model, features: list[str], threshold: float, version: str):
        self.model, self.features, self.threshold, self.version = model, features, threshold, version

    def matrix(self, rows: list[dict]) -> np.ndarray:
        return np.array([[r[f] for f in self.features] for r in rows], dtype=float)

    def predict_proba(self, rows: list[dict]) -> np.ndarray:
        return self.model.predict_proba(self.matrix(rows))[:, 1]

    def score(self, ctx: Context) -> tuple[float, dict]:
        feats = featurize(ctx)
        x = self.matrix([feats])
        p = float(self.model.predict_proba(x)[0, 1])
        contrib = self.model.get_booster().predict(xgb.DMatrix(x, feature_names=self.features), pred_contribs=True)[0][:-1]
        top = sorted(zip(self.features, contrib, strict=True), key=lambda kv: -abs(kv[1]))[:3]
        return round(p, 6), {"features": {k: round(v, 6) for k, v in feats.items()},
                             "top_contributions": [{"feature": k, "contribution": round(float(c), 4)} for k, c in top]}


def load_model(client, version: str) -> ScoringModel:
    mv = client.get_model_version(MODEL_NAME, version)
    model = mlflow.xgboost.load_model(f"models:/{MODEL_NAME}/{version}")
    tags = mv.tags or {}
    return ScoringModel(model, json.loads(tags["features"]), float(tags["threshold"]), label(version))


def load_approved(tracking_uri: str) -> ScoringModel | None:
    client = configure(tracking_uri)
    v = approved_version(client)
    return load_model(client, v) if v else None


class ModelProvider:
    """Serves the approved model; re-checks the registry periodically. If none is approved or the
    registry is unreachable, returns None and the caller scores with the explicit rules-only baseline."""

    def __init__(self, tracking_uri: str | None, refresh_s: float = 60.0):
        self.uri, self.refresh_s = tracking_uri, refresh_s
        self._model: ScoringModel | None = None
        self._checked = 0.0

    def get(self) -> ScoringModel | None:
        if not self.uri or time.monotonic() - self._checked < self.refresh_s:
            return self._model
        self._checked = time.monotonic()
        try:
            client = configure(self.uri)
            v = approved_version(client)
            if v is None:
                if self._model:
                    log(logger, logging.WARNING, "no_approved_model_falling_back_to_rules")
                self._model = None
            elif self._model is None or self._model.version != label(v):
                self._model = load_model(client, v)
                log(logger, logging.INFO, "model_loaded", version=self._model.version, threshold=self._model.threshold)
        except Exception as e:  # registry down: keep serving the last good model, else rules-only
            log(logger, logging.ERROR, "registry_unavailable", error=str(e)[:150], serving=getattr(self._model, "version", "rules-only"))
        return self._model
