INSERT INTO llm_decision_cache (cache_key, product_id, eligible, reason, model)
SELECT cache_key, product_id, eligible, reason, model
FROM jsonb_to_recordset($1::jsonb) AS x(cache_key text, product_id int, eligible boolean, reason text, model text)
ON CONFLICT (cache_key) DO UPDATE
    SET eligible = EXCLUDED.eligible, reason = EXCLUDED.reason, model = EXCLUDED.model, created_at = now()
RETURNING cache_key;
