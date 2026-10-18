select * from (values
  ('online', 'Card-not-present / web or app'), ('pos', 'Card present at point of sale'),
  ('transfer', 'Account-to-account transfer'), ('atm', 'ATM cash withdrawal')
) as t(channel, description)
