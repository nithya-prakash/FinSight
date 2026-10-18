-- One row per transaction with the latest prediction, alert flag, and offline-eval label.
with latest_pred as (
    select distinct on (transaction_id) transaction_id, model_version, risk_score, scored_at, rules_triggered
    from {{ ref('stg_predictions') }}
    order by transaction_id, scored_at desc
)
select
    t.*,
    p.model_version, p.risk_score, p.scored_at, p.rules_triggered,
    a.alert_id,
    (a.alert_id is not null) as is_flagged,
    l.is_fraud as is_fraud_label,
    (l.transaction_id is not null) as has_label
from {{ ref('stg_transactions') }} t
left join latest_pred p using (transaction_id)
left join {{ ref('stg_alerts') }} a using (transaction_id)
left join {{ ref('stg_fraud_labels') }} l using (transaction_id)
