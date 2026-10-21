// The webhook is already authenticated (Header Auth credential); here we only
// make sure the payload is a real batch id before touching the database.
const batchId = String($json.body?.batch_id ?? $json.batch_id ?? '').trim().toLowerCase();
if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(batchId)) {
  throw new Error(`Invalid or missing batch_id in webhook payload: ${JSON.stringify($json.body ?? {})}`);
}
return { json: { batch_id: batchId, started_at: new Date().toISOString() } };
