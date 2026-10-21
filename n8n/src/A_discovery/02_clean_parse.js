// Turns raw HTML into (a) rate-table rows and (b) a compact eligibility
// snippet per lender page, plus cheap regex "heuristic" extraction.
//
// Cost trick #1: the LLM never sees a full page. We keep only ~3 KB of text
// around eligibility keywords (pages are 200 KB - 1.2 MB of HTML), which cuts
// tokens by ~98% and also improves extraction accuracy (less noise).
// Cost trick #2: a content hash of that snippet lets the next node skip the
// LLM entirely when the page has not changed since the last crawl.

const targets = $('Crawl targets').all().map(i => i.json);
const pages = $input.all();

// ---------------------------------------------------------------- helpers
const ENTITIES = { '&amp;': '&', '&nbsp;': ' ', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&#8377;': '₹', '&rsquo;': "'", '&ndash;': '-', '&mdash;': '-' };
const decode = s => s.replace(/&[#a-z0-9]+;/gi, m => ENTITIES[m.toLowerCase()] ?? ' ');

function htmlToText(html) {
  return decode(
    html.replace(/<(script|style|noscript|svg|header|footer|nav)[\s\S]*?<\/\1>/gi, ' ')
        .replace(/<[^>]+>/g, ' ')
  ).replace(/\s+/g, ' ').trim();
}

// cyrb53: fast 53-bit string hash - plenty for change detection, no crypto module needed.
function hash(str) {
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
  for (let i = 0; i < str.length; i++) {
    const ch = str.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(16);
}

const num = s => Number(String(s).replace(/,/g, ''));

function parseRates(text) {
  // first "a% - b%" range that looks like an interest rate (skips fee ranges such as "0% - 6%")
  for (const m of text.matchAll(/(\d{1,2}(?:\.\d{1,2})?)\s*%(?:\s*p\.a\.?)?\s*(?:to|-|–)\s*(\d{1,2}(?:\.\d{1,2})?)\s*%/gi)) {
    const lo = num(m[1]), hi = num(m[2]);
    if (lo >= 5 && hi <= 45 && hi >= lo) return { interest_rate_min: lo, interest_rate_max: hi };
  }
  const single = text.match(/(\d{1,2}(?:\.\d{1,2})?)\s*%\s*p\.a\./i);
  return single && num(single[1]) >= 5 ? { interest_rate_min: num(single[1]), interest_rate_max: null } : {};
}

function parseRateTable(html) {
  const tables = html.match(/<table[\s\S]*?<\/table>/gi) || [];
  for (const table of tables) {
    const rows = (table.match(/<tr[\s\S]*?<\/tr>/gi) || []).map(r =>
      (r.match(/<t[dh][\s\S]*?<\/t[dh]>/gi) || []).map(c => htmlToText(c)));
    if (!rows.length || !/interest/i.test(rows[0].join(' '))) continue;
    return rows.slice(1)
      .filter(r => r.length >= 2 && r[0] && /\d/.test(r[1]))
      .map(r => ({ lender: r[0], processing_fee: r[2] || null, ...parseRates(r[1]) }));
  }
  return [];
}

const KEYWORDS = /eligib|minimum (net )?(monthly )?(income|salary)|credit score|cibil|age (limit|criteria|of)|years of age|salaried|self[- ]employed|work experience|loan amount/gi;

// Facts look like "Rs. 25,000", "750", "21 years", "10.5%": windows dense in them are
// eligibility tables; windows without them are navigation menus or marketing copy.
const FACT = /(?:rs\.?|₹|inr)\s*[\d,]{4,}|\b[3-9]\d{2}\b|\b\d{2}\s*years|\d%/gi;

function eligibilitySnippet(text, maxLen = 3500) {
  const windows = [];
  for (const m of text.matchAll(KEYWORDS)) {
    const start = Math.max(0, m.index - 200), end = Math.min(text.length, m.index + 300);
    const last = windows[windows.length - 1];
    if (last && start <= last.end) last.end = end; else windows.push({ start, end });
  }
  // keep the most fact-dense windows that fit the budget, then restore page order
  const ranked = windows
    .map(w => ({ ...w, facts: (text.slice(w.start, w.end).match(FACT) || []).length }))
    .filter(w => w.facts > 0)
    .sort((a, b) => b.facts / (b.end - b.start) - a.facts / (a.end - a.start));
  const picked = [];
  let used = 0;
  for (const w of ranked) {
    const len = Math.min(w.end - w.start, 800);
    if (used + len > maxLen) continue;
    picked.push({ start: w.start, end: w.start + len });
    used += len;
  }
  return picked.sort((a, b) => a.start - b.start).map(w => text.slice(w.start, w.end)).join(' … ');
}

// Regex fallback, run over the whole page text (cheap) - used when no LLM is
// configured and to pre-fill fields the LLM leaves empty.
function heuristics(text, html) {
  const h = {};
  const incomes = [...text.matchAll(/minimum[^.]{0,30}?(?:monthly\s+)?(?:income|salary|nmi)[^₹\d]{0,40}?(?:rs\.?|₹|inr)\s*([\d,]{4,9})/gi)]
    .map(m => num(m[1])).filter(v => v >= 5000 && v <= 500000);
  // some lender pages embed their eligibility calculator config, e.g. "minIncome":30000
  const calc = html.match(/minIncome["']?\s*:\s*["']?(\d{4,7})/);
  if (calc) incomes.push(num(calc[1]));
  if (incomes.length) h.min_monthly_income = Math.min(...incomes);

  const cs = text.match(/(?:credit|cibil)\s+score\s+(?:of\s+|over\s+|above\s+|at least\s+|minimum\s+)?([5-8]\d{2})\b/i);
  if (cs) h.min_credit_score = num(cs[1]);

  const age = text.match(/age\s+(?:at least|between|of|from)?\s*(\d{2})\s*(?:years)?\s*(?:and|to|-)\s*(?:less than|up to|upto|below)?\s*(\d{2})/i)
           || text.match(/(\d{2})\s*(?:years)?\s*(?:to|-|–)\s*(\d{2})\s*years/i);
  if (age && num(age[1]) >= 18 && num(age[2]) <= 80 && num(age[1]) < num(age[2])) {
    h.min_age = num(age[1]); h.max_age = num(age[2]);
  }
  if (/(?:must be|should be|only)\s+salaried|salaried (?:indian )?residents/i.test(text)) h.employment_types = ['salaried'];

  const amt = text.match(/up to\s*(?:rs\.?|₹)\s*([\d.]+)\s*(lakh|crore|l\b)/i);
  if (amt) h.max_loan_amount = num(amt[1]) * (/crore/i.test(amt[2]) ? 1e7 : 1e5);
  Object.assign(h, parseRates(text));
  return h;
}

// ---------------------------------------------------------------- main
const out = [];
pages.forEach((page, idx) => {
  const t = targets[idx];
  const html = page.json.html;
  if (!html || page.json.error) {
    out.push({ json: { ...t, ok: false, error: String(page.json.error?.message || page.json.error || 'empty response') } });
    return;
  }
  if (t.kind === 'rate_table') {
    out.push({ json: { ...t, ok: true, rates: parseRateTable(html) } });
    return;
  }
  const text = htmlToText(html);
  const snippet = eligibilitySnippet(text);
  out.push({ json: { ...t, ok: snippet.length > 0, snippet, content_hash: hash(snippet), heuristic: heuristics(text, html) } });
});
return out;
