# Manzil Bharat

**Smart India Hackathon 2026 — Problem Statement #26204 (Travel & Tourism)**

An AI-assisted travel planner for India: search any destination, get an AI
travel card, explore it on a map with photos, and (once signed in) plan and
save trips. Includes an admin panel for users, bookings and payments.

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
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL connection |
| `JWT_SECRET` | Signs user/admin/reset tokens (min 32 chars) |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Admin panel login (min 12 chars) |
| `GEMINI_API_KEY` | Server-side AI travel guide |
| `GOOGLE_CLIENT_ID` | Google Sign-In (optional) |
| `SMTP_USER`, `SMTP_PASSWORD` | Password-reset emails (optional) |
| `PORT` | HTTP port (default 5000) |

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
├── ROADMAP.txt             # Zero-cost plan: phases, tasks, acceptance tests
└── .env.example            # Environment template (real .env is gitignored)
```

---

## API overview

All endpoints are prefixed `/api`.

Status column: `live` = implemented today, `todo` = Phase 1 of ROADMAP.txt.

| Method | Endpoint | Auth | Status | Purpose |
|--------|----------|------|--------|---------|
| POST | `/signup` | — | live | Create account |
| POST | `/login` | — | live | Email + password login |
| POST | `/google-login` | — | live | Google Sign-In |
| POST | `/forgot-password` | — | live | Emails a signed 30-minute reset link |
| POST | `/reset-password` | — | live | Redeem reset link |
| POST | `/ai-guide` | — | todo | Server-side Gemini proxy (rate-limited) |
| GET | `/me` | user | todo | Current user profile |
| GET, POST, DELETE | `/bookings` | user/admin | todo (admin part live) | Trip bookings |
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
