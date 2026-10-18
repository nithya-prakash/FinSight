select
    merchant_id,
    count(*) as txn_count,
    sum(amount) as total_amount,
    count(distinct customer_id) as distinct_customers,
    count(*) filter (where is_fraud_label) as labeled_fraud_count,
    count(*) filter (where is_flagged) as flagged_count
from {{ ref('int_transactions_labeled') }}
where merchant_id is not null
group by merchant_id
