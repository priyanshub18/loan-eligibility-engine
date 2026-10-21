// Merge every source into one row per lender and hand a single JSON payload to
// Postgres (one bulk upsert instead of one query per product).
//
// Field precedence per lender:  official site > aggregator page
//                    per page:  fresh LLM > cached extraction > regex heuristic
// Anything still missing gets conservative industry defaults, flagged as
// criteria_source = 'default' so the matcher can down-weight it.

const pages = $('Clean, parse & fingerprint').all().map(i => i.json);
const cache = new Map($('Load crawl cache').all().filter(i => i.json.url).map(i => [i.json.url, i.json]));
let fresh = new Map();
try {
  fresh = new Map($('Parse LLM output').all().filter(i => i.json.extracted).map(i => [i.json.url, i.json.extracted]));
} catch (e) { /* LLM branch did not run this time */ }

const DEFAULTS = { min_monthly_income: 25000, min_credit_score: 750, min_age: 21, max_age: 60 };
const FIELDS = ['interest_rate_min', 'interest_rate_max', 'min_monthly_income', 'min_credit_score', 'min_age',
  'max_age', 'employment_types', 'max_loan_amount', 'other_criteria', 'product_name'];
const EMP = new Set(['salaried', 'self_employed', 'business']);

const slug = s => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const canonicalLender = name => name.replace(/\s+(personal loan|ltd\.?|limited)$/i, '').trim();
const isNum = v => typeof v === 'number' && Number.isFinite(v);

function clean(e = {}) {
  const out = {};
  for (const f of FIELDS) {
    const v = e[f];
    if (v === null || v === undefined || v === '') continue;
    if (f === 'employment_types') {
      const list = (Array.isArray(v) ? v : []).map(x => String(x).toLowerCase().replace(/[- ]/g, '_')).filter(x => EMP.has(x));
      if (list.length) out[f] = [...new Set(list)];
    } else if (f === 'other_criteria' || f === 'product_name') {
      out[f] = String(v).slice(0, 300);
    } else if (isNum(Number(v))) {
      out[f] = Number(v);
    }
  }
  // sanity bounds - an LLM or regex misread must not produce absurd criteria
  const outside = (f, lo, hi) => out[f] !== undefined && (out[f] < lo || out[f] > hi);
  if (outside('min_credit_score', 300, 900)) delete out.min_credit_score;
  if (outside('min_monthly_income', 1000, 1000000)) delete out.min_monthly_income;
  if (outside('interest_rate_min', 5, 45)) delete out.interest_rate_min;
  if (outside('interest_rate_max', 5, 45) || out.interest_rate_max < out.interest_rate_min) delete out.interest_rate_max;
  if (outside('min_age', 18, 70) || outside('max_age', 21, 80)) { delete out.min_age; delete out.max_age; }
  return out;
}

// --- 1. per-page extraction, choosing the best available source -------------
const lenders = new Map(); // lender -> { official: {...}, aggregator: {...}, urls, sources }
const cacheRows = [];
for (const p of pages.filter(p => p.kind === 'lender_page' && p.ok)) {
  let extracted, via;
  if (fresh.has(p.url)) { extracted = fresh.get(p.url); via = 'llm'; }
  else if (cache.get(p.url)?.content_hash === p.content_hash && cache.get(p.url)?.extracted) {
    extracted = cache.get(p.url).extracted; via = cache.get(p.url).extracted._via || 'llm';
  } else { extracted = p.heuristic; via = 'heuristic'; }

  const record = { ...clean(p.heuristic), ...clean(extracted) }; // LLM/cached values override regex
  // Only cache LLM results: a heuristic-only page must be retried once an LLM key is available.
  if (via === 'llm') cacheRows.push({ url: p.url, content_hash: p.content_hash, extracted: { ...record, _via: via } });

  const key = canonicalLender(p.lender);
  const entry = lenders.get(key) || { lender: key, official: null, aggregator: null, url: p.url, via: new Set() };
  entry[p.source === 'official' ? 'official' : 'aggregator'] = record;
  if (p.source === 'official') entry.url = p.url;
  entry.via.add(via);
  lenders.set(key, entry);
}

// --- 2. add rate-table lenders (rates + fees for many lenders in one page) ---
const rateRows = pages.find(p => p.kind === 'rate_table' && p.ok)?.rates || [];
const rateUrl = pages.find(p => p.kind === 'rate_table')?.url;
for (const r of rateRows) {
  const key = canonicalLender(r.lender);
  const entry = lenders.get(key) || { lender: key, official: null, aggregator: null, url: rateUrl, via: new Set(['default']) };
  entry.table = r;
  lenders.set(key, entry);
}

// --- 3. merge into product rows ---------------------------------------------
const products = [];
for (const e of lenders.values()) {
  const merged = { ...(e.table ? clean(e.table) : {}), ...(e.aggregator || {}), ...(e.official || {}) };
  const usedDefaults = Object.keys(DEFAULTS).filter(k => merged[k] === undefined);
  for (const k of usedDefaults) merged[k] = DEFAULTS[k];
  const criteriaSource = usedDefaults.length === Object.keys(DEFAULTS).length ? 'default'
    : e.via.has('llm') ? 'llm' : 'heuristic';
  products.push({
    product_key: `${slug(e.lender)}-personal-loan`,
    lender: e.lender,
    product_name: merged.product_name || `${e.lender} Personal Loan`,
    interest_rate_min: merged.interest_rate_min ?? null,
    interest_rate_max: merged.interest_rate_max ?? null,
    processing_fee: e.table?.processing_fee ?? null,
    min_monthly_income: merged.min_monthly_income,
    min_credit_score: merged.min_credit_score,
    max_credit_score: null,
    min_age: merged.min_age,
    max_age: merged.max_age,
    employment_types: merged.employment_types ?? null,
    max_loan_amount: merged.max_loan_amount ?? null,
    other_criteria: merged.other_criteria ?? null,
    source_url: e.url,
    criteria_source: criteriaSource,
  });
}

const summary = {
  pages_ok: pages.filter(p => p.ok).length,
  pages_failed: pages.filter(p => !p.ok).map(p => p.url),
  llm_extractions: fresh.size,
  products: products.length,
  by_criteria_source: products.reduce((a, p) => ({ ...a, [p.criteria_source]: (a[p.criteria_source] || 0) + 1 }), {}),
};
if (!products.length) throw new Error(`Crawl produced no products: ${JSON.stringify(summary)}`);

return [{ json: { products_json: JSON.stringify(products), cache_json: JSON.stringify(cacheRows), summary } }];
