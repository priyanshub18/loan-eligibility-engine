-- OPTIONAL fallback seed. Workflow A (crawler) is the real source of
-- loan_products; use this only if the crawled sites are unreachable during a
-- demo:  psql "$DATABASE_URL" -f db/seed_loan_products.sql
INSERT INTO loan_products (product_key, lender, product_name, interest_rate_min, interest_rate_max,
                           processing_fee, min_monthly_income, min_credit_score, min_age, max_age,
                           employment_types, max_loan_amount, other_criteria, source_url, criteria_source)
VALUES
 ('hdfc-bank-personal-loan', 'HDFC Bank', 'HDFC Bank Personal Loan', 9.99, 24.00, 'Up to Rs. 6,500 + GST',
  25000, 720, 21, 60, ARRAY['salaried'], 4000000,
  'Minimum 2 years total work experience, 1 year with current employer.',
  'https://www.bankbazaar.com/hdfc-personal-loan.html', 'seed'),
 ('icici-bank-personal-loan', 'ICICI Bank', 'ICICI Bank Personal Loan', 9.99, 16.50, 'Up to 2%',
  30000, 700, 20, 58, ARRAY['salaried','self_employed'], 5000000,
  'Credit score of 700+ preferred; repayment capacity assessed on existing obligations.',
  'https://www.bankbazaar.com/icici-personal-loan.html', 'seed'),
 ('state-bank-of-india-personal-loan', 'State Bank of India', 'SBI Xpress Credit Personal Loan', 10.00, 15.00, 'Up to 1.50%',
  15000, 700, 21, 58, ARRAY['salaried'], 3500000,
  'Salary account with SBI preferred; EMI/NMI ratio should not exceed 50%.',
  'https://www.bankbazaar.com/sbi-personal-loan.html', 'seed'),
 ('kotak-mahindra-bank-personal-loan', 'Kotak Mahindra Bank', 'Kotak Personal Loan', 10.99, 24.00, 'Up to 5%',
  25000, 730, 21, 55, ARRAY['salaried'], 3500000,
  'Applicants must be salaried employees of private/public companies or government.',
  'https://www.bankbazaar.com/kotak-personal-loan.html', 'seed'),
 ('bajaj-finserv-personal-loan', 'Bajaj Finserv', 'Bajaj Finserv Personal Loan', 10.00, 31.00, 'Up to 3.93%',
  15000, 750, 21, 60, ARRAY['salaried','self_employed','business'], 5500000,
  'Self-employed applicants need a minimum business vintage of 3 years.',
  'https://www.bankbazaar.com/bajaj-finserv-personal-loan.html', 'seed')
ON CONFLICT (product_key) DO NOTHING;
