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
| Frontend  | HTML5, CSS3, vanilla JavaScript (17 static pages) |
| Backend   | Python 3 + Flask (`backend/app.py`, single file) |
| Database  | PostgreSQL |
| AI        | Google Gemini (called **server-side** via `/api/ai-guide`) |
| Maps      | Leaflet + OpenStreetMap (Nominatim / Photon for search) |
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
├── index.html              # Landing + destination search
├── map.html                # Leaflet map, AI travel card, photo gallery
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
| POST | `/signup` | — | live | Create account |
| POST | `/login` | — | live | Email + password login |
| POST | `/google-login` | — | live | Google Sign-In |
| POST | `/forgot-password` | — | live | Emails a signed 30-minute reset link |
| POST | `/reset-password` | — | live | Redeem reset link |
| POST | `/ai-guide` | — | live | Server-side Gemini proxy (rate-limited) |
| GET | `/me` | user | live | Current user profile |
| POST | `/bookings` | user | live | Create a booking (validated, owner row) |
| GET | `/bookings/mine` | user | live | The caller's own bookings |
| POST | `/bookings/<id>/cancel` | user | live | Cancel own booking (403/404/409 protected) |
| POST | `/admin/login` | — | live | Admin session |
| GET | `/stats`, `/recent` | admin | live | Dashboard |
| GET, DELETE | `/users`, `/users/<id>` | admin | live | User management |
| GET, PUT, DELETE | `/bookings/<id>`, `/payments/<id>` | admin | live | Record management |

---

## Testing

_No test suite yet — planned in Phase 3 of `ROADMAP.txt` (`pytest` + GitHub
Actions). Until then, smoke-test with:_

```bash
./start.sh
curl -i http://localhost:5000/api/stats   # expect 401 Unauthorized
```

---

## Roadmap & status

See [`ROADMAP.txt`](ROADMAP.txt) for the full phase-by-phase plan, including
what is deliberately out of scope (paid travel APIs, live payments, custom
domain) and the demo script acceptance criteria.

## License

All rights reserved © 2026 Manzil Bharat.
