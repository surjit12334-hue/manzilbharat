-- =============================================================================
-- Phase 5 migration - TRUST & COMPLIANCE
--
-- Run this ONCE against a database that already exists:
--
--   psql "$DATABASE_URL" -v app_role=<the role the app connects as> \
--        -f migrations/phase5.sql
--
-- It is safe to run twice: every statement is IF NOT EXISTS.
-- A FRESH database gets these tables from schema.sql instead - do not run
-- this file on one, it is only for the databases created before Phase 5.
-- =============================================================================

-- DPDP consent records (Phase 5 step 1). Each time consent is given it is
-- recorded with the version of the text that was accepted, so we can always
-- prove WHICH wording the user agreed to.
CREATE TABLE IF NOT EXISTS consents (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text_version VARCHAR(50) NOT NULL,
    source       VARCHAR(20) NOT NULL DEFAULT 'signup',
    created_at   TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS consents_user_id_idx ON consents (user_id);

-- source says WHERE the consent came from: 'signup', 'google' or 'renewal'.
-- A separate statement, because CREATE TABLE IF NOT EXISTS does NOT add a
-- column to a table that already exists.
ALTER TABLE consents ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'signup';

-- First-party page-view counter (Phase 5 step 5). One row per (path, day):
-- no third-party script, no cookies, no visitor identifier - just how often
-- each page was requested.
CREATE TABLE IF NOT EXISTS page_views (
    path  VARCHAR(255) NOT NULL,
    day   DATE NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (path, day)
);
CREATE INDEX IF NOT EXISTS page_views_day_idx ON page_views (day);

-- Access for the role the app connects as.
\if :{?app_role}
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE consents, page_views TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE consents_id_seq TO :"app_role";
\else
\echo 'No app_role given - skipping the GRANT statements (run with -v app_role=<role> to include them).'
\endif
