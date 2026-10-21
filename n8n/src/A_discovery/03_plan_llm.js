// Decide which lender pages actually need the LLM.
// A page is sent only if (a) its snippet hash changed since the last crawl and
// (b) an LLM key is configured. Otherwise we reuse the cached extraction or
// fall back to the regex heuristics. Always emits >= 1 item so the IF node
// downstream runs (n8n stops a branch that receives zero items).

const pages = $('Clean, parse & fingerprint').all().map(i => i.json);
const cache = new Map($input.all().filter(i => i.json.url).map(i => [i.json.url, i.json]));
const llmEnabled = Boolean($env.LLM_API_KEY);

const SYSTEM = `You extract personal-loan eligibility criteria from Indian bank web page text.
Return ONLY a JSON object with these keys (use null when the text does not state it):
{"product_name": string, "interest_rate_min": number, "interest_rate_max": number,
 "min_monthly_income": number (INR per month), "min_credit_score": number,
 "min_age": number, "max_age": number,
 "employment_types": array of "salaried" | "self_employed" | "business" (null if not restricted),
 "max_loan_amount": number (INR), "other_criteria": string (<= 200 chars, qualitative rules
 such as work experience or employer type)}.
If different minimum incomes are given for different cities or employment types, use the LOWEST.`;

const work = pages
  .filter(p => p.kind === 'lender_page' && p.ok)
  .filter(p => cache.get(p.url)?.content_hash !== p.content_hash)
  .filter(() => llmEnabled)
  .map(p => ({
    json: {
      url: p.url,
      llm_request: {
        model: $env.LLM_MODEL || 'gemini-3.5-flash-lite',
        temperature: 0,
        messages: [
          { role: 'system', content: SYSTEM },
          { role: 'user', content: `Lender: ${p.lender}\nPage text:\n${p.snippet}` },
        ],
      },
    },
  }));

return work.length ? work : [{ json: { skip: true, reason: llmEnabled ? 'all pages unchanged' : 'LLM disabled' } }];
