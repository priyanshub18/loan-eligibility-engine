// Parse the LLM's JSON answers (one per lender page). Items line up 1:1 with
// the "Plan LLM extraction" items. Bad / failed responses become null and the
// finaliser falls back to cache or heuristics for that page.
const requests = $('Plan LLM extraction').all().map(i => i.json);

function firstJsonObject(text) {
  const m = String(text || '').match(/\{[\s\S]*\}/);
  if (!m) return null;
  try { return JSON.parse(m[0]); } catch { return null; }
}

return $input.all().map((item, idx) => {
  const content = item.json.choices?.[0]?.message?.content;
  const extracted = firstJsonObject(content);
  return { json: { url: requests[idx].url, extracted, error: extracted ? null : (item.json.error?.message || 'unparseable LLM response') } };
});
