"""Deterministic synthetic banking data generator. All data is fictional.

Only `random.Random(seed)` is used (no wall clock, no uuid4, no Faker) so output is a pure
function of (GeneratorConfig, GENERATOR_VERSION).
"""
import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from .config import FRAUD_SCENARIOS, GENERATOR_VERSION, GeneratorConfig
from .models import Account, Customer, Device, FraudLabel, Merchant, MerchantCategory, Transaction

REGIONS = {  # region -> (lat, lon)
    "DE-BE": (52.52, 13.40), "DE-BY": (48.14, 11.58), "DE-HH": (53.55, 9.99),
    "FR-IDF": (48.86, 2.35), "UK-LON": (51.51, -0.13), "US-NY": (40.71, -74.01),
    "JP-TKY": (35.68, 139.69), "BR-SP": (-23.55, -46.63),
}
HOME_REGIONS = ["DE-BE", "DE-BY", "DE-HH", "FR-IDF", "UK-LON"]
FAR_REGIONS = ["US-NY", "JP-TKY", "BR-SP"]
CATEGORIES = [  # code, description, risk tier
    ("grocery", "Grocery stores", "low"), ("restaurant", "Restaurants", "low"),
    ("fuel", "Fuel stations", "low"), ("utilities", "Utilities", "low"),
    ("travel", "Airlines and hotels", "medium"), ("electronics", "Electronics retail", "medium"),
    ("luxury", "Luxury goods", "medium"), ("jewelry", "Jewelry", "high"),
    ("gift_cards", "Gift cards", "high"), ("online_gaming", "Online gaming", "high"),
]
EVERYDAY = ["grocery", "restaurant", "fuel", "utilities"]
FIRST = ["Alex", "Mira", "Jonas", "Lena", "Omar", "Priya", "Luca", "Sofia", "Tariq", "Hana",
         "Felix", "Nora", "Ivan", "Mei", "Paul", "Aisha"]
LAST = ["Example", "Testmann", "Sample", "Demo", "Fiktiv", "Muster", "Placeholder", "Synth"]


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


@dataclass
class Dataset:
    dataset_id: str
    config: GeneratorConfig
    customers: list[Customer]
    accounts: list[Account]
    categories: list[MerchantCategory]
    merchants: list[Merchant]
    devices: list[Device]
    transactions: list[Transaction]
    labels: list[FraudLabel]
    meta: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for coll in (self.customers, self.accounts, self.merchants, self.devices,
                     self.transactions, self.labels):
            for row in coll:
                h.update(row.model_dump_json().encode())
        return h.hexdigest()


