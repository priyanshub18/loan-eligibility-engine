// Final decision per pair:
//   clear pair                         -> match (decision_source = rules)
//   borderline + LLM said eligible     -> match (llm / llm_cache) with the LLM's reason
//   borderline + LLM said NOT eligible -> rejected
//   borderline + no verdict available  -> match (rules_fallback) - hard criteria already passed
// Runs exactly once whichever IF branch was taken (LLM or skip).
const s2 = $('Stage 2 · Score, top-K, bucket').first().json;

const verdicts = new Map();
for (const i of $('LLM cache lookup').all()) {
  if (i.json.cache_key) verdicts.set(i.json.cache_key, { ...i.json, source: 'llm_cache' });
}
let llmMeta = { llm_calls: 0, failed_calls: 0 };
try {
  const parsed = $('Parse LLM verdicts').first().json;
  for (const r of JSON.parse(parsed.rows_json)) verdicts.set(r.cache_key, { ...r, source: 'llm' });
  llmMeta = { llm_calls: parsed.llm_calls, failed_calls: parsed.failed_calls };
} catch (e) { /* LLM branch skipped */ }

const matches = s2.clear.map(([user_id, product_id, score, maxLoan]) => ({
  user_id, product_id, score, decision_source: 'rules',
  reason: `Meets all published criteria; est. affordable loan up to ₹${maxLoan.toLocaleString('en-IN')}`,
}));

let rejected = 0, fallback = 0, cacheHits = 0;
for (const [user_id, product_id, score, maxLoan, key] of s2.borderline) {
  const v = verdicts.get(key);
  if (!v) {
    fallback++;
    matches.push({ user_id, product_id, score, decision_source: 'rules_fallback',
                   reason: 'Borderline on a threshold; passes all hard criteria' });
  } else if (v.eligible) {
    if (v.source === 'llm_cache') cacheHits++;
    matches.push({ user_id, product_id, score, decision_source: v.source, reason: v.reason });
  } else {
    rejected++;
  }
}

const stats = {
  ...s2.stats,
  stage3_cache_hit_buckets: $('LLM cache lookup').all().filter(i => i.json.cache_key).length,
  stage3_llm_calls: llmMeta.llm_calls,
  stage3_llm_failed_calls: llmMeta.failed_calls,
  stage3_llm_rejected_pairs: rejected,
  stage3_rules_fallback_pairs: fallback,
  stage3_pairs_from_cache: cacheHits,
  final_matches: matches.length,
  users_matched: new Set(matches.map(m => m.user_id)).size,
  duration_ms: Date.now() - new Date($('Validate payload').first().json.started_at).getTime(),
};

return [{ json: { batch_id: s2.batch_id, matches_json: JSON.stringify(matches), stats_json: JSON.stringify(stats), stats } }];
