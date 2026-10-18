"""Interpretable, configurable fraud rules. Pure functions over a prefetched Context.

Rules only see data available at prediction time (events strictly before the current one in
(event_time, transaction_id) order). Each rule returns a Signal with structured evidence.
A signal is evidence of *risk*, not proof of fraud.
"""
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import NamedTuple

from pydantic import BaseModel

from ..generator.models import Transaction


class Hist(NamedTuple):
    transaction_id: str
    event_time: datetime
    account_id: str
    merchant_id: str | None
    amount: float
    lat: float | None
    lon: float | None


@dataclass
class Context:
    txn: Transaction
    category: str | None = None
    risk_tier: str | None = None
    recent: list[Hist] = field(default_factory=list)       # customer's prior txns, last 24h
    prior_count: int = 0                                    # customer's all prior txns
    prior_mean: float | None = None
    prior_std: float | None = None
    seen_categories: dict[str, int] = field(default_factory=dict)
    incoming: list[tuple[datetime, float]] = field(default_factory=list)  # transfers into account, 24h
    device_known: bool = True  # device belongs to the customer, or is a shared POS/ATM terminal


class Signal(BaseModel):
    rule: str
    score: float
    explanation: str
    evidence: dict


class RuleConfig(BaseModel):
    velocity_window_min: int = 10
    velocity_min_txns: int = 6
    small_amount: float = 3.0
    small_window_min: int = 30
    small_min_count: int = 4
    amount_sigma: float = 4.0
    amount_min_ratio: float = 5.0
    amount_floor: float = 100.0
    amount_min_history: int = 5
    category_min_history: int = 10
    passthrough_ratio: float = 0.8
    geo_max_window_min: int = 180
    geo_min_km: float = 500.0
    geo_min_speed_kmh: float = 900.0


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


def velocity(ctx: Context, cfg: RuleConfig) -> Signal | None:
    t = ctx.txn
    since = t.event_time - timedelta(minutes=cfg.velocity_window_min)
    n = 1 + sum(1 for h in ctx.recent if h.account_id == t.account_id and h.event_time >= since)
    if n < cfg.velocity_min_txns:
        return None
    return Signal(rule="velocity", score=min(0.95, 0.6 + 0.05 * (n - cfg.velocity_min_txns)),
                  explanation=f"{n} transactions on this account within {cfg.velocity_window_min} minutes",
                  evidence={"count": n, "window_min": cfg.velocity_window_min})


def repeated_small_payments(ctx: Context, cfg: RuleConfig) -> Signal | None:
    t = ctx.txn
    since = t.event_time - timedelta(minutes=cfg.small_window_min)
    small = [h for h in ctx.recent if h.account_id == t.account_id and h.event_time >= since
             and h.amount <= cfg.small_amount]
    amount = float(t.amount)
    follows = amount <= cfg.small_amount or amount > 20 * cfg.small_amount
    if len(small) < cfg.small_min_count or not follows:
        return None
    return Signal(rule="repeated_small_payments", score=0.7,
                  explanation=f"{len(small)} payments <= {cfg.small_amount} in {cfg.small_window_min} min "
                              "before this transaction (possible card testing)",
                  evidence={"small_count": len(small), "current_amount": amount})


def unusual_amount(ctx: Context, cfg: RuleConfig) -> Signal | None:
    if ctx.prior_count < cfg.amount_min_history or ctx.prior_mean is None:
        return None
    amount = float(ctx.txn.amount)
    limit = max(ctx.prior_mean + cfg.amount_sigma * (ctx.prior_std or 0.0),
                cfg.amount_min_ratio * ctx.prior_mean)
    if amount <= limit or amount < cfg.amount_floor:
        return None
    return Signal(rule="unusual_amount", score=round(0.45 + min(0.2, (amount / limit - 1) * 0.1), 4),
                  explanation=f"amount {amount:.2f} exceeds customer's usual range (limit {limit:.2f})",
                  evidence={"amount": amount, "limit": round(limit, 2), "history_count": ctx.prior_count,
                            "history_mean": round(ctx.prior_mean, 2)})


def unusual_category(ctx: Context, cfg: RuleConfig) -> Signal | None:
    if (ctx.category is None or ctx.risk_tier != "high" or ctx.prior_count < cfg.category_min_history
            or ctx.seen_categories.get(ctx.category, 0) > 0):
        return None
    return Signal(rule="unusual_category", score=0.35,
                  explanation=f"first purchase in high-risk category '{ctx.category}' for this customer",
                  evidence={"category": ctx.category, "history_count": ctx.prior_count})


def transfer_passthrough(ctx: Context, cfg: RuleConfig) -> Signal | None:
    t = ctx.txn
    if t.transaction_type not in ("transfer", "cash_withdrawal") or not ctx.incoming:
        return None
    received = sum(a for _, a in ctx.incoming)
    if float(t.amount) < cfg.passthrough_ratio * received:
        return None
    return Signal(rule="transfer_passthrough", score=0.6,
                  explanation=f"{t.transaction_type} moves out >= {int(cfg.passthrough_ratio * 100)}% of "
                              f"{received:.2f} received via {len(ctx.incoming)} transfer(s) in 24h",
                  evidence={"received_total": round(received, 2), "incoming_count": len(ctx.incoming),
                            "outgoing": float(t.amount)})


def geo_inconsistency(ctx: Context, cfg: RuleConfig) -> Signal | None:
    t = ctx.txn
    if t.lat is None or t.lon is None:
        return None
    for h in sorted(ctx.recent, key=lambda h: h.event_time, reverse=True):
        if h.lat is None or h.lon is None:
            continue
        mins = (t.event_time - h.event_time).total_seconds() / 60
        if mins > cfg.geo_max_window_min:
            return None
        km = haversine_km(h.lat, h.lon, t.lat, t.lon)
        speed = km / max(mins / 60, 1 / 3600)
        if km >= cfg.geo_min_km and speed >= cfg.geo_min_speed_kmh:
            return Signal(rule="geo_inconsistency", score=0.85,
                          explanation=f"{km:.0f} km from previous geolocated transaction in {mins:.0f} min "
                                      f"(implied {speed:.0f} km/h); may reflect VPN/proxy or data limits",
                          evidence={"distance_km": round(km), "minutes": round(mins, 1),
                                    "speed_kmh": round(speed), "previous_transaction_id": h.transaction_id})
        return None  # only compare against the most recent geolocated event
    return None


RULES = [velocity, repeated_small_payments, unusual_amount, unusual_category,
         transfer_passthrough, geo_inconsistency]


def evaluate(ctx: Context, cfg: RuleConfig | None = None) -> list[Signal]:
    cfg = cfg or RuleConfig()
    return [s for r in RULES if (s := r(ctx, cfg))]


def combine(signals: list[Signal]) -> float:
    """Noisy-OR of independent rule scores (an ordinal risk score, not a calibrated probability)."""
    p = 1.0
    for s in signals:
        p *= 1 - s.score
    return round(1 - p, 4)


def severity(score: float) -> str:
    return "high" if score >= 0.85 else "medium" if score >= 0.65 else "low"
