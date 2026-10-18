select m.merchant_id, m.merchant_name, m.category_code, c.description as category_description,
       c.risk_tier, m.region, m.lat, m.lon
from {{ source('core', 'merchants') }} m
join {{ source('core', 'merchant_categories') }} c using (category_code)
