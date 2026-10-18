select
    a.alert_id, a.transaction_id, a.severity, a.alerted_at,
    c.case_id, c.status as case_status, c.opened_at, c.closed_at, coalesce(c.is_closed, false) as is_closed,
    (c.case_id is not null) as is_investigated,
    case when c.is_closed then extract(epoch from (c.closed_at - c.opened_at)) / 3600.0 end as resolution_hours
from {{ ref('stg_alerts') }} a
left join {{ ref('stg_investigations') }} c using (alert_id)
