-- Alert-to-investigation conversion and resolution time (cases are created by analysts, Phase 6).
select
    count(*) as alert_count,
    count(*) filter (where is_investigated) as investigated_count,
    count(*) filter (where is_investigated)::numeric / nullif(count(*), 0) as investigation_conversion_rate,
    avg(resolution_hours) as avg_resolution_hours,
    count(*) filter (where case_status = 'closed_fraud') as confirmed_fraud_cases
from {{ ref('int_alert_investigations') }}
