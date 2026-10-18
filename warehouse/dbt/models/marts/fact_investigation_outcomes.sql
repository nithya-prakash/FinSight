-- Grain: one row per investigation case (case_id).
select case_id, alert_id, transaction_id, case_status, opened_at, closed_at, is_closed, resolution_hours
from {{ ref('int_alert_investigations') }}
where case_id is not null
