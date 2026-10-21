// Map the LLM's short case ids ("c1".."c25") back to bucket keys.
// One output item carrying all verdicts, ready for a single bulk cache insert.
const s2 = $('Stage 2 · Score, top-K, bucket').first().json;
const plans = $('Plan LLM batches').all().map(i => i.json);

const rows = [];
let failedCalls = 0;
$input.all().forEach((item, idx) => {
  const keys = plans[idx].keys;
  const text = item.json.choices?.[0]?.message?.content || '';
  let decisions = [];
  try { decisions = JSON.parse(text.match(/\{[\s\S]*\}/)[0]).decisions || []; } catch { failedCalls++; return; }
  for (const d of decisions) {
    const key = keys[Number(String(d.id).replace(/\D/g, '')) - 1];
    if (!key || typeof d.eligible !== 'boolean') continue;
    rows.push({ cache_key: key, product_id: s2.questions[key].product_id, eligible: d.eligible,
                reason: String(d.reason || '').slice(0, 300), model: plans[idx].llm_request.model });
  }
});

return [{ json: { rows_json: JSON.stringify(rows), verdicts: rows.length, llm_calls: plans.length, failed_calls: failedCalls } }];
