-- STAGE 1 - set-based hard filter inside PostgreSQL.
-- Every numeric/categorical rule is applied as a JOIN predicate, so the
-- users x products cross-product is never materialised in n8n: Postgres
-- returns only the pairs that satisfy all hard criteria, in one round-trip.
-- Pairs come back as compact arrays inside ONE row (not one n8n item per pair),
-- which keeps n8n's per-item overhead out of the hot path.
WITH batch_users AS (
    SELECT user_id, monthly_income, credit_score, age, employment_status
    FROM users
    WHERE batch_id = $1::uuid
),
products AS (
    SELECT product_id, lender, product_name, interest_rate_min, interest_rate_max,
           min_monthly_income, min_credit_score, max_credit_score, min_age, max_age,
           employment_types, max_loan_amount, other_criteria, criteria_source
    FROM loan_products
    WHERE is_active
),
candidates AS (
    SELECT u.user_id, p.product_id, u.monthly_income, u.credit_score, u.age, u.employment_status
    FROM batch_users u
    JOIN products p
      ON  u.monthly_income >= COALESCE(p.min_monthly_income, 0)
      AND u.credit_score BETWEEN COALESCE(p.min_credit_score, 300) AND COALESCE(p.max_credit_score, 900)
      AND u.age          BETWEEN COALESCE(p.min_age, 18)          AND COALESCE(p.max_age, 100)
      AND (p.employment_types IS NULL OR u.employment_status = ANY (p.employment_types))
)
SELECT
    $1::text                                        AS batch_id,
    (SELECT count(*) FROM batch_users)::int         AS users_in_batch,
    (SELECT count(*) FROM products)::int            AS products_active,
    (SELECT count(*) FROM candidates)::int          AS stage1_pairs,
    (SELECT COALESCE(json_agg(row_to_json(p)), '[]'::json) FROM products p) AS products,
    (SELECT COALESCE(json_agg(json_build_array(user_id, product_id, monthly_income,
                                               credit_score, age, employment_status)), '[]'::json)
       FROM candidates)                             AS pairs;
