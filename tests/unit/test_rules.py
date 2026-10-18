from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from finsight.events import make_event
from finsight.generator.models import Transaction
from finsight.scoring.rules import Context, Hist, RuleConfig, combine, evaluate, severity

T0 = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def txn(**kw):
    base = dict(transaction_id="txn_x", event_time=T0, customer_id="c", account_id="a",
                merchant_id="m", counterparty_account_id=None, amount=Decimal("20"), currency="EUR",
                transaction_type="purchase", channel="online", device_id=None, ip_region=None,
                lat=None, lon=None, status="approved")
    base.update(kw)
    return Transaction(**base)


def hist(minutes_ago, amount=20.0, lat=None, lon=None, acc="a", i=0):
    return Hist(f"h{i}", T0 - timedelta(minutes=minutes_ago), acc, "m", amount, lat, lon)


def rules(ctx):
    return {s.rule for s in evaluate(ctx)}


def test_velocity_fires_at_threshold_and_not_below():
    four = [hist(m, i=m) for m in (1, 2, 3, 4)]
    assert "velocity" not in rules(Context(txn=txn(), recent=four))                   # 4 + current = 5 < 6
    assert "velocity" in rules(Context(txn=txn(), recent=four + [hist(5, i=9)]))      # 5 + current = 6


def test_velocity_ignores_old_and_other_accounts():
    old = [hist(30 + m, i=m) for m in range(8)]
    other = [hist(1, acc="b", i=20 + m) for m in range(8)]
    assert "velocity" not in rules(Context(txn=txn(), recent=old + other))


def test_card_testing_small_then_large():
    small = [hist(m, amount=1.5, i=m) for m in range(1, 6)]
    assert "repeated_small_payments" in rules(Context(txn=txn(amount=Decimal("400")), recent=small))
    assert "repeated_small_payments" not in rules(Context(txn=txn(amount=Decimal("30")), recent=small))


def test_unusual_amount_needs_history():
    assert "unusual_amount" not in rules(Context(txn=txn(amount=Decimal("900")), prior_count=2, prior_mean=20, prior_std=5))
    s = [x for x in evaluate(Context(txn=txn(amount=Decimal("900")), prior_count=30, prior_mean=30,
                                     prior_std=10)) if x.rule == "unusual_amount"]
    assert s and s[0].evidence["limit"] == 150.0


def test_unusual_category_only_high_risk_and_unseen():
    base = dict(txn=txn(), category="jewelry", risk_tier="high", prior_count=20)
    assert "unusual_category" in rules(Context(**base))
    assert "unusual_category" not in rules(Context(**{**base, "seen_categories": {"jewelry": 1}}))
    assert "unusual_category" not in rules(Context(**{**base, "risk_tier": "low"}))


def test_transfer_passthrough():
    ctx = Context(txn=txn(transaction_type="transfer", merchant_id=None, counterparty_account_id="z",
                          amount=Decimal("950")), incoming=[(T0 - timedelta(hours=2), 1000.0)])
    assert "transfer_passthrough" in rules(ctx)
    ctx.txn = txn(transaction_type="transfer", merchant_id=None, counterparty_account_id="z", amount=Decimal("100"))
    assert "transfer_passthrough" not in rules(ctx)


def test_geo_impossible_vs_plausible():
    berlin = hist(30, lat=52.5, lon=13.4)
    tokyo = txn(lat=35.7, lon=139.7)
    sig = [s for s in evaluate(Context(txn=tokyo, recent=[berlin])) if s.rule == "geo_inconsistency"]
    assert sig and sig[0].evidence["speed_kmh"] > 900 and "VPN" in sig[0].explanation
    # same trip over 20 hours is plausible; missing coordinates yield no signal
    assert "geo_inconsistency" not in rules(Context(txn=tokyo, recent=[hist(1200, lat=52.5, lon=13.4)]))
    assert "geo_inconsistency" not in rules(Context(txn=txn(), recent=[berlin]))


def test_combine_and_severity():
    assert combine([]) == 0.0
    from finsight.scoring.rules import Signal
    s = [Signal(rule="a", score=0.5, explanation="", evidence={}), Signal(rule="b", score=0.5, explanation="", evidence={})]
    assert combine(s) == 0.75
    assert [severity(x) for x in (0.5, 0.7, 0.9)] == ["low", "medium", "high"]


def test_thresholds_are_configurable():
    five = [hist(m, i=m) for m in (1, 2, 3)]
    cfg = RuleConfig(velocity_min_txns=4)
    assert any(s.rule == "velocity" for s in evaluate(Context(txn=txn(), recent=five), cfg))


def test_event_contract_validation():
    ev = make_event(txn())
    assert ev.event_id == "evt_txn_x" and ev.schema_version == 1
    bad = ev.model_dump(mode="json") | {"event_id": "evt_other"}
    with pytest.raises(ValidationError):
        type(ev).model_validate(bad)
    with pytest.raises(ValidationError):
        type(ev).model_validate(ev.model_dump(mode="json") | {"schema_version": 2})
