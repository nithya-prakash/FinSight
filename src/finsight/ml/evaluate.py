"""Imbalance-aware evaluation with an explicit, configurable cost model."""
from dataclasses import asdict, dataclass

import numpy as np
from pydantic import BaseModel
from sklearn.metrics import average_precision_score


class CostModel(BaseModel):
    investigation_cost: float = 5.0      # EUR per alert reviewed (true or false)
    false_positive_friction: float = 2.0  # EUR customer-friction cost per false alert
    missed_fraud_loss_rate: float = 1.0  # share of a missed fraud amount that is lost


@dataclass
class Metrics:
    n: int
    positives: int
    threshold: float
    precision: float
    recall: float
    f1: float
    false_positive_rate: float
    pr_auc: float
    tp: int
    fp: int
    fn: int
    tn: int
    alert_budget: float
    precision_at_budget: float
    recall_at_budget: float
    expected_cost: float

    def dict(self):
        return asdict(self)


def _safe(a, b):
    return float(a / b) if b else 0.0


def expected_cost(y, amount, flagged, cost: CostModel) -> float:
    fn_loss = float((amount * (y == 1) * (~flagged)).sum()) * cost.missed_fraud_loss_rate
    n_flag = int(flagged.sum())
    fp = int(((y == 0) & flagged).sum())
    return fn_loss + n_flag * cost.investigation_cost + fp * cost.false_positive_friction


def evaluate(y, score, amount, threshold: float, cost: CostModel | None = None,
             alert_budget: float = 0.01) -> Metrics:
    cost = cost or CostModel()
    y, score, amount = np.asarray(y), np.asarray(score, float), np.asarray(amount, float)
    flagged = score >= threshold
    tp = int(((y == 1) & flagged).sum())
    fp = int(((y == 0) & flagged).sum())
    fn = int(((y == 1) & ~flagged).sum())
    tn = int(((y == 0) & ~flagged).sum())
    p, r = _safe(tp, tp + fp), _safe(tp, tp + fn)
    k = max(1, int(round(alert_budget * len(y))))
    top = np.argsort(-score, kind="stable")[:k]
    tp_k = int(y[top].sum())
    return Metrics(
        n=len(y), positives=int(y.sum()), threshold=float(threshold), precision=p, recall=r,
        f1=_safe(2 * p * r, p + r), false_positive_rate=_safe(fp, fp + tn),
        pr_auc=float(average_precision_score(y, score)) if y.sum() else 0.0,
        tp=tp, fp=fp, fn=fn, tn=tn, alert_budget=alert_budget,
        precision_at_budget=_safe(tp_k, k), recall_at_budget=_safe(tp_k, int(y.sum())),
        expected_cost=expected_cost(y, amount, flagged, cost))


def best_threshold(y, score, amount, cost: CostModel | None = None) -> float:
    """Threshold minimising expected cost on the given (validation) data; not assumed to be 0.5."""
    cost = cost or CostModel()
    y, score, amount = np.asarray(y), np.asarray(score, float), np.asarray(amount, float)
    cands = np.unique(np.concatenate([np.quantile(score, np.linspace(0.5, 0.999, 120)), [0.5]]))
    costs = [expected_cost(y, amount, score >= t, cost) for t in cands]
    return float(cands[int(np.argmin(costs))])


def pr_curve_points(y, score, amount, thresholds, cost: CostModel | None = None) -> list[dict]:
    return [evaluate(y, score, amount, t, cost).dict() for t in thresholds]
