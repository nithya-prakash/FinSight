-- Deduplicated (by transaction_id, latest ingestion wins), typed, standardized.
with ranked as (
    select *, row_number() over (partition by transaction_id order by ingested_at desc) as rn
    from {{ source('core', 'transactions') }}
)
select
    transaction_id,
    event_time,
    (event_time at time zone 'UTC')::date as event_date,
    customer_id,
    account_id,
    merchant_id,
    counterparty_account_id,
    amount::numeric(14, 2) as amount,
    upper(currency) as currency,
    transaction_type,
    channel,
    device_id,
    ip_region,
    lat,
    lon,
    status,
    ingested_at
from ranked
where rn = 1 and amount > 0
