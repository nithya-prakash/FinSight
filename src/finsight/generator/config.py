from pydantic import BaseModel, Field, model_validator

GENERATOR_VERSION = "1.0.0"

FRAUD_SCENARIOS = ["velocity_burst", "impossible_travel", "mule_ring", "unusual_spending",
                   "card_testing", "merchant_concentration"]


class GeneratorConfig(BaseModel):
    seed: int = 42
    n_customers: int = Field(default=200, ge=10)
    n_merchants: int = Field(default=60, ge=12)
    days: int = Field(default=30, ge=7)
    start: str = "2026-01-01T00:00:00+00:00"
    txns_per_customer_per_day: float = Field(default=0.8, gt=0)
    fraud_prevalence: float = Field(default=0.02, ge=0, le=0.2)
    benign_lookalike_ratio: float = Field(default=1.0, ge=0, description="benign lookalike txns per fraud txn")
    scenario_weights: dict[str, float] = Field(
        default_factory=lambda: {s: 1.0 for s in FRAUD_SCENARIOS})

    @model_validator(mode="after")
    def _check(self):
        bad = set(self.scenario_weights) - set(FRAUD_SCENARIOS)
        if bad:
            raise ValueError(f"unknown scenarios: {bad}")
        if self.fraud_prevalence > 0 and sum(self.scenario_weights.values()) <= 0:
            raise ValueError("scenario_weights must sum to > 0")
        return self


PROFILES = {
    "dev": GeneratorConfig(),
    "ml": GeneratorConfig(seed=11, n_customers=1500, n_merchants=200, days=60, txns_per_customer_per_day=0.7,
                          fraud_prevalence=0.01),
    "benchmark": GeneratorConfig(seed=2026, n_customers=3000, n_merchants=400, days=90,
                                 txns_per_customer_per_day=0.6, fraud_prevalence=0.005),
}
