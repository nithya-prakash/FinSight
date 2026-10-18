-- Time-windowed account activity INCLUDING the current transaction (trailing windows only).
select
    transaction_id, account_id, event_time,
    count(*)    over w10 as txn_count_10m,
    count(*)    over w1h as txn_count_1h,
    sum(amount) over w1h as amount_sum_1h
from {{ ref('stg_transactions') }}
window w10 as (partition by account_id order by event_time range between interval '10 minutes' preceding and current row),
       w1h as (partition by account_id order by event_time range between interval '1 hour' preceding and current row)
