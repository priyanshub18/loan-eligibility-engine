// Build one personalised HTML email per user (provider-agnostic: Gmail SMTP or SES).
//
// SAFETY: the sample CSV contains other people's addresses, so recipients are
// ALWAYS rewritten to test-inbox aliases (round-robin over TEST_INBOX_ALIASES,
// e.g. you+one@gmail.com,you+two@gmail.com). If none are configured we refuse
// to send rather than fall back to the real address.
const aliases = String($env.TEST_INBOX_ALIASES || '').split(',').map(s => s.trim()).filter(Boolean);
if (!aliases.length) throw new Error('TEST_INBOX_ALIASES is not set - refusing to email real user addresses');

const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const inr = n => `₹${Number(n).toLocaleString('en-IN')}`;
const rate = p => p.rate_min == null ? 'See lender' : p.rate_max ? `${p.rate_min}% – ${p.rate_max}% p.a.` : `from ${p.rate_min}% p.a.`;

const users = $input.all().filter(i => i.json.user_id);
return users.map((item, idx) => {
  const u = item.json;
  const firstName = (u.name || 'there').split(' ')[0];
  const products = u.products;

  const rows = products.map(p => `
      <tr>
        <td style="padding:10px;border-bottom:1px solid #eee"><strong>${esc(p.product_name)}</strong><br>
          <span style="color:#666;font-size:12px">${esc(p.lender)}</span></td>
        <td style="padding:10px;border-bottom:1px solid #eee;white-space:nowrap">${esc(rate(p))}</td>
        <td style="padding:10px;border-bottom:1px solid #eee;color:#555;font-size:13px">${esc(p.reason)}</td>
        <td style="padding:10px;border-bottom:1px solid #eee"><a href="${esc(p.source_url)}" style="color:#0b6bcb">Details</a></td>
      </tr>`).join('');

  const html = `<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;color:#222;max-width:680px;margin:auto">
    <h2 style="color:#0b6bcb;margin-bottom:4px">Hi ${esc(firstName)}, good news!</h2>
    <p>Based on your profile (monthly income ${inr(u.monthly_income)}, credit score ${u.credit_score}),
       you may qualify for <strong>${products.length} personal loan${products.length > 1 ? 's' : ''}</strong>:</p>
    <table style="border-collapse:collapse;width:100%;font-size:14px">
      <thead><tr style="background:#f3f7fb;text-align:left">
        <th style="padding:10px">Product</th><th style="padding:10px">Interest rate</th>
        <th style="padding:10px">Why you match</th><th style="padding:10px"></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p style="font-size:12px;color:#777;margin-top:24px">This is an indicative pre-qualification based on publicly
       listed criteria, not a loan offer. Final approval is at the lender's discretion.<br>
       [Demo] Original recipient: ${esc(u.email)} · user ${esc(u.user_id)}</p>
  </body></html>`;

  return {
    json: {
      user_id: u.user_id,
      batch_id: u.batch_id,
      product_ids: products.map(p => p.product_id),
      to: aliases[idx % aliases.length],
      subject: `${firstName}, you may qualify for ${products.length} personal loan${products.length > 1 ? 's' : ''}`,
      html,
    },
  };
});
