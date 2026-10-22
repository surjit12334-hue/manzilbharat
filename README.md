# Manzil Bharat

An AI-assisted travel planner for India: search any destination, get an AI
travel card, explore it on a map with photos, and (once signed in) plan and
save trips. Includes an admin panel for users, bookings and payments.

**Live demo:** https://manzilbharat.onrender.com (free Render tier — the first
request after ~15 idle minutes takes up to a minute to wake the instance).

---

## Tech stack

| Layer     | Technology |
|-----------|------------|
| Frontend  | HTML5, CSS3, vanilla JavaScript (18 static pages) |
| Backend   | Python 3 + Flask (`backend/app.py`, single file) |
| Database  | PostgreSQL |
| AI        | Google Gemini (called **server-side** via `/api/ai-guide`) |
| Maps      | Leaflet + OpenStreetMap (place search goes through our own `/api/geocode` proxy, which sets the User-Agent the OSM policy asks for and caches the answers) |
| Auth      | Signed HMAC tokens, bcrypt password hashing, Google Sign-In |

---

## Quick start

```bash
# 1. Configure the environment (never commit .env)
cp .env.example .env
$EDITOR .env

# 2. Create the database
createdb manzil_bharat
psql -d manzil_bharat -f schema.sql
psql -d manzil_bharat -f seed.sql     # optional demo data

# 3. Run
./start.sh
```

Then open:

- Home: <http://localhost:5000/>
- Admin panel: <http://localhost:5000/admin.html> (credentials come from
  `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `.env`)

### Required environment variables

See `.env.example` for the full commented list.

| Variable | Purpose |
|----------|---------|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL connection (local dev) |
| `DATABASE_URL` | One connection string (Neon/Render). **Wins over `DB_*` when set** |
| `DB_SSLMODE`, `DB_CONNECT_TIMEOUT` | Optional connection overrides (defaults: provider's, 5 s) |
| `APP_ORIGIN` | Live origin(s), comma separated - CORS + reset links |
| `JWT_SECRET` | Signs user/admin/reset tokens (min 32 chars) |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Admin panel login (min 12 chars) |
| `GEMINI_API_KEY` | Server-side AI travel guide |
| `GOOGLE_CLIENT_ID` | Google Sign-In (optional) |
| `EMAIL_PROVIDER`, `RESEND_API_KEY` / `BREVO_API_KEY`, `EMAIL_FROM` | Password-reset mail over HTTPS (optional) |
| `SMTP_USER`, `SMTP_PASSWORD` | Password-reset mail over SMTP (local only - see below) |
| `GEOCODER_USER_AGENT` | Optional. The User-Agent sent to the public geocoders; by default the app builds one from `ADMIN_EMAIL` |
| `PORT` | HTTP port (default 5000) |

---

## Deploy (free: Render + Neon)

`render.yaml` is a **Render blueprint**: Render dashboard → *New → Blueprint* →
this repo, and Render reads that file, creates the service and prompts for
every secret it lists (`sync: false`) so nothing sensitive is ever committed.
Prefer clicking it yourself? *New → Web Service*, Language **Docker**, and use
`render.yaml` as the field-by-field checklist.

1. **Database — Neon (free, permanent).**
   <https://neon.com> → Free plan → new project → copy the connection string
   (it already ends in `sslmode=require`).
   *Not Render's free Postgres*: it is deleted 30 days after creation unless
   you upgrade.
2. **Service — Render.** The blueprint fills in `healthCheckPath: /api/health`,
   the Docker runtime and the env vars below.
3. **Apply the schema from your machine** (free services have no shell and no
   one-off jobs):

   ```bash
   export DATABASE_URL='postgresql://…'    # the Neon string from step 1
   psql "$DATABASE_URL" -f schema.sql
   psql "$DATABASE_URL" -f seed.sql        # optional demo data
   ```

   Already have a database from an earlier version? `schema.sql` is the
   fresh-database script, so run the migration instead — it only adds the
   tables that came later and is safe to run twice:

   ```bash
   psql "$DATABASE_URL" -v app_role=<the role the app connects as> \
        -f migrations/phase4.sql
   ```

4. **Environment variables** (set them in the Render dashboard, never upload
   `.env`):

   | Variable | Value |
   |----------|-------|
   | `DATABASE_URL` | the Neon connection string |
   | `JWT_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"` |
   | `ADMIN_EMAIL`, `ADMIN_PASSWORD` | admin panel login |
   | `GEMINI_API_KEY` | from <https://aistudio.google.com/apikey> |
   | `GOOGLE_CLIENT_ID` | optional, see step 5 |
   | `APP_ORIGIN` | `https://<your-service>.onrender.com` |
   | `EMAIL_PROVIDER`, `RESEND_API_KEY`, `EMAIL_FROM` | HTTPS reset mail, see step 6 |

5. **Google Sign-In** — add `https://<your-service>.onrender.com` to
   *Authorized JavaScript origins* in the Google Cloud console, then set
   `GOOGLE_CLIENT_ID`. Everything else works without it; only the Google
   button needs it.
