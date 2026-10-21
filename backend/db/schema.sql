-- =============================================================================
-- Loan Eligibility Engine - PostgreSQL schema
-- Idempotent: safe to run on every deploy (used by the `migrate` Lambda and by
-- the local docker-compose Postgres init).
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- One row per CSV upload. Drives the status shown in the UI and records the
-- matching funnel metrics (the "Optimization Treasure Hunt" numbers).
CREATE TABLE IF NOT EXISTS upload_batches (
    batch_id        UUID PRIMARY KEY,
    file_name       TEXT        NOT NULL,
    s3_key          TEXT,
    status          TEXT        NOT NULL DEFAULT 'awaiting_upload',
        -- awaiting_upload -> ingesting -> ingested -> matching -> matched -> notified
        -- (or failed / notify_webhook_failed / notify_failed)
    rows_total      INTEGER,
    rows_inserted   INTEGER,
    rows_rejected   INTEGER,
    rejected_sample JSONB,
    match_stats     JSONB,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    user_id           TEXT PRIMARY KEY,
    name              TEXT,
    email             TEXT          NOT NULL,
    monthly_income    NUMERIC(12,2) NOT NULL CHECK (monthly_income >= 0),
    credit_score      SMALLINT      NOT NULL CHECK (credit_score BETWEEN 300 AND 900),
    employment_status TEXT          NOT NULL,   -- canonical: salaried | self_employed | business | unemployed | other
    age               SMALLINT      NOT NULL CHECK (age BETWEEN 18 AND 100),
    batch_id          UUID REFERENCES upload_batches(batch_id),
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ   NOT NULL DEFAULT now()
);
-- Workflow B fetches "the newly added users" by batch.
CREATE INDEX IF NOT EXISTS users_batch_idx ON users (batch_id);

-- Populated by n8n Workflow A (crawler). NULL criteria = "no constraint published".
CREATE TABLE IF NOT EXISTS loan_products (
    product_id          SERIAL PRIMARY KEY,
    product_key         TEXT UNIQUE NOT NULL,      -- stable slug, e.g. "hdfc-bank-personal-loan"
    lender              TEXT        NOT NULL,
    product_name        TEXT        NOT NULL,
    interest_rate_min   NUMERIC(5,2),
    interest_rate_max   NUMERIC(5,2),
    processing_fee      TEXT,
    min_monthly_income  NUMERIC(12,2),
    min_credit_score    SMALLINT,
    max_credit_score    SMALLINT,
    min_age             SMALLINT,
    max_age             SMALLINT,
    employment_types    TEXT[],                    -- NULL = any employment type
    max_loan_amount     NUMERIC(14,2),
    other_criteria      TEXT,                      -- qualitative text the LLM reasons over
    source_url          TEXT,
    criteria_source     TEXT,                      -- llm | heuristic | default | seed
    is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS loan_products_active_idx
    ON loan_products (min_credit_score, min_monthly_income) WHERE is_active;

CREATE TABLE IF NOT EXISTS matches (
    match_id        BIGSERIAL PRIMARY KEY,
    user_id         TEXT    NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    product_id      INTEGER NOT NULL REFERENCES loan_products(product_id) ON DELETE CASCADE,
    batch_id        UUID,
    score           NUMERIC(5,2),
    decision_source TEXT,          -- rules | llm | llm_cache | rules_fallback
    reason          TEXT,
    notified_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, product_id)
);
CREATE INDEX IF NOT EXISTS matches_batch_idx ON matches (batch_id);
-- Workflow C only ever scans un-notified rows.
CREATE INDEX IF NOT EXISTS matches_pending_notify_idx ON matches (user_id) WHERE notified_at IS NULL;

-- LLM verdicts are cached per (product, coarse user profile bucket), so a
-- borderline question is asked once, not once per user.
CREATE TABLE IF NOT EXISTS llm_decision_cache (
    cache_key   TEXT PRIMARY KEY,
    product_id  INTEGER,
    eligible    BOOLEAN NOT NULL,
    reason      TEXT,
    model       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Crawler change detection: if a page's cleaned-text hash is unchanged we
-- reuse the previous extraction and skip the LLM entirely.
CREATE TABLE IF NOT EXISTS crawl_cache (
    url          TEXT PRIMARY KEY,
    content_hash TEXT  NOT NULL,
    extracted    JSONB,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id BIGSERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL,
    sent_to         TEXT NOT NULL,
    product_ids     INTEGER[] NOT NULL,
    batch_id        UUID,
    ses_message_id  TEXT,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every status change of a batch is appended to status_history by a trigger, so
-- the dashboard can show when each pipeline step happened regardless of which
-- component (Lambda or n8n) made the change.
ALTER TABLE upload_batches ADD COLUMN IF NOT EXISTS status_history JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE OR REPLACE FUNCTION upload_batches_track_status() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' OR NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_history := COALESCE(NEW.status_history, '[]'::jsonb)
            || jsonb_build_array(jsonb_build_object('status', NEW.status, 'at', clock_timestamp()));
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS upload_batches_status_history ON upload_batches;

CREATE TRIGGER upload_batches_status_history
    BEFORE INSERT OR UPDATE OF status ON upload_batches
    FOR EACH ROW EXECUTE FUNCTION upload_batches_track_status();
