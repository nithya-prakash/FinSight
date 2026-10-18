select alert_id, transaction_id, severity, created_at as alerted_at, jsonb_array_length(signals) as signal_count
from {{ source('core', 'fraud_alerts') }}
