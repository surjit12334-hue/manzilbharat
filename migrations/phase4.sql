-- ---------------------------------------------------------------------------
-- Manzil Bharat - Phase 4 migration (steps 6 and 7)
--
-- schema.sql is the FRESH-database script. This file adds the two tables
-- that were introduced after the live database was created, and it is safe
-- to run more than once (every statement is IF NOT EXISTS).
--
-- Run it against the live database AS THE TABLE OWNER (the Neon owner
-- role), because the role the app connects as usually may not CREATE:
--
--   psql "$DATABASE_URL" -f migrations/phase4.sql
--
-- The app role also needs read/write access to the two new tables. Pass its
-- name and the GRANT is included:
--
--   psql "$DATABASE_URL" -v app_role=<role> -f migrations/phase4.sql
--
-- On the local development database the role is manzil_user.
-- ---------------------------------------------------------------------------

-- Step 6: itineraries a visitor saved from trip.html, shown under My Trips.
-- The row belongs to one user and goes with the account (ON DELETE CASCADE).
CREATE TABLE IF NOT EXISTS saved_trips (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      VARCHAR(255) NOT NULL,
    payload    TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS saved_trips_user_id_idx ON saved_trips (user_id);
CREATE INDEX IF NOT EXISTS saved_trips_created_at_idx ON saved_trips (created_at);

-- Step 7: cached answers from the public geocoders (Nominatim / Photon).
-- The site searches for places through the server so it can send a proper
-- User-Agent, and every answer is kept here so the same search does not hit
-- the shared community service twice.
CREATE TABLE IF NOT EXISTS geocode_cache (
    query_key  TEXT PRIMARY KEY,
    provider   TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS geocode_cache_created_idx ON geocode_cache (created_at);

-- DPDP consent records (Phase 5 step 1).
CREATE TABLE IF NOT EXISTS consents (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text_version VARCHAR(50) NOT NULL,
    created_at   TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS consents_user_id_idx ON consents (user_id);

-- Access for the role the app connects as.
\if :{?app_role}
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE saved_trips, geocode_cache, consents TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE saved_trips_id_seq, consents_id_seq TO :"app_role";
\else
\echo 'No app_role given - skipping the GRANT statements (run with -v app_role=<role> to include them).'
\endif