6. **Password-reset mail.** Render's free plan blocks outbound ports
   25/465/587, so SMTP cannot work there. Either Resend (needs a verified
   sender domain) or Brevo (accepts a verified address you already own) sends
   over HTTPS on port 443: set `EMAIL_PROVIDER=resend|brevo`, the matching key
   and `EMAIL_FROM`. If you skip this, the app says so honestly -
   `Email service is not configured on the server` - instead of pretending a
   link was sent.
7. **Smoke test.**

   ```bash
   curl https://<your-service>.onrender.com/api/health   # {"db":"ok",...}
   ```

   then sign up, plan a trip, book it, cancel it, find it in My Trips.

### Free-plan caveats (know these before demoing)

- **Cold start:** a free service spins down after 15 idle minutes and takes
  ~60 s to come back. Open the site once before you start presenting.
- **512 MB / 0.1 CPU, 750 instance-hours a month**, ephemeral disk - local
  files are lost on every redeploy, which is why all real data lives in
  Postgres.
- **`/api/health` answers 503** when the database is unreachable, so a bad
  `DATABASE_URL` fails the deploy instead of 500-ing every page.

---

## Project structure

```
manzilbharat/
├── backend/
│   ├── app.py              # Flask app: API + static file serving
│   └── requirements.txt
├── tests/                  # pytest suite (conftest points at <DB_NAME>_test)
│   ├── conftest.py         # fixtures: throwaway DB, client, register/login/admin
│   └── test_setup.py
├── data/
│   ├── city_costs.json     # Hand-written per-day cost model (budget breakdown)
│   ├── transport.json      # Curated metro/rail/airport info for the 5 demo cities
│   ├── blog_posts.json     # Local blog stories (replaces rss2json)
│   └── <city>.json         # Curated hotels/things-to-do/eateries for the 5 demo cities
├── migrations/
│   ├── phase4.sql          # Adds the saved_trips + geocode_cache tables to an existing database
│   └── phase5.sql          # Adds the consents + page_views tables (Phase 5) to an existing database
├── scripts/
│   ├── backup.sh           # pg_dump to backups/ (nightly, via cron)
│   └── restore.sh          # put a backup back into the database
├── logs/                   # The rotating application log (gitignored, never committed)
├── backups/                # The pg_dump archives (gitignored, never committed)
├── requirements-dev.txt    # pytest - never installed by Render
├── pytest.ini
├── index.html              # Landing + destination search
├── map.html                # Leaflet map, AI travel card, photo gallery
├── trip.html               # Day-wise itinerary planner (AI day cards)
├── login.html              # Login / forgot password / Google sign-in
├── createaccount.html      # Signup
├── admin.html              # Admin panel (stats, users, bookings, payments)
├── mytrips.html            # User's saved trips/bookings (planned)
├── config.js               # Frontend config (API_BASE, client IDs)
├── schema.sql              # Fresh-database schema + migration comments
├── seed.sql                # Idempotent demo data (password Test@1234)
├── start.sh                # Local dev server
├── start-prod.sh           # Production gunicorn launcher (what Docker runs)
├── Dockerfile              # python:3.12-slim image (.env excluded)
├── render.yaml             # Render blueprint: service + env vars for free deploy
├── ROADMAP.txt             # Zero-cost plan: phases, tasks, acceptance tests
└── .env.example            # Environment template (real .env is gitignored)
```

---

## API overview

All endpoints are prefixed `/api`.

Status column: `live` = implemented today, `todo` = Phase 1 of ROADMAP.txt.

