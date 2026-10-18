-- Ground truth. Offline evaluation only; never a model feature.
select transaction_id, is_fraud, scenario, dataset_id
from {{ source('eval', 'fraud_labels') }}
