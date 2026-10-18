import numpy as np
import pytest

from finsight.ml.evaluate import CostModel, best_threshold, evaluate


def test_metrics_hand_computed():
    y = np.array([1, 1, 0, 0, 0, 1])
    s = np.array([0.9, 0.4, 0.8, 0.1, 0.2, 0.7])
    amt = np.array([100, 200, 50, 10, 10, 300])
    m = evaluate(y, s, amt, 0.5, CostModel(investigation_cost=5, false_positive_friction=2, missed_fraud_loss_rate=1), alert_budget=0.5)
    assert (m.tp, m.fp, m.fn, m.tn) == (2, 1, 1, 2)
    assert m.precision == pytest.approx(2 / 3) and m.recall == pytest.approx(2 / 3)
    assert m.f1 == pytest.approx(2 / 3) and m.false_positive_rate == pytest.approx(1 / 3)
    # alert budget 50% of 6 = top 3 scores (0.9, 0.8, 0.7) -> 2 fraud
    assert m.precision_at_budget == pytest.approx(2 / 3) and m.recall_at_budget == pytest.approx(2 / 3)
    # cost = missed fraud 200 + 3 alerts * 5 + 1 FP * 2
    assert m.expected_cost == pytest.approx(200 + 15 + 2)
    assert m.pr_auc == pytest.approx((1 + 2 / 3 + 3 / 4) / 3)


def test_threshold_is_tuned_not_fixed_at_half():
    rng = np.random.default_rng(1)
    y = (rng.random(4000) < 0.03).astype(int)
    s = np.clip(rng.normal(0.05 + 0.25 * y, 0.05), 0, 1)  # all scores far below 0.5
    t = best_threshold(y, s, np.full(4000, 300.0))
    assert t < 0.5 and evaluate(y, s, np.full(4000, 300.0), t).recall > 0.5
    assert evaluate(y, s, np.full(4000, 300.0), 0.5).recall == 0.0


def test_no_positives_does_not_crash():
    m = evaluate(np.zeros(10, int), np.linspace(0, 1, 10), np.ones(10), 0.5)
    assert m.recall == 0.0 and m.pr_auc == 0.0
