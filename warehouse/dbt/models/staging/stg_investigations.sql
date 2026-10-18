select case_id, alert_id, status, opened_at, closed_at,
       (status in ('closed_fraud', 'closed_legit')) as is_closed
from {{ source('core', 'investigation_cases') }}
