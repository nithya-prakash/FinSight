select
    transaction_id,
    model_version,
    risk_score,
    scored_at,
    array(select s ->> 'rule' from jsonb_array_elements(coalesce(evidence -> 'signals', '[]'::jsonb)) s) as rules_triggered
from {{ source('core', 'model_predictions') }}
