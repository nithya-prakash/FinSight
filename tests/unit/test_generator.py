from collections import defaultdict

import pytest
from pydantic import ValidationError

from finsight.generator import GeneratorConfig, generate
from finsight.generator.build import geo_speed_kmh
from finsight.generator.models import Transaction
from finsight.generator.validate import validate


def by_scenario(ds):
    g = defaultdict(lambda: defaultdict(list))
    tx = {t.transaction_id: t for t in ds.transactions}
    for l in ds.labels:
        g[l.scenario][l.scenario_instance_id].append((tx[l.transaction_id], l))
    return g


def test_deterministic_same_seed():
    cfg = GeneratorConfig(seed=11, n_customers=40, days=10)
    assert generate(cfg).fingerprint() == generate(cfg).fingerprint()


def test_different_seed_differs():
    a = generate(GeneratorConfig(seed=1, n_customers=40, days=10))
    b = generate(GeneratorConfig(seed=2, n_customers=40, days=10))
    assert a.fingerprint() != b.fingerprint()


def test_validation_passes(ds):
    rep = validate(ds)
    assert rep["ok"], rep["errors"]


def test_prevalence_close_to_target(ds):
    rate = validate(ds)["fraud_rate"]
    assert 0.03 <= rate <= 0.05  # last injected episode may overshoot slightly


def test_zero_prevalence_has_no_fraud():
    ds = generate(GeneratorConfig(seed=3, n_customers=30, days=10, fraud_prevalence=0,
                                  benign_lookalike_ratio=0))
    assert not any(l.is_fraud for l in ds.labels)


def test_all_scenarios_present_incl_benign(ds):
    g = by_scenario(ds)
    for s in ("velocity_burst", "impossible_travel", "mule_ring", "unusual_spending",
              "card_testing", "merchant_concentration", "benign_lookalike"):
        assert g[s], s


def test_benign_lookalikes_are_never_fraud(ds):
    assert all(not l.is_fraud for _, l in [x for v in by_scenario(ds)["benign_lookalike"].values() for x in v])


def test_velocity_burst_is_dense(ds):
    for rows in by_scenario(ds)["velocity_burst"].values():
        times = sorted(t.event_time for t, _ in rows)
        assert len(rows) >= 8 and (times[-1] - times[0]).total_seconds() <= 15 * 60
        assert all(l.is_fraud for _, l in rows)


def test_impossible_travel_speed(ds):
    for rows in by_scenario(ds)["impossible_travel"].values():
        rows = sorted(rows, key=lambda r: r[0].event_time)
        assert [l.is_fraud for _, l in rows] == [False, True]
        assert geo_speed_kmh(rows[0][0], rows[1][0]) > 900  # faster than a commercial flight


def test_mule_ring_funds_flow_to_cash_out(ds):
    for rows in by_scenario(ds)["mule_ring"].values():
        txns = [t for t, _ in rows]
        assert txns[-1].transaction_type == "cash_withdrawal"
        transfers = [t for t in txns if t.transaction_type == "transfer"]
        assert all(t.counterparty_account_id for t in transfers)
        assert txns[-1].account_id == transfers[-1].counterparty_account_id


def test_card_testing_small_then_large(ds):
    for rows in by_scenario(ds)["card_testing"].values():
        amounts = [t.amount for t, _ in sorted(rows, key=lambda r: r[0].event_time)]
        assert sum(a <= 3 for a in amounts) >= 5 and amounts[-1] > 20


def test_merchant_concentration_single_merchant(ds):
    for rows in by_scenario(ds)["merchant_concentration"].values():
        assert len({t.merchant_id for t, _ in rows}) == 1 and len(rows) >= 10


def test_labels_not_in_transaction_model():
    fields = set(Transaction.model_fields)
    assert not fields & {"is_fraud", "scenario", "scenario_instance_id", "dataset_id"}


def test_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        Transaction(is_fraud=True)


def test_config_rejects_bad_scenario():
    with pytest.raises(ValidationError):
        GeneratorConfig(scenario_weights={"nope": 1})


def test_event_ids_are_chronological(ds):
    ids = [t.transaction_id for t in ds.transactions]
    assert ids == sorted(ids)
