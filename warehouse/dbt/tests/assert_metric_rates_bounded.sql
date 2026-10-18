select date_key from {{ ref('metrics_daily') }}
where fraud_rate not between 0 and 1 or flagged_rate not between 0 and 1
   or precision not between 0 and 1 or recall not between 0 and 1 or false_positive_rate not between 0 and 1
