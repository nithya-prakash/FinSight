-- Business rule: an alert can only exist for a scored transaction.
select a.alert_id from {{ ref('stg_alerts') }} a
left join {{ ref('stg_predictions') }} p using (transaction_id) where p.transaction_id is null
