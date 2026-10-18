select merchant_id, merchant_name, category_code, category_description, risk_tier, region
from {{ ref('stg_merchants') }}
