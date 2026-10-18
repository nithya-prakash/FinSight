-- Daily KPIs. fraud_* use ground-truth labels (labelled transactions only); flagged_* use alerts.
-- flagged_rate is NOT the fraud rate. precision/recall/fpr are NULL when their denominator is 0.
with d as (
    select
        f.date_key,
        count(*) as txn_count,
        sum(amount) as txn_value,
        count(*) filter (where has_label) as labeled_txn_count,
        count(*) filter (where is_fraud_label) as fraud_txn_count,
        coalesce(sum(amount) filter (where is_fraud_label), 0) as fraud_value,
        count(*) filter (where is_flagged) as flagged_txn_count,
        count(*) filter (where is_flagged and is_fraud_label) as true_positives,
        count(*) filter (where is_flagged and has_label and not is_fraud_label) as false_positives,
        count(*) filter (where not is_flagged and is_fraud_label) as false_negatives,
        count(*) filter (where has_label and not is_fraud_label) as actual_negatives
    from {{ ref('fact_transactions') }} f
    group by 1
)
select
    date_key, txn_count, txn_value, labeled_txn_count, fraud_txn_count, fraud_value, flagged_txn_count,
    true_positives, false_positives, false_negatives, actual_negatives,
    fraud_txn_count::numeric / nullif(labeled_txn_count, 0) as fraud_rate,
    flagged_txn_count::numeric / nullif(txn_count, 0) as flagged_rate,
    true_positives::numeric / nullif(true_positives + false_positives, 0) as precision,
    true_positives::numeric / nullif(true_positives + false_negatives, 0) as recall,
    false_positives::numeric / nullif(actual_negatives, 0) as false_positive_rate
from d