| Method | Endpoint | Auth | Status | Purpose |
|--------|----------|------|--------|---------|
| GET | `/health` | — | live | Readiness probe: 503 when the DB is unreachable |
| GET | `/places?city=…` | — | live | Curated hotels/things-to-do/eateries for the demo cities (404 + available list for others) |
| GET | `/transport?city=…` | — | live | Curated metro/rail/airport info for the demo cities (404 + available list for others) |
| GET | `/geocode?q=…` | — | live | Place search through the server (real User-Agent, cached, rate-limited) |
| POST | `/signup` | — | live | Create account |
| POST | `/login` | — | live | Email + password login |
| POST | `/google-login` | — | live | Google Sign-In |
| POST | `/forgot-password` | — | live | Emails a signed 30-minute reset link |
| POST | `/reset-password` | — | live | Redeem reset link |
| POST | `/ai-guide` | — | live | Server-side Gemini proxy (rate-limited); `kind='itinerary'` returns cached day cards + a budget estimate |
| GET | `/me` | user | live | Current user profile |
| POST | `/me/consent` | user | live | Record consent again after the policy text changes (versioned) |
| GET | `/me/consent` | user | live | The caller's consent history |
| GET | `/me/export` | user | live | DPDP data export: profile, bookings, saved trips, payments, consents (one JSON file) |
| DELETE | `/me` | user | live | DPDP erasure: deletes the account and everything attached to it |
| POST | `/bookings` | user | live | Create a booking (validated, owner row) |
| GET | `/bookings/mine` | user | live | The caller's own bookings |
| POST | `/bookings/<id>/cancel` | user | live | Cancel own booking (403/404/409 protected) |
| POST, GET | `/me/trips` | user | live | Save an itinerary / list the caller's saved itineraries |
| DELETE | `/me/trips/<id>` | user | live | Remove one saved itinerary (403 for somebody else's) |
| POST | `/admin/login` | — | live | Admin session |
| GET | `/stats`, `/recent` | admin | live | Dashboard |
| GET | `/analytics?days=14` | admin | live | The server's own page-view counter (no third-party analytics) |
| GET, DELETE | `/users`, `/users/<id>` | admin | live | User management |
| GET, PUT, DELETE | `/bookings/<id>`, `/payments/<id>` | admin | live | Record management |

Blog content is served locally from `data/blog_posts.json` and rendered by
`blog.html` / `blog-post.html` (no third-party RSS proxy).

---

## Trust & compliance (Phase 5)

**Consent (DPDP).** Creating an account needs an explicit, labelled checkbox -
the API refuses a signup without `consent: true`, and the consent row is
written in the *same transaction* as the user row, so an account can never
exist without the consent that created it. Every consent keeps the version of
the policy text that was accepted (`dpdp-v1` today) and where it came from
(`signup`, `google`, `renewal`), so we can always prove *which* wording
somebody agreed to. `privacypolicy.html` describes exactly this.

**Your data, your call.** *My Trips → Your data & privacy* has the two
self-service rights: **Download my data** (one JSON file with the profile,
bookings, saved itineraries, payments and consent history) and **Delete my
account** (the profile and every booking, saved trip, payment and consent row
go with it - `ON DELETE CASCADE`).

**Security headers.** Every response carries a Content-Security-Policy that
names the origins the pages really use (Font Awesome, Google Fonts, Leaflet,
Google Sign-In, the map tiles) and locks the rest down (`object-src 'none'`,
`frame-ancestors 'self'`, `form-action 'self'`), plus
`Strict-Transport-Security` (on HTTPS requests only) and a `Permissions-Policy`
that denies the camera, the microphone, the location and the payment API.

**Logging.** Sign-ups, sign-ins (successful and failed), password resets,
consent records, data exports, account deletions, admin actions and crashes
are written to `logs/manzil.log` (rotated at 5 MB, 3 old files) and to stdout,
where the host collects them. Passwords, password hashes, session tokens and
reset links are never logged.

**Backups.** `scripts/backup.sh` takes a `pg_dump` into `backups/`, keeps the
last 14 of them, and never lets a broken dump replace a good one;
`scripts/restore.sh` puts a backup back (`pg_restore --clean --if-exists`, so
a restore works over a database that already has the tables). Run it nightly
with cron, or let `.github/workflows/backup.yml` do it - that workflow runs on
a schedule, skips itself when the `DATABASE_URL` secret is not set, and keeps
the dump as an artifact for 14 days.

**Analytics.** The server counts the pages it serves (`page_views`, one row
per path and day) and the admin panel shows it as a bar chart in the Traffic
tab. No third-party script, no cookie, no visitor identifier, and the query
string is never stored.

---

## Testing

```bash
# 1. One-off: create the throwaway test database. This needs a role with
#    CREATEDB - the app's own user has neither CREATEDB nor CREATE:
sudo -u postgres createdb -O manzil_user manzil_bharat_test

# 2. Dev dependencies (production installs backend/requirements.txt only)
venv/bin/pip install -r requirements-dev.txt
venv/bin/python -m pytest
```

`tests/conftest.py` points the app at `<DB_NAME>_test` (or at
`TEST_DATABASE_URL` when set, which is what CI uses), applies `schema.sql`
once per session, and truncates every table before and after each test, so the
development database is never written to. It refuses to run if the resolved
database name does not contain "test", precisely because every test truncates.

Smoke test after a deploy:

```bash
curl -i https://manzilbharat.onrender.com/api/stats   # expect 401
curl https://manzilbharat.onrender.com/api/health     # {"db":"ok","status":"ok"}
```

---

## Lint

One config file per language: `ruff.toml` (Python) and `.prettierrc.json`
(frontend). Prettier covers every `*.js`, `*.css`, `*.html` and `*.json` in the
repo - the CSS lives inside each page's `<style>` block, so the HTML pass is
the CSS pass, and Prettier formats the embedded `<script>` bodies too.

```bash
# Python
venv/bin/ruff check .            # CI runs the same command
venv/bin/ruff check . --fix      # apply the safe fixes

# Frontend (needs Node; package.json pins Prettier exactly)
npm ci                           # one-off: install from package-lock.json
npm run format:check             # CI: fail if anything is unformatted
npm run format                   # rewrite the files
```

Both run in CI as the `lint (ruff + prettier)` job, in parallel with the test
suite. `ruff check` only - nothing runs `ruff format` over `backend/app.py`,
because its layout is intentional and a reflow would bury a 1700-line file in
whitespace churn.

---

## Roadmap & status

See [`ROADMAP.txt`](ROADMAP.txt) for the full phase-by-phase plan, including
what is deliberately out of scope (paid travel APIs, live payments, custom
domain) and the demo script acceptance criteria.

## License

All rights reserved © 2026 Manzil Bharat.
