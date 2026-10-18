select
    to_char(d, 'YYYYMMDD')::int as date_key,
    d::date as date_day,
    extract(isodow from d)::int as iso_weekday,
    to_char(d, 'Dy') as weekday_name,
    date_trunc('week', d)::date as week_start,
    date_trunc('month', d)::date as month_start,
    extract(isodow from d) in (6, 7) as is_weekend
from generate_series(
    (select coalesce(min(event_date), current_date) from {{ ref('stg_transactions') }}),
    (select coalesce(max(event_date), current_date) from {{ ref('stg_transactions') }}),
    interval '1 day') as d
