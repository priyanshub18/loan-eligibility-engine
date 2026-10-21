-- STAGE 3a - reuse earlier LLM verdicts for identical (product, profile-bucket) questions.
SELECT cache_key, eligible, reason
FROM llm_decision_cache
WHERE cache_key IN (SELECT jsonb_array_elements_text($1::jsonb));
