select account_id, customer_id, account_type, currency, opened_date from {{ ref('stg_accounts') }}
