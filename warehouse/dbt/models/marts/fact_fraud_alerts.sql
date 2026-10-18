-- Grain: one row per alert (alert_id); at most one alert per transaction.
select a.alert_id, a.transaction_id, a.severity, a.alerted_at, a.signal_count,
       to_char(t.event_date, 'YYYYMMDD')::int as date_key, t.account_id, t.amount,
       t.risk_score, t.has_label, t.is_fraud_label,
       i.is_investigated
from {{ ref('stg_alerts') }} a
join {{ ref('int_transactions_labeled') }} t using (transaction_id)
join {{ ref('int_alert_investigations') }} i on i.alert_id = a.alert_id
