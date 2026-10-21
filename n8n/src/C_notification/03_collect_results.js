// Pair each provider response (Gmail SMTP or SES - only one branch runs) with
// the email it belongs to. Only accepted emails are logged and marked as
// notified; failures stay pending and are retried on the next run.
const emails = $('Build personalised emails').all().map(i => i.json);
const sent = [];
const failed = [];
$input.all().forEach((item, idx) => {
  const r = item.json;
  const messageId = r.messageId /* SMTP (nodemailer) */ || r.MessageId || r.SendEmailResult?.MessageId || r.SendEmailResponse?.SendEmailResult?.MessageId;
  const e = emails[idx];
  if (messageId) sent.push({ user_id: e.user_id, sent_to: e.to, product_ids: e.product_ids, batch_id: e.batch_id, ses_message_id: messageId });
  else failed.push({ user_id: e.user_id, error: r.error?.message || r.error || 'no MessageId' });
});
const batchId = $('Matching finished (webhook)').first().json.body?.batch_id || emails[0]?.batch_id || null;
return [{ json: { batch_id: batchId, sent_json: JSON.stringify(sent), sent: sent.length, failed,
                  first_error: failed[0] ? `${failed.length} email(s) failed: ${failed[0].error}` : '' } }];
