-- Log the sends, stamp the matches as notified and close out the batch, atomically.
-- $1 = sent rows (JSON), $2 = batch_id, $3 = JSON-encoded error summary ('' if none).
WITH sent AS (
    SELECT * FROM jsonb_to_recordset($1::jsonb)
        AS x(user_id text, sent_to text, product_ids int[], batch_id uuid, ses_message_id text)
),
logged AS (
    INSERT INTO notifications (user_id, sent_to, product_ids, batch_id, ses_message_id)
    SELECT user_id, sent_to, product_ids, batch_id, ses_message_id FROM sent
    RETURNING 1
),
stamped AS (
    UPDATE matches m SET notified_at = now()
    FROM sent
    WHERE m.user_id = sent.user_id AND m.product_id = ANY (sent.product_ids)
    RETURNING 1
),
batch AS (
    -- 'notified' if at least one email went out, otherwise surface the SES error to the UI
    UPDATE upload_batches
    SET status = CASE WHEN (SELECT count(*) FROM sent) > 0 THEN 'notified' ELSE 'notify_failed' END,
        error = NULLIF($3::jsonb #>> '{}', ''),   -- $3 arrives JSON-encoded (may contain commas)
        updated_at = now(),
        match_stats = COALESCE(match_stats, '{}'::jsonb) || jsonb_build_object('emails_sent', (SELECT count(*) FROM sent))
    WHERE batch_id IN (SELECT DISTINCT batch_id FROM sent) OR batch_id::text = $2
    RETURNING 1
)
SELECT (SELECT count(*) FROM logged)::int  AS emails_logged,
       (SELECT count(*) FROM stamped)::int AS matches_marked,
       (SELECT count(*) FROM batch)::int   AS batches_closed;
