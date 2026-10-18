select m.category_code, m.risk_tier,
       count(*) as txn_count, sum(f.amount) as txn_value,
       count(*) filter (where f.is_fraud_label) as fraud_txn_count,
       coalesce(sum(f.amount) filter (where f.is_fraud_label), 0) as fraud_exposure,
       count(*) filter (where f.is_fraud_label)::numeric / nullif(count(*) filter (where f.has_label), 0) as fraud_rate
from {{ ref('fact_transactions') }} f
join {{ ref('dim_merchant') }} m using (merchant_id)
group by 1, 2
