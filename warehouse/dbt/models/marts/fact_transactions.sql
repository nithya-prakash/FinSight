-- Grain: one row per transaction (transaction_id).
select
    t.transaction_id, to_char(t.event_date, 'YYYYMMDD')::int as date_key, t.event_time,
    t.customer_id, t.account_id, t.merchant_id, t.channel, t.transaction_type,
    t.amount, t.currency, t.status, (t.status = 'declined') as is_declined,
    h.prior_txn_count, h.prior_avg_amount,
    v.txn_count_10m, v.txn_count_1h, v.amount_sum_1h,
    t.model_version, t.risk_score, t.is_flagged,
    t.has_label, t.is_fraud_label            -- ground truth: offline evaluation only
from {{ ref('int_transactions_labeled') }} t
join {{ ref('int_customer_transaction_history') }} h using (transaction_id)
join {{ ref('int_account_velocity') }} v using (transaction_id)
