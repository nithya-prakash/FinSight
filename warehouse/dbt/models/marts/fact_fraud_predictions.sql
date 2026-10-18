-- Grain: one row per (transaction_id, model_version).
select p.transaction_id, p.model_version, p.risk_score, p.scored_at, p.rules_triggered,
       to_char(t.event_date, 'YYYYMMDD')::int as date_key, t.account_id, t.amount
from {{ ref('stg_predictions') }} p
join {{ ref('stg_transactions') }} t using (transaction_id)
