# Data dictionary (generator v1.0.0)

All data is **synthetic and fictional**. Names are drawn from a placeholder list; no real person,
account or bank record is used. "Prediction time" = may be used as a model/rule input when a
transaction is scored.

## core.* (operational data; available at prediction time unless noted)
| Table.field | Meaning | Generation | At prediction time |
|---|---|---|---|
| customers.customer_id | `cus_NNNNNN` | sequential | yes |
| customers.full_name | placeholder name | random pick of first+last | **no (PII-like; masked in later phases)** |
| customers.home_region | home area | uniform over 5 EU regions | yes |
| customers.segment | retail/premium/small_business | weights .7/.2/.1 | yes |
| accounts.* | 1 checking + 50% credit card per customer | seeded | yes |
| merchant_categories.risk_tier | static category risk | fixed table | yes |
| merchants.* | name, category (round-robin so all exist), region, jittered coordinates | seeded | yes |
| devices.* | mobile+desktop per customer, POS per merchant, shared ATM, unknown devices for fraud | seeded | yes |
| transactions.transaction_id | `txn_NNNNNNNN`, assigned after chronological sort | deterministic | yes (stable event id) |
| transactions.event_time | when the event happened (UTC) | daytime-weighted | yes |
| transactions.amount / currency | lognormal around a per-customer mean; EUR only | seeded | yes |
| transactions.transaction_type / channel | purchase, transfer, cash_withdrawal / online, pos, transfer, atm | scenario or behaviour | yes |
| transactions.device_id, ip_region, lat, lon | device and coarse location (POS/ATM: merchant location, online: IP region centroid) | seeded | yes |
| transactions.status | approved/declined (1.5% baseline, higher in card testing) | seeded | yes (outcome of authorization) |
| transactions.counterparty_account_id | transfer destination | mule_ring only | yes |

## eval.* (ground truth, **never** a feature)
| Field | Meaning |
|---|---|
| fraud_labels.is_fraud | injected fraud truth. Becomes "known" only after label maturity in production-like use |
| fraud_labels.scenario / scenario_instance_id | generator provenance (episode that produced the row) |
| fraud_labels.generator_version / dataset_id | reproducibility metadata |
| datasets.config | full `GeneratorConfig` JSON (seed, volume, prevalence, scenario weights) |

Scenario rows: in `impossible_travel` the anchor (home) purchase is `is_fraud=false`, the distant one is
`true`. `benign_lookalike` rows are always `false` (large purchase, consistent trip, legitimate shopping burst).

## Contract-only tables (empty until later phases)
`core.model_predictions` (Phase 2/4), `core.fraud_alerts` (Phase 2), `core.investigation_cases` (Phase 6).

## Limitations
Distributions are configurable assumptions, not a model of real banking behaviour. Fraud patterns are
simple and well separated by design, so any metric on this data does **not** demonstrate real-world
fraud-detection performance.
