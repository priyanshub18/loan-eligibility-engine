// STAGE 2 - deterministic scoring, top-K pruning and borderline detection (pure JS, O(pairs)).
//
// 1. Score every Stage-1 pair 0-100 from credit headroom, income headroom,
//    the product's best rate and an affordability check (FOIR).
// 2. Keep only each user's top-K products: nobody needs an email with 14 loans,
//    and every pair we drop here is a pair the LLM never has to look at.
// 3. Split survivors into CLEAR (comfortably eligible -> accepted, no LLM) and
//    BORDERLINE (within a hair of a threshold -> qualitative LLM review).
// 4. Collapse borderline pairs into coarse PROFILE BUCKETS
//    (product x employment x ONLY the dimensions that are actually borderline:
//    a credit-borderline user is bucketed by credit band, and their income is
//    just "comfortably above minimum"). Users in the same bucket get the same
//    answer, so the LLM is asked once per bucket, not once per user - and that
//    answer is cached across batches.

const s1 = $input.first().json;
const MAX_PER_USER = Number($env.MAX_MATCHES_PER_USER || 5);
const FOIR = 0.5;          // max share of income that may go to EMIs (RBI-style fixed-obligation ratio)
const TENURE_MONTHS = 60;
const MIN_USEFUL_LOAN = 50000; // if the user can't service even this, the match is pointless

const products = new Map(s1.products.map(p => [p.product_id, p]));
const clamp01 = x => Math.max(0, Math.min(1, x));
const band = (v, w) => Math.floor(v / w) * w;
const emiPerRupee = (ratePct, n) => { const r = ratePct / 1200; return r ? r / (1 - Math.pow(1 + r, -n)) : 1 / n; };

const byUser = new Map();
let droppedUnaffordable = 0;

for (const [userId, productId, incomeRaw, credit, age, emp] of s1.pairs) {
  const p = products.get(productId);
  const income = Number(incomeRaw);
  const minIncome = Number(p.min_monthly_income || 0);
  const minCredit = Number(p.min_credit_score || 300);
  const rate = Number(p.interest_rate_max ?? p.interest_rate_min ?? 16); // be conservative: assume worst rate

  const affordableLoan = (income * FOIR) / emiPerRupee(rate, TENURE_MONTHS);
  if (affordableLoan < MIN_USEFUL_LOAN) { droppedUnaffordable++; continue; }

  const creditHeadroom = Number(credit) - minCredit;
  const incomeHeadroom = minIncome ? income / minIncome - 1 : 1;
  const ageEdge = age - (p.min_age ?? 18) <= 1 ? 'min' : (p.max_age ?? 100) - age <= 1 ? 'max' : 'mid';

  let score = 100 * (0.40 * clamp01(creditHeadroom / 100)
                   + 0.30 * clamp01(incomeHeadroom)
                   + 0.30 * clamp01((24 - Number(p.interest_rate_min ?? 16)) / 16));
  if (p.criteria_source === 'default') score *= 0.85; // lender didn't publish criteria: less confident

  const reasons = [];
  if (creditHeadroom < 20) reasons.push(`credit score ${credit} is within 20 of the ${minCredit} minimum`);
  if (minIncome && incomeHeadroom < 0.10) reasons.push(`income ${income} is within 10% of the ${minIncome} minimum`);
  if (ageEdge !== 'mid') reasons.push(`age ${age} is at the ${ageEdge} edge of ${p.min_age}-${p.max_age}`);

  const list = byUser.get(userId) || [];
  list.push({ userId, productId, score: Math.round(score * 100) / 100, reasons, income, credit, age, emp, ageEdge,
              maxLoan: Math.round(Math.min(affordableLoan, Number(p.max_loan_amount) || Infinity)) });
  byUser.set(userId, list);
}

const clear = [];       // [userId, productId, score, maxLoan]
const borderline = [];  // [userId, productId, score, maxLoan, bucketKey]
const questions = {};   // bucketKey -> what the LLM needs to judge it
let afterTopK = 0;

for (const list of byUser.values()) {
  list.sort((a, b) => b.score - a.score);
  for (const m of list.slice(0, MAX_PER_USER)) {
    afterTopK++;
    if (!m.reasons.length) { clear.push([m.userId, m.productId, m.score, m.maxLoan]); continue; }
    const incBand = band(m.income, 5000), csBand = band(m.credit, 10);
    const flagged = { income: m.reasons.some(r => r.startsWith('income')),
                      credit: m.reasons.some(r => r.startsWith('credit')), age: m.ageEdge !== 'mid' };
    const profile = {
      monthly_income: flagged.income ? `${incBand}-${incBand + 4999}` : 'comfortably above minimum',
      credit_score: flagged.credit ? `${csBand}-${csBand + 9}` : 'comfortably above minimum',
      employment: m.emp,
      age: flagged.age ? m.age : 'within range',
    };
    const key = `v2|p${m.productId}|${m.emp}|inc:${flagged.income ? incBand : 'ok'}|cs:${flagged.credit ? csBand : 'ok'}|age:${flagged.age ? m.age : 'ok'}`;
    borderline.push([m.userId, m.productId, m.score, m.maxLoan, key]);
    if (!questions[key]) questions[key] = { product_id: m.productId, profile, flags: m.reasons.map(r => r.replace(/\d+ is/, 'is')) };
  }
}

const stats = {
  users_in_batch: s1.users_in_batch,
  products_active: s1.products_active,
  naive_pairs: s1.users_in_batch * s1.products_active,
  stage1_sql_pairs: s1.stage1_pairs,
  stage2_dropped_unaffordable: droppedUnaffordable,
  stage2_after_top_k: afterTopK,
  stage2_clear: clear.length,
  stage2_borderline: borderline.length,
  stage3_unique_questions: Object.keys(questions).length,
};

return [{ json: { batch_id: s1.batch_id, products: s1.products, clear, borderline, questions,
                  question_keys_json: JSON.stringify(Object.keys(questions)), stats } }];
