// Crawl plan. Two sites, chosen because they are server-rendered (no headless
// browser needed) and cover both an aggregator and a lender's own page:
//   1. BankBazaar - one rate-comparison table (many lenders at once) plus
//      per-lender pages that carry the eligibility criteria.
//   2. ICICI Bank  - the lender's official eligibility page; official values
//      take precedence over the aggregator when both are present.
const BB = 'https://www.bankbazaar.com';

const targets = [
  { kind: 'rate_table', source: 'bankbazaar', url: `${BB}/personal-loan-interest-rate.html` },
  { kind: 'lender_page', source: 'bankbazaar', lender: 'HDFC Bank', url: `${BB}/hdfc-personal-loan.html` },
  { kind: 'lender_page', source: 'bankbazaar', lender: 'State Bank of India', url: `${BB}/sbi-personal-loan.html` },
  { kind: 'lender_page', source: 'bankbazaar', lender: 'ICICI Bank', url: `${BB}/icici-personal-loan.html` },
  { kind: 'lender_page', source: 'bankbazaar', lender: 'Kotak Mahindra Bank', url: `${BB}/kotak-personal-loan.html` },
  { kind: 'lender_page', source: 'bankbazaar', lender: 'Bajaj Finserv', url: `${BB}/bajaj-finserv-personal-loan.html` },
  { kind: 'lender_page', source: 'official', lender: 'ICICI Bank',
    url: 'https://www.icicibank.com/personal-banking/loans/personal-loan/eligibility' },
];

return targets.map(t => ({ json: t }));
