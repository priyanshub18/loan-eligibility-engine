-- Bulk-insert all matches and close out the batch in a single statement.
-- Re-uploaded users: un-notified matches that no longer qualify are pruned.
WITH incoming AS (
    SELECT * FROM jsonb_to_recordset($2::jsonb)
        AS x(user_id text, product_id int, score numeric, decision_source text, reason text)
),
stale AS (
    DELETE FROM matches m
    USING users u
    WHERE u.user_id = m.user_id AND u.batch_id = $1::uuid AND m.notified_at IS NULL
      AND NOT EXISTS (SELECT 1 FROM incoming i WHERE i.user_id = m.user_id AND i.product_id = m.product_id)
    RETURNING 1
),
saved AS (
    INSERT INTO matches (user_id, product_id, batch_id, score, decision_source, reason)
    SELECT user_id, product_id, $1::uuid, score, decision_source, reason FROM incoming
    ON CONFLICT (user_id, product_id) DO UPDATE
        SET score = EXCLUDED.score, decision_source = EXCLUDED.decision_source,
            reason = EXCLUDED.reason, batch_id = EXCLUDED.batch_id
    RETURNING 1
)
UPDATE upload_batches
SET status = 'matched', match_stats = $3::jsonb, updated_at = now()
WHERE batch_id = $1::uuid
RETURNING batch_id::text, (SELECT count(*) FROM saved)::int AS matches_saved,
          (SELECT count(*) FROM stale)::int AS stale_matches_removed;
