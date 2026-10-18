-- TP+FP = flagged labelled; TP+FN = all fraud.
select date_key from {{ ref('metrics_daily') }}
where true_positives + false_negatives <> fraud_txn_count
   or true_positives + false_positives > flagged_txn_count
