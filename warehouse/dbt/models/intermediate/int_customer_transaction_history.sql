-- Behaviour BEFORE each transaction (strictly earlier rows only -> no look-ahead).
select
    transaction_id, customer_id, event_time,
    count(*)    over w as prior_txn_count,
    avg(amount) over w as prior_avg_amount,
    sum(amount) over w as prior_total_amount
from {{ ref('stg_transactions') }}
window w as (partition by customer_id order by event_time, transaction_id
              rows between unbounded preceding and 1 preceding)
