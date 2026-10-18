"""Feature definitions shared by training (offline) and scoring (online).

`featurize(ctx)` is the SINGLE definition. Offline, contexts are rebuilt in-memory by
`OfflineContexts` with exactly the semantics of `scoring.context.fetch_context` (strictly earlier
events only), so there is no train/serve skew and no look-ahead.
Forbidden inputs: labels, scenario metadata, anything after the current event.
"""
import math
from collections import defaultdict, deque
from datetime import timedelta

from ..generator.models import Transaction
from ..scoring.rules import Context, Hist, haversine_km

FEATURE_SET_VERSION = "fs-1"
FEATURES = [
    "amount", "log_amount", "hour", "is_declined", "channel_online", "channel_pos", "channel_transfer",
    "channel_atm", "type_transfer", "type_cash", "risk_tier", "txn_count_10m", "txn_count_1h",
    "amount_sum_1h", "small_count_30m", "prior_count", "prior_mean", "amount_ratio", "amount_z",
    "category_seen", "incoming_sum_24h", "passthrough_ratio", "geo_km", "geo_minutes", "geo_speed",
    "device_known",
]
FORBIDDEN = {"is_fraud", "is_fraud_label", "scenario", "scenario_instance_id", "dataset_id", "has_label"}
_TIER = {None: 0, "low": 1, "medium": 2, "high": 3}
assert not FORBIDDEN & set(FEATURES)


def featurize(ctx: Context) -> dict[str, float]:
    t = ctx.txn
    amount = float(t.amount)
    acc = [h for h in ctx.recent if h.account_id == t.account_id]
    t10 = t.event_time - timedelta(minutes=10)
    t1h = t.event_time - timedelta(hours=1)
    t30 = t.event_time - timedelta(minutes=30)
    in1h = [h for h in acc if h.event_time >= t1h]
    incoming = sum(a for _, a in ctx.incoming)
    mean = ctx.prior_mean if ctx.prior_mean is not None else 0.0
    std = ctx.prior_std if ctx.prior_std is not None else 0.0
    km = mins = speed = 0.0
    if t.lat is not None and t.lon is not None:
        for h in sorted(ctx.recent, key=lambda h: h.event_time, reverse=True):
            if h.lat is None or h.lon is None:
                continue
            mins = (t.event_time - h.event_time).total_seconds() / 60
            km = haversine_km(h.lat, h.lon, t.lat, t.lon)
            speed = min(km / max(mins / 60, 1 / 3600), 2000.0)
            break
    return {
        "amount": amount, "log_amount": math.log1p(amount), "hour": float(t.event_time.hour),
        "is_declined": float(t.status == "declined"),
        "channel_online": float(t.channel == "online"), "channel_pos": float(t.channel == "pos"),
        "channel_transfer": float(t.channel == "transfer"), "channel_atm": float(t.channel == "atm"),
        "type_transfer": float(t.transaction_type == "transfer"),
        "type_cash": float(t.transaction_type == "cash_withdrawal"),
        "risk_tier": float(_TIER[ctx.risk_tier]),
        "txn_count_10m": float(1 + sum(1 for h in acc if h.event_time >= t10)),
        "txn_count_1h": float(1 + len(in1h)),
        "amount_sum_1h": amount + sum(h.amount for h in in1h),
        "small_count_30m": float(sum(1 for h in acc if h.event_time >= t30 and h.amount <= 3.0)),
        "prior_count": float(ctx.prior_count), "prior_mean": mean,
        "amount_ratio": amount / mean if mean > 0 else 1.0,
        "amount_z": (amount - mean) / (std + 1.0) if ctx.prior_count else 0.0,
        "category_seen": float(ctx.category is not None and ctx.seen_categories.get(ctx.category, 0) > 0),
        "incoming_sum_24h": incoming, "passthrough_ratio": amount / incoming if incoming else 0.0,
        "geo_km": km, "geo_minutes": mins, "geo_speed": speed,
        "device_known": float(ctx.device_known),
    }


class OfflineContexts:
    """Rebuilds the online context for each transaction from an in-memory frame, in event order."""

    def __init__(self, merchants: dict[str, tuple[str, str]], devices: dict[str, tuple[str | None, str]]):
        self.merchants, self.devices = merchants, devices

    def iterate(self, txns: list[Transaction]):
        """Yield (txn, Context) in (event_time, transaction_id) order; state excludes the current txn."""
        recent: dict[str, deque] = defaultdict(deque)           # customer -> Hist (last 24h)
        stats: dict[str, list] = defaultdict(lambda: [0, 0.0, 0.0])  # count, sum, sumsq
        seen: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        incoming: dict[str, deque] = defaultdict(deque)         # account -> (time, amount)
        for t in sorted(txns, key=lambda x: (x.event_time, x.transaction_id)):
            horizon = t.event_time - timedelta(hours=24)
            r = recent[t.customer_id]
            while r and r[0].event_time < horizon:
                r.popleft()
            inc = incoming[t.account_id]
            while inc and inc[0][0] < horizon:
                inc.popleft()
            n, s1, s2 = stats[t.customer_id]
            cat, tier = self.merchants.get(t.merchant_id, (None, None)) if t.merchant_id else (None, None)
            dev = self.devices.get(t.device_id) if t.device_id else None
            ctx = Context(
                txn=t, category=cat, risk_tier=tier, recent=list(r), prior_count=n,
                prior_mean=(s1 / n) if n else None,
                prior_std=math.sqrt(max(s2 / n - (s1 / n) ** 2, 0.0)) if n else None,
                seen_categories=dict(seen[t.customer_id]), incoming=list(inc),
                device_known=True if dev is None else (dev[0] == t.customer_id or dev[1] in ("pos_terminal", "atm")))
            yield t, ctx
            a = float(t.amount)
            r.append(Hist(t.transaction_id, t.event_time, t.account_id, t.merchant_id, a, t.lat, t.lon))
            stats[t.customer_id] = [n + 1, s1 + a, s2 + a * a]
            if cat:
                seen[t.customer_id][cat] += 1
            if t.counterparty_account_id:
                incoming[t.counterparty_account_id].append((t.event_time, a))
