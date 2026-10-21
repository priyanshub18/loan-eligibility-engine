// STAGE 3b - batch the cache misses into a few LLM calls.
// 25 questions per request and a hard per-run call budget keep latency and
// cost bounded no matter how large the upload is. Questions over budget are
// not dropped: they fall back to the rules verdict and get asked next run.
const s2 = $('Stage 2 · Score, top-K, bucket').first().json;
const cached = new Set($input.all().map(i => i.json.cache_key).filter(Boolean));
const misses = Object.keys(s2.questions).filter(k => !cached.has(k));

const PER_CALL = 25;
const MAX_CALLS = Number($env.LLM_MAX_CALLS_PER_RUN || 20);
if (!$env.LLM_API_KEY || !misses.length) {
  return [{ json: { skip: true, misses: misses.length, cache_hits: cached.size, reason: $env.LLM_API_KEY ? 'no cache misses' : 'LLM disabled' } }];
}

const products = new Map(s2.products.map(p => [p.product_id, p]));
const describe = p => ({
  product_id: p.product_id, lender: p.lender, product: p.product_name,
  min_monthly_income: p.min_monthly_income, min_credit_score: p.min_credit_score,
  age: `${p.min_age}-${p.max_age}`, employment_types: p.employment_types ?? 'any',
  other_criteria: p.other_criteria ?? 'none published',
});

const SYSTEM = `You are a conservative credit analyst for Indian unsecured personal loans.
Each case is an applicant PROFILE (ranges, not a single person) that already passes the lender's
hard minimums but sits close to a threshold. Decide if a typical applicant in that profile is
LIKELY to be approved, considering the lender's qualitative criteria, the borderline flags and
standard Indian lending practice (bureau score cut-offs, FOIR, employment stability).
Answer ONLY with JSON: {"decisions":[{"id":"<case id>","eligible":true|false,"reason":"<= 20 words"}]}`;

const toAsk = misses.slice(0, PER_CALL * MAX_CALLS);
const calls = [];
for (let i = 0; i < toAsk.length; i += PER_CALL) {
  const chunk = toAsk.slice(i, i + PER_CALL);
  const productIds = [...new Set(chunk.map(k => s2.questions[k].product_id))];
  const cases = chunk.map((k, j) => ({ id: `c${j + 1}`, ...s2.questions[k] }));
  calls.push({
    json: {
      keys: chunk,
      llm_request: {
        model: $env.LLM_MODEL || 'gemini-3.5-flash-lite',
        temperature: 0,
        messages: [
          { role: 'system', content: SYSTEM },
          { role: 'user', content: JSON.stringify({ products: productIds.map(id => describe(products.get(id))), cases }) },
        ],
      },
    },
  });
}
return calls;
