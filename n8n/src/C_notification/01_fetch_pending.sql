-- Users with matches that have never been emailed, one row per user with their
-- products pre-aggregated (so the email builder needs no extra queries).
-- $1 = batch_id ('any' = every batch), $2 = max emails this run (demo safety cap).
WITH pending AS (
    SELECT m.user_id,
           max(m.batch_id::text) AS batch_id,
           max(m.score)          AS best_score,
           json_agg(json_build_object(
               'product_id', p.product_id, 'lender', p.lender, 'product_name', p.product_name,
               'rate_min', p.interest_rate_min, 'rate_max', p.interest_rate_max,
               'processing_fee', p.processing_fee, 'score', m.score, 'reason', m.reason,
               'source_url', p.source_url) ORDER BY m.score DESC) AS products
    FROM matches m
    JOIN loan_products p USING (product_id)
    WHERE m.notified_at IS NULL
      AND ($1 = 'any' OR m.batch_id = NULLIF($1, 'any')::uuid)
    GROUP BY m.user_id
)
SELECT u.user_id, u.name, u.email, u.monthly_income, u.credit_score, u.employment_status,
       pending.batch_id, pending.products
FROM pending
JOIN users u USING (user_id)
ORDER BY json_array_length(pending.products) DESC, pending.best_score DESC
LIMIT $2::int;
