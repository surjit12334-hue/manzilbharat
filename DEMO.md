# Demo script — Manzil Bharat

Phase 6, step 1. **Two minutes**: 30 s problem → 60 s live flow → 30 s honest
boundary. The whole thing still has to land if you are cut to 90 seconds — see
*Short version* at the bottom.

Read the **Say** lines out loud, do the **Do** lines, keep an eye on the clock.
The clock only works if you talk while you click: describe *why it matters*,
never *what you are typing*. Three clean runs is the bar; fill the log at the
end of this file.

---

## 30 minutes before (once)

| # | Check | How |
|---|-------|-----|
| 1 | Server answers | `./start.sh`, then `curl -s localhost:5000/api/health` |
| 2 | AI key present | `GEMINI_API_KEY=…` in `.env` (without it the itinerary beat answers 503) |
| 3 | Razorpay **test** keys | `RAZORPAY_KEY_ID=rzp_test_…`, `RAZORPAY_KEY_SECRET=…` |
| 4 | Admin login ready | `ADMIN_EMAIL` / `ADMIN_PASSWORD` from `.env` |
| 5 | Admin panel not empty (optional) | `psql "$DATABASE_URL" -f seed.sql` — idempotent demo rows |
| 6 | **Pre-warm the itinerary** | Run the exact plan once: **Jaipur, 3 days, mid-range, `forts, street food`**. It is cached for 7 days, so the AI beat becomes instant and survives a dead network. |
| 7 | Browser | One clean window, signed out, on `index.html`. Second tab `admin.html`. Third tab `mytrips.html` (backup). |
| 8 | Fresh email each run | `riya1@…`, `riya2@…`, `riya3@…` — signup refuses a duplicate |
| 9 | Use one of the curated five | Delhi, Goa, Jaipur, Manali, Varanasi. Anything else honestly shows the fallback page. |

Signup values that pass validation first time (11 fields — rehearse this beat
until it takes 15 seconds):
**Name** `Riya Sharma` · **Email** `riya1@example.com` · **Contact**
`9876543210` · **Gender** Female · **DOB** `1998-05-14` · **Marital** Single ·
**Address** `12 Civil Lines, Jaipur` · **Pincode** `302001` · **Password**
`Demo@12345`

---

## 0:00–0:30 · the problem

Say (≈70 words, no slides):

> Planning a trip in India means twelve tabs. One site for hotels, one for the
> train, a group chat for the budget, and a blog post from 2019 for what to
> actually do. Nothing knows what you like and nothing adds up the money.
> Manzil Bharat does the whole arc in one place: search a place, get a day-wise
> itinerary, see what it costs, book it, pay for it — and an admin sees every
> booking land. Everything I am about to show you is running right now.

---

## 0:30–1:30 · the live flow

| Clock | Do | Say (one sentence while you click) |
|-------|----|------------------------------------|
| 0:30 | **Sign up**: `createaccount.html`, fill the nine values above, **tick the consent box**, *Create Account* → lands signed in on `index.html` | "One form — and the consent tick is enforced by the server, which also stores which version of the policy was accepted." |
| 0:42 | **Search**: type `Jaipur`, click the first suggestion → the map opens → *Plan Trip* | "Search goes through our own server, with a real User-Agent, a cache and a rate limit — not straight at a public API." |
| 0:50 | **Itinerary**: 3 days, Mid-range, `forts, street food` → *Build itinerary* | "Gemini writes the days; the server validates every slot, drops a malformed answer and caches what it accepts." |
| 1:00 | **Budget**: point at the total and the four bars | "Day costs come from the model; the stay/food/transport/activities split comes from our own hand-written city table." |
| 1:06 | **Save**: *Save this itinerary* → open `mytrips.html` → Saved Itineraries | "It is saved against my account, not in the browser." |
| 1:12 | **Book**: *New trip* → cab, Delhi → Jaipur, one month out, amount `2400` → *Save trip* | "A real row in Postgres, owned by me — which is why I cannot see anyone else's." |
| 1:18 | **Pay**: *Pay (test)* → Razorpay test card `4111 1111 1111 1111`, any future expiry/CVV → card comes back marked **Test payment** | "Razorpay's checkout takes the card — our server never sees it — and the payment is only recorded after the server checks Razorpay's signature with the secret key." |
| 1:26 | **Admin**: second tab `admin.html`, log in → dashboard → **Payments** (Mode: test) → **Traffic** | "The same booking, its test payment, and first-party page views — no Google Analytics anywhere." |

