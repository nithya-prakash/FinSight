select customer_id, segment, home_region, customer_since from {{ ref('stg_customers') }}
