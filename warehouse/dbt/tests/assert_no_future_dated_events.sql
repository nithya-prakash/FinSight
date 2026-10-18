select transaction_id from {{ ref('stg_transactions') }} where event_time > ingested_at + interval '1 day'