---

## 1:30–2:00 · what is real and what is a stub

Say (do not skip this — judges trust the honest boundary more than a perfect
story):

> **Real, running now:** signup and login with bcrypt and signed tokens; the
> itinerary engine; the budget model; bookings in Postgres, scoped to their
> owner; the admin panel; consent, one-click data export and account deletion;
> security headers; rotating logs; tested backup and restore; the page-view
> counter; and the payment integration, signature check included.
> **Deliberately stubbed:** hotels, eateries and transport are a hand-curated
> set for five cities — no paid inventory APIs. Payments run in Razorpay's
> **test** mode: no real money, and every payment is labelled test. Email needs
> a provider key to actually send. There is no live airline or hotel stock.
> Nobody is pretending otherwise anywhere in the UI.

### The table, if they ask for it in writing

| Real | Stub / by design |
|------|------------------|
| Signup/login: bcrypt + signed JWT, admin separate | Google Sign-In: works once `GOOGLE_CLIENT_ID` is set |
| AI itinerary: Gemini, validated JSON, 7-day cache | Gemini is the only paid dependency; the key stays server-side |
| Budget: real arithmetic over a hand-written city cost model | City list: 5 curated cities + an honest fallback rate |
| Bookings: Postgres, owner-scoped, cancellable | No real airline/hotel inventory |
| Payments: order + HMAC signature verification, test mode enforced | No live Razorpay (KYC + fees), no refunds because no money moves |
| Admin: live queries, payments, traffic | Email delivery only with a provider configured |
| DPDP: versioned consent, export, delete; CSP, HSTS; logs; backups | Traffic is first-party and stores no visitor identifier |

---

## If it breaks

1. **AI fails / no network.** The demo plan is pre-warmed in `itinerary_cache`
   (7 days). If even that fails: "The planner is a live model — here is the one
   I built in rehearsal," and open `mytrips.html` → Saved Itineraries.
2. **Razorpay checkout will not load.** Say "payments are in test mode; here is
   the one I took earlier," and show `admin.html` → Payments (Mode: test).
   If the checkout says the origin is not allowed, add `localhost` in the
   Razorpay dashboard → Account & Settings → Website/Domains.
3. **Signup says the email exists.** Use the next `riya2@…` address — never
   stop to debug on stage.
4. **A 500.** `tail -20 logs/manzil.log` (the crash is logged with its request);
   `./start.sh` again. Keep the tab order so the audience sees a reload, not a
   code editor.

---

## Questions they will ask

- **Is the AI real?** Yes — Gemini server-side. The key is never in the browser,
  the answer is parsed and validated, and a bad answer is rejected, not shown.
- **Why test mode?** Live needs KYC and fees. Test mode proves the whole
  integration — order, checkout, signature verification — with no risk, and
  every payment says `test` in the UI and the admin panel.
- **Where does the hotel data come from?** Hand-curated JSON for five cities,
  with photographs and honest "demo data" wording. Paid inventory APIs are out
  of scope, not hidden.
- **Privacy/compliance?** DPDP consent with the accepted text version, export
  and deletion in the app, no third-party analytics, CSP/HSTS on every
  response, rotating logs, and a nightly backup workflow that was tested with a
  real restore.
- **How is it tested?** pytest against a real Postgres — 260+ cases, including
  the payment signature vector and cross-user access. `ruff` and Prettier gate
  the style.
- **One server?** One Flask app plus Postgres; the AI is the only slow call and
  it is cached. That is the right size for a demo and the README says so.

---

## Short version (90 seconds)

Start already signed in (`mytrips.html`), drop the map beat, keep:
itinerary → budget → saved trip → book → pay (test) → admin Payments.
The problem sentence and the real-vs-stub sentence are the two things that must
survive any cut.

---

## Rehearsal log — three clean runs

A run counts only if it is under 2:10, with no dead air longer than two
seconds, and no debugging out loud.

| # | Date | Length | What broke | Fix before the next run |
|---|------|--------|------------|--------------------------|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |

When the third row is filled, Phase 6 step 1 is done.
