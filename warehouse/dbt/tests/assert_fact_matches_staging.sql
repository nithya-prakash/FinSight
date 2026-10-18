-- Completeness: the fact must neither drop nor duplicate transactions.
select 1 from (select (select count(*) from {{ ref('stg_transactions') }}) as s,
                      (select count(*) from {{ ref('fact_transactions') }}) as f) x where s <> f
