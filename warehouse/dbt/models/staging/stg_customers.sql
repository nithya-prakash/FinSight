-- full_name is deliberately NOT carried into the warehouse (PII minimisation).
select customer_id, home_region, segment, (created_at at time zone 'UTC')::date as customer_since
from {{ source('core', 'customers') }}
