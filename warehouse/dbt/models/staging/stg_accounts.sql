select account_id, customer_id, account_type, upper(currency) as currency,
       (opened_at at time zone 'UTC')::date as opened_date
from {{ source('core', 'accounts') }}
