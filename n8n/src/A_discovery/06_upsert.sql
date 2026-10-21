-- One round-trip: upsert all products, refresh the crawl cache, and retire
-- products that have not been seen for 7 days (delisted by the lender).
WITH incoming AS (
    SELECT * FROM jsonb_to_recordset($1::jsonb) AS x(
        product_key text, lender text, product_name text,
        interest_rate_min numeric, interest_rate_max numeric, processing_fee text,
        min_monthly_income numeric, min_credit_score int, max_credit_score int,
        min_age int, max_age int, employment_types text[], max_loan_amount numeric,
        other_criteria text, source_url text, criteria_source text)
),
upserted AS (
    INSERT INTO loan_products (product_key, lender, product_name, interest_rate_min, interest_rate_max,
                               processing_fee, min_monthly_income, min_credit_score, max_credit_score,
                               min_age, max_age, employment_types, max_loan_amount, other_criteria,
                               source_url, criteria_source)
    SELECT product_key, lender, product_name, interest_rate_min, interest_rate_max,
           processing_fee, min_monthly_income, min_credit_score, max_credit_score,
           min_age, max_age, employment_types, max_loan_amount, other_criteria,
           source_url, criteria_source
    FROM incoming
    ON CONFLICT (product_key) DO UPDATE SET
        lender = EXCLUDED.lender, product_name = EXCLUDED.product_name,
        interest_rate_min = EXCLUDED.interest_rate_min, interest_rate_max = EXCLUDED.interest_rate_max,
        processing_fee = EXCLUDED.processing_fee, min_monthly_income = EXCLUDED.min_monthly_income,
        min_credit_score = EXCLUDED.min_credit_score, max_credit_score = EXCLUDED.max_credit_score,
        min_age = EXCLUDED.min_age, max_age = EXCLUDED.max_age, employment_types = EXCLUDED.employment_types,
        max_loan_amount = EXCLUDED.max_loan_amount, other_criteria = EXCLUDED.other_criteria,
        source_url = EXCLUDED.source_url, criteria_source = EXCLUDED.criteria_source,
        is_active = TRUE, last_seen_at = now(), updated_at = now()
    RETURNING (xmax = 0) AS inserted
),
cached AS (
    INSERT INTO crawl_cache (url, content_hash, extracted)
    SELECT url, content_hash, extracted FROM jsonb_to_recordset($2::jsonb) AS c(url text, content_hash text, extracted jsonb)
    ON CONFLICT (url) DO UPDATE SET content_hash = EXCLUDED.content_hash, extracted = EXCLUDED.extracted, fetched_at = now()
    RETURNING 1
),
retired AS (
    UPDATE loan_products SET is_active = FALSE, updated_at = now()
    WHERE is_active AND last_seen_at < now() - interval '7 days'
    RETURNING 1
)
SELECT (SELECT count(*) FROM upserted WHERE inserted)     AS products_inserted,
       (SELECT count(*) FROM upserted WHERE NOT inserted) AS products_updated,
       (SELECT count(*) FROM cached)                      AS cache_rows_written,
       (SELECT count(*) FROM retired)                     AS products_retired;
