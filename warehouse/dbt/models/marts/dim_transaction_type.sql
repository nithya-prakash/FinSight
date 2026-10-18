select * from (values
  ('purchase', 'Merchant purchase'), ('transfer', 'Account-to-account transfer'),
  ('cash_withdrawal', 'Cash withdrawal')
) as t(transaction_type, description)