class _Builder:
    def __init__(self, cfg: GeneratorConfig):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.start = datetime.fromisoformat(cfg.start)
        self.end = self.start + timedelta(days=cfg.days)
        self.raw: list[dict] = []  # txn dicts with scenario/instance/is_fraud
        self.devices: dict[str, Device] = {}
        self.inst = 0

    # ---- entities ---------------------------------------------------------------------
    def entities(self):
        r, cfg = self.rng, self.cfg
        self.categories = [MerchantCategory(category_code=c, description=d, risk_tier=cast(Any, t))
                           for c, d, t in CATEGORIES]
        self.merchants: list[Merchant] = []
        for i in range(cfg.n_merchants):
            cat = CATEGORIES[i % len(CATEGORIES)][0]  # every category is represented
            reg = r.choice(HOME_REGIONS + FAR_REGIONS[:1]) if i % 7 else r.choice(FAR_REGIONS)
            lat, lon = REGIONS[reg]
            self.merchants.append(Merchant(
                merchant_id=f"mer_{i:05d}", merchant_name=f"Synthetic {cat.title()} {i:05d}",
                category_code=cat, region=reg, lat=round(lat + r.uniform(-.2, .2), 4),
                lon=round(lon + r.uniform(-.2, .2), 4)))
        self.customers: list[Customer] = []
        self.accounts: list[Account] = []
        self.profile: dict[str, dict[str, Any]] = {}
        for i in range(cfg.n_customers):
            cid = f"cus_{i:06d}"
            seg = r.choices(["retail", "premium", "small_business"], [.7, .2, .1])[0]
            home = r.choice(HOME_REGIONS)
            self.customers.append(Customer(
                customer_id=cid, full_name=f"{r.choice(FIRST)} {r.choice(LAST)}", home_region=home,
                segment=cast(Any, seg), created_at=self.start - timedelta(days=r.randint(60, 2000))))
            accs = []
            for j, typ in enumerate(["checking"] + (["credit_card"] if r.random() < .5 else [])):
                aid = f"acc_{i:06d}_{j}"
                self.accounts.append(Account(account_id=aid, customer_id=cid, account_type=cast(Any, typ),
                                             currency="EUR", opened_at=self.start - timedelta(days=r.randint(30, 1500))))
                accs.append(aid)
            mean = {"retail": 35, "premium": 90, "small_business": 140}[seg] * r.uniform(.7, 1.4)
            self.profile[cid] = {"accounts": accs, "home": home, "mean": mean,
                                 "favored": r.sample(EVERYDAY, 3) + r.sample(["travel", "electronics"], 1)}
            for kind, nm in (("mobile", "m"), ("desktop", "d")):
                did = f"dev_{nm}_{i:06d}"
                self.devices[did] = Device(device_id=did, customer_id=cid, device_type=cast(Any, kind))
        for m in self.merchants:
            did = f"pos_{m.merchant_id}"
            self.devices[did] = Device(device_id=did, customer_id=None, device_type="pos_terminal")
        self.devices["atm_shared"] = Device(device_id="atm_shared", customer_id=None, device_type="atm")
        self.by_cat: dict[str, list[Merchant]] = {}
        for m in self.merchants:
            self.by_cat.setdefault(m.category_code, []).append(m)

    # ---- helpers ----------------------------------------------------------------------
    def new_inst(self, scenario: str) -> str:
        self.inst += 1
        return f"{scenario}-{self.inst:05d}"

    def rand_time(self, margin_days: float = 0) -> datetime:
        span = (self.end - self.start).total_seconds() - margin_days * 86400
        t = self.start + timedelta(seconds=self.rng.uniform(0, span))
        hour = int(self.rng.triangular(7, 23, 16))  # daytime-weighted
        return t.replace(hour=hour, minute=self.rng.randint(0, 59), second=self.rng.randint(0, 59))

    def amount(self, mean: float, mult: float = 1.0) -> Decimal:
        a = self.rng.lognormvariate(math.log(mean * mult), .5)
        return Decimal(str(round(max(a, .5), 2)))

    def add(self, *, t, cid, acc, scenario, inst, fraud, ttype="purchase", merchant=None,
            counterparty=None, amount, channel, device=None, ip_region=None, status="approved"):
        lat = lon = None
        if merchant is not None and channel in ("pos", "atm"):
            lat, lon = merchant.lat, merchant.lon
            ip_region = merchant.region
        elif ip_region:
            lat, lon = REGIONS[ip_region]
        self.raw.append(dict(event_time=t, customer_id=cid, account_id=acc,
                             merchant_id=merchant.merchant_id if merchant else None,
                             counterparty_account_id=counterparty, amount=amount, currency="EUR",
                             transaction_type=ttype, channel=channel, device_id=device,
                             ip_region=ip_region, lat=lat, lon=lon, status=status,
                             _scenario=scenario, _inst=inst, _fraud=fraud))

    def cust(self):
        c = self.rng.choice(self.customers)
        return c.customer_id, self.profile[c.customer_id]

    def merchant_in(self, cat: str, region: str | None = None) -> Merchant:
        pool = self.by_cat[cat]
        if region:
            local = [m for m in pool if m.region == region]
            pool = local or pool
        return self.rng.choice(pool)

    def new_device(self, tag: str) -> str:
        did = f"dev_unk_{len(self.devices):06d}"
        self.devices[did] = Device(device_id=did, customer_id=None, device_type="desktop")
        return did

    # ---- normal behaviour -------------------------------------------------------------
    def normal(self):
        r, cfg = self.rng, self.cfg
        for c in self.customers:
            p = self.profile[c.customer_id]
            for day in range(cfg.days):
                lam = cfg.txns_per_customer_per_day
                n = int(lam) + (1 if r.random() < lam - int(lam) else 0)
                for _ in range(n):
                    t = self.start + timedelta(days=day, hours=int(r.triangular(7, 23, 16)),
                                               minutes=r.randint(0, 59), seconds=r.randint(0, 59))
                    cat = r.choice(p["favored"]) if r.random() < .85 else r.choice(CATEGORIES)[0]
                    m = self.merchant_in(cat, p["home"])
                    online = r.random() < .3
                    self.add(t=t, cid=c.customer_id, acc=r.choice(p["accounts"]), scenario="normal",
                             inst="normal", fraud=False, merchant=m, amount=self.amount(p["mean"]),
                             channel="online" if online else "pos",
                             device=(f"dev_{r.choice('md')}_{c.customer_id[4:]}" if online
                                     else f"pos_{m.merchant_id}"),
                             ip_region=p["home"] if online else None,
                             status="declined" if r.random() < .015 else "approved")
        self.n_normal = len(self.raw)

    # ---- fraud scenarios --------------------------------------------------------------
    def velocity_burst(self):
        r = self.rng
        cid, p = self.cust()
        inst, dev = self.new_inst("velocity_burst"), self.new_device("vb")
        t0, acc = self.rand_time(1), r.choice(p["accounts"])
        for k in range(r.randint(8, 15)):
            m = self.merchant_in(r.choice(["electronics", "gift_cards"]))
            self.add(t=t0 + timedelta(seconds=k * r.randint(15, 60)), cid=cid, acc=acc,
                     scenario="velocity_burst", inst=inst, fraud=True, merchant=m,
                     amount=self.amount(150), channel="online", device=dev,
                     ip_region=r.choice(FAR_REGIONS + HOME_REGIONS))

    def impossible_travel(self):
        r = self.rng
        cid, p = self.cust()
        inst, acc, t0 = self.new_inst("impossible_travel"), r.choice(p["accounts"]), self.rand_time(1)
        m1 = self.merchant_in(r.choice(p["favored"]), p["home"])
        self.add(t=t0, cid=cid, acc=acc, scenario="impossible_travel", inst=inst, fraud=False,
                 merchant=m1, amount=self.amount(p["mean"]), channel="pos", device=f"pos_{m1.merchant_id}")
        far = [m for m in self.merchants if m.region in FAR_REGIONS and m.region != p["home"]]
        m2 = r.choice(far)
        self.add(t=t0 + timedelta(minutes=r.randint(20, 60)), cid=cid, acc=acc,
                 scenario="impossible_travel", inst=inst, fraud=True, merchant=m2,
                 amount=self.amount(300), channel="pos", device=f"pos_{m2.merchant_id}")

    def mule_ring(self):
        r = self.rng
        inst, t0 = self.new_inst("mule_ring"), self.rand_time(2)
        ring = [self.cust() for _ in range(r.randint(3, 4))]  # mule customers
        victims = [self.cust() for _ in range(r.randint(2, 4))]
        mules = [p["accounts"][0] for _, p in ring]
        t = t0
        total = Decimal(0)
        for cid, p in victims:
            amt = self.amount(1200)
            total += amt
            t += timedelta(minutes=r.randint(5, 90))
            self.add(t=t, cid=cid, acc=p["accounts"][0], scenario="mule_ring", inst=inst, fraud=True,
                     ttype="transfer", counterparty=mules[0], amount=amt, channel="transfer",
                     device=f"dev_m_{cid[4:]}", ip_region=p["home"])
        for i in range(len(ring) - 1):  # layering: hop to next mule, small cut kept
            t += timedelta(minutes=r.randint(10, 120))
            total = (total * Decimal("0.95")).quantize(Decimal("0.01"))
            self.add(t=t, cid=ring[i][0], acc=mules[i], scenario="mule_ring", inst=inst, fraud=True,
                     ttype="transfer", counterparty=mules[i + 1], amount=total, channel="transfer",
                     device=f"dev_m_{ring[i][0][4:]}", ip_region=ring[i][1]["home"])
        t += timedelta(minutes=r.randint(10, 120))
        self.add(t=t, cid=ring[-1][0], acc=mules[-1], scenario="mule_ring", inst=inst, fraud=True,
                 ttype="cash_withdrawal", amount=total, channel="atm", device="atm_shared",
                 ip_region=ring[-1][1]["home"])

    def unusual_spending(self):
        r = self.rng
        cid, p = self.cust()
        inst, acc, t0 = self.new_inst("unusual_spending"), r.choice(p["accounts"]), self.rand_time(1)
        for k in range(r.randint(1, 3)):
            cat = r.choice([c for c in ("jewelry", "luxury", "gift_cards", "online_gaming") if c not in p["favored"]])
            self.add(t=t0 + timedelta(minutes=k * r.randint(5, 40)), cid=cid, acc=acc,
                     scenario="unusual_spending", inst=inst, fraud=True, merchant=self.merchant_in(cat),
                     amount=self.amount(p["mean"], r.uniform(8, 20)), channel="online",
                     device=self.new_device("us"), ip_region=p["home"])

    def card_testing(self):
        r = self.rng
        cid, p = self.cust()
        inst, acc, t0, dev = self.new_inst("card_testing"), r.choice(p["accounts"]), self.rand_time(1), self.new_device("ct")
        m = self.merchant_in(r.choice(["online_gaming", "gift_cards"]))
        n = r.randint(5, 10)
        for k in range(n):
            self.add(t=t0 + timedelta(seconds=k * r.randint(30, 120)), cid=cid, acc=acc,
                     scenario="card_testing", inst=inst, fraud=True, merchant=m,
                     amount=Decimal(str(round(r.uniform(.5, 3), 2))), channel="online", device=dev,
                     ip_region=r.choice(FAR_REGIONS), status="declined" if r.random() < .5 else "approved")
        for k in range(r.randint(1, 2)):
            self.add(t=t0 + timedelta(seconds=n * 120 + k * 90), cid=cid, acc=acc, scenario="card_testing",
                     inst=inst, fraud=True, merchant=m, amount=self.amount(400), channel="online",
                     device=dev, ip_region=r.choice(FAR_REGIONS))

    def merchant_concentration(self):
        r = self.rng
        inst = self.new_inst("merchant_concentration")
        m = r.choice(self.by_cat["online_gaming"] + self.by_cat["gift_cards"])
        t0 = self.rand_time(3)
        for _ in range(r.randint(10, 25)):
            cid, p = self.cust()
            self.add(t=t0 + timedelta(hours=r.uniform(0, 48)), cid=cid, acc=r.choice(p["accounts"]),
                     scenario="merchant_concentration", inst=inst, fraud=True, merchant=m,
                     amount=self.amount(p["mean"], 1.5), channel="online",
                     device=f"dev_d_{cid[4:]}", ip_region=p["home"])

    # ---- benign lookalikes (is_fraud = False) -----------------------------------------
    def benign_lookalike(self):
        r = self.rng
        cid, p = self.cust()
        acc, kind = r.choice(p["accounts"]), r.choice(["big_purchase", "trip", "shopping_spree"])
        inst = self.new_inst("benign_lookalike")
        t0 = self.rand_time(4)
        kw = dict(cid=cid, acc=acc, scenario="benign_lookalike", inst=inst, fraud=False)
        if kind == "big_purchase":
            m = self.merchant_in(r.choice(["jewelry", "electronics", "luxury"]), p["home"])
            self.add(t=t0, merchant=m, amount=self.amount(p["mean"], r.uniform(8, 18)), channel="pos",
                     device=f"pos_{m.merchant_id}", **kw)
        elif kind == "trip":  # consistent trip: first foreign txn at least a day after leaving usual area
            reg = r.choice(FAR_REGIONS)
            for k in range(r.randint(3, 6)):
                m = self.merchant_in(r.choice(["restaurant", "travel", "grocery"]), reg)
                self.add(t=t0 + timedelta(hours=k * r.randint(4, 14)), merchant=m,
                         amount=self.amount(p["mean"], 1.5), channel="pos",
                         device=f"pos_{m.merchant_id}", **kw)
        else:  # many legitimate txns in a short window at the same physical area
            for k in range(r.randint(6, 9)):
                m = self.merchant_in(r.choice(["grocery", "restaurant", "electronics"]), p["home"])
                self.add(t=t0 + timedelta(minutes=k * r.randint(4, 8)), merchant=m,
                         amount=self.amount(p["mean"], .8), channel="pos", device=f"pos_{m.merchant_id}", **kw)

    # ---- orchestration ----------------------------------------------------------------
    def n_fraud(self) -> int:
        return sum(1 for x in self.raw if x["_fraud"])

    def run(self) -> Dataset:
        cfg = self.cfg
        self.entities()
        self.normal()
        fraud_target = round(cfg.fraud_prevalence * self.n_normal / (1 - cfg.fraud_prevalence)) \
            if cfg.fraud_prevalence else 0
        names = [s for s in FRAUD_SCENARIOS if cfg.scenario_weights.get(s, 0) > 0]
        weights = [cfg.scenario_weights[s] for s in names]
        count = 0
        for name in names if fraud_target else []:  # guarantee every enabled scenario appears
            getattr(self, name)()
        count = self.n_fraud()
        while fraud_target and count < fraud_target:
            getattr(self, self.rng.choices(names, weights)[0])()
            count = self.n_fraud()
        benign_target = round(count * cfg.benign_lookalike_ratio)
        got = 0
        while got < benign_target:
            before = len(self.raw)
            self.benign_lookalike()
            got += len(self.raw) - before
        order = sorted(range(len(self.raw)), key=lambda i: (self.raw[i]["event_time"], i))
        txns, labels = [], []
        ds_id = "ds-%d-%s" % (cfg.seed, hashlib.sha256(
            (cfg.model_dump_json() + GENERATOR_VERSION).encode()).hexdigest()[:8])
        for n, i in enumerate(order):
            x = self.raw[i]
            tid = f"txn_{n:08d}"
            row = {k: v for k, v in x.items() if not k.startswith("_")}
            txns.append(Transaction(transaction_id=tid, **row))
            labels.append(FraudLabel(transaction_id=tid, is_fraud=x["_fraud"], scenario=x["_scenario"],
                                     scenario_instance_id=x["_inst"], generator_version=GENERATOR_VERSION,
                                     dataset_id=ds_id))
        return Dataset(ds_id, cfg, self.customers, self.accounts, self.categories, self.merchants,
                       list(self.devices.values()), txns, labels,
                       meta={"fraud_txns": sum(l.is_fraud for l in labels)})


def generate(cfg: GeneratorConfig | None = None) -> Dataset:
    return _Builder(cfg or GeneratorConfig()).run()


def geo_speed_kmh(a: Transaction, b: Transaction) -> float:
    """Helper for tests/validation: implied speed between two geolocated transactions."""
    dt = abs((b.event_time - a.event_time).total_seconds()) / 3600 or 1e-9
    if a.lat is None or a.lon is None or b.lat is None or b.lon is None:
        raise ValueError("both transactions need coordinates")
    return _haversine_km((a.lat, a.lon), (b.lat, b.lon)) / dt


__all__ = ["Dataset", "generate", "geo_speed_kmh"]
