import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import smtplib
import sys
import time
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from functools import wraps
from logging.handlers import RotatingFileHandler
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import bcrypt
import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv
from flask import Flask, abort, g, jsonify, request, send_from_directory
from flask.json.provider import DefaultJSONProvider
from flask_cors import CORS
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix


def base64url_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def base64url_decode(s):
    s += '=' * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)

load_dotenv()


def _required(name, min_len=0):
    value = os.getenv(name)
    if not value or not value.strip():
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and set {name}, "
            "or export it before starting the server."
        )
    value = value.strip()
    if len(value) < min_len:
        raise RuntimeError(
            f"{name} must be at least {min_len} characters (got {len(value)})."
        )
    return value


GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
# Optional: without it /api/ai-guide answers 503 and the rest of the site
# keeps working. Never sent to the browser - see ai_guide() below.
GEMINI_API_KEY = (os.getenv('GEMINI_API_KEY') or '').strip()
GEMINI_MODEL = (os.getenv('GEMINI_MODEL') or '').strip() or 'gemini-3.6-flash'
JWT_SECRET = _required('JWT_SECRET', min_len=32)
ADMIN_EMAIL = _required('ADMIN_EMAIL')
ADMIN_PASSWORD = _required('ADMIN_PASSWORD', min_len=12)
ADMIN_PASSWORD_HASH = bcrypt.hashpw(
    ADMIN_PASSWORD.encode('utf-8'),
    bcrypt.gensalt()
).decode('utf-8')

# Compared against when the admin email does not match, so the login endpoint
# always performs one bcrypt check and response timing does not reveal whether
# an admin account exists for a given address.
_DUMMY_ADMIN_HASH = bcrypt.hashpw(b'not-the-admin-password', bcrypt.gensalt())

SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')

# Password-reset mail leaves the server in one of two ways:
#   smtp          direct SMTP on port 587 - fine locally, but Render's FREE
#                 plan blocks outbound 25/465/587, so it cannot work there
#   resend/brevo  HTTPS JSON APIs on port 443 - allowed by every free host
# EMAIL_PROVIDER picks one. When it is empty we infer a provider from whichever
# credentials are present, so an existing .env behaves exactly as before.
EMAIL_PROVIDER = (os.getenv('EMAIL_PROVIDER') or '').strip().lower()
RESEND_API_KEY = (os.getenv('RESEND_API_KEY') or '').strip()
BREVO_API_KEY = (os.getenv('BREVO_API_KEY') or '').strip()
EMAIL_FROM = (os.getenv('EMAIL_FROM') or '').strip()

if not EMAIL_PROVIDER:
    if RESEND_API_KEY:
        EMAIL_PROVIDER = 'resend'
    elif BREVO_API_KEY:
        EMAIL_PROVIDER = 'brevo'
    elif SMTP_USER and SMTP_PASSWORD:
        EMAIL_PROVIDER = 'smtp'

if EMAIL_PROVIDER in ('resend', 'brevo') and not EMAIL_FROM:
    # Both APIs reject an empty From. Fail loudly at boot rather than on a
    # judge's forgot-password click.
    print(f'NOTE: EMAIL_PROVIDER={EMAIL_PROVIDER} but EMAIL_FROM is empty - '
          'password-reset emails will fail until you set it')
elif EMAIL_PROVIDER not in ('smtp', 'resend', 'brevo'):
    print('NOTE: no email provider configured (set EMAIL_PROVIDER and its '
          'credentials) - password-reset emails are disabled')

ALLOWED_ORIGINS = [
    "http://localhost:5000",
    "http://localhost:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5000",
    "https://surjit12334-hue.github.io"
]
# The live origin(s), comma separated, set in the host dashboard:
#   APP_ORIGIN=https://manzil-bharat.onrender.com
# Without this the deployed site falls outside the CORS allow-list and the
# forgot-password handler refuses the page's own origin, silently rebuilding
# reset links against whatever host the proxy forwarded.
ALLOWED_ORIGINS += [o.strip() for o in (os.getenv('APP_ORIGIN') or '').split(',')
                    if o.strip()]

RESET_TOKEN_VALID_SECONDS = 1800

# The version of the consent text the visitor agreed to (DPDP). Bump it when
# the wording in privacypolicy.html changes: the old rows stay in the consents
# table, so we can always prove WHICH text someone accepted, and a signup that
# quotes an outdated version is refused instead of being recorded against the
# wrong text.
CONSENT_TEXT_VERSION = 'dpdp-v1'
CONSENT_TEXT_VERSIONS = {CONSENT_TEXT_VERSION}

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)

app = Flask(__name__, static_folder=PROJECT_ROOT, static_url_path='')

# ---------------------------------------------------------------------------
# Logging (Phase 5 step 4)
# ---------------------------------------------------------------------------
# Two sinks, one set of lines:
#   stdout   the host collects it (Render's log dashboard, Docker, systemd)
#   a file   logs/manzil.log on disk, rotated at 5 MB with 3 old files
#
# WHAT IS NEVER LOGGED: passwords, password hashes, session tokens, reset
# tokens, API keys. The audit lines below carry the user id and the email
# address of the account involved - that is what an access log is for - and
# nothing else. The privacy policy says so in section 1.E.
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'manzil.log')


def _setup_logging():
    """Send the app's logs to stdout and to a rotating file."""
    root = logging.getLogger()
    if getattr(root, '_manzil_configured', False):
        return
    root.setLevel(logging.INFO)
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        # A read-only disk is not a reason to refuse to serve traffic: the
        # stdout stream is still there.
        print(f'NOTE: cannot write {LOG_FILE} ({exc}) - logging to stdout only')

    root._manzil_configured = True


_setup_logging()

# Trust one proxy hop so request.remote_addr / request.scheme reflect the real
# visitor when running behind a reverse proxy. NOTE: this is NOT enough to key
# the rate limiter on Render - see _client_ip() for why.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})

# gzip/br response bodies. Optional on purpose: the module is in
# requirements.txt (production) but the app must still boot without it. Only
# text MIME types are compressed, so JPEG/PNG bytes are never re-encoded.
try:
    from flask_compress import Compress
    # Streaming (file-backed) responses default to br/deflate only; add gzip so
    # a client that offers nothing else still gets a compressed body.
    app.config['COMPRESS_ALGORITHM_STREAMING'] = [
        'zstd', 'br', 'gzip', 'deflate']
    # Re-check If-None-Match AFTER compression for the '/' homepage too (only
    # the 'static' endpoint is listed by default), otherwise a browser holding
    # the compressed body gets a 200 + full re-download instead of a 304.
    app.config['COMPRESS_STREAMING_ENDPOINT_CONDITIONAL'] = ['static', 'index']
    Compress(app)
except ImportError:  # pragma: no cover - dev machines without the extra
    print('NOTE: flask-compress not installed - responses are sent uncompressed')


class ISODatetimeJSONProvider(DefaultJSONProvider):
    """JSON-encode date/datetime as ISO-8601.

    psycopg2 returns datetime.date for the bookings.travel_date DATE column
    (and datetime for timestamps); without this every endpoint selecting
    them fails to jsonify. ISO strings also parse cleanly with new Date()
    in admin.html and mytrips.html.
    """

    @staticmethod
    def default(o):
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return DefaultJSONProvider.default(o)


app.json = ISODatetimeJSONProvider(app)

STATIC_ALLOWED_EXTENSIONS = {
    '.html', '.js', '.css', '.json', '.png', '.jpg', '.jpeg', '.gif', '.svg',
    '.webp', '.ico', '.pdf', '.woff', '.woff2', '.ttf', '.eot'
}

# Cache lifetimes per asset kind, applied in set_security_headers(). See the
# comment there for why HTML is deliberately absent (revalidated instead).
_CACHE_IMMUTABLE = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico',
                    '.woff', '.woff2', '.ttf', '.eot', '.pdf'}
_CACHE_ONE_WEEK = {'.css', '.js', '.json'}


@app.before_request
def block_private_files():
    """The static root is the whole project, so only let web assets through.

    Without this, /.env, /schema.sql, /seed.sql and /backend/app.py are all
    downloadable over HTTP. Anything without a known asset extension (or with
    a dot-prefixed path segment) is refused.
    """
    if request.path.startswith('/api/'):
        return
    filename = request.path.lstrip('/')
    if not filename:
        return
    if any(segment.startswith('.') for segment in filename.split('/')):
        abort(404)
    if os.path.splitext(filename)[1].lower() not in STATIC_ALLOWED_EXTENSIONS:
        abort(404)
    return


@app.errorhandler(HTTPException)
def handle_http_exception(error):
    if request.path.startswith('/api/'):
        return jsonify({'error': error.description}), error.code
    return error


@app.errorhandler(500)
def handle_internal_error(error):
    """Log a crash with the request that caused it.

    The traceback belongs in the log, not in the response: a stack trace in
    the body tells an outsider about the internals of the server. The visitor
    gets the same plain message every time, and the operator gets the
    details in logs/manzil.log (and on stdout).
    """
    user_id = None
    try:
        user_id = g.get('user', {}).get('user_id')
    except Exception:
        user_id = None
    app.logger.exception('500 on %s %s (user %s)', request.method, request.path,
                        user_id)
    if request.path.startswith('/api/'):
        return jsonify({'error': 'An internal error occurred. Please try again later.'}), 500
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<title>Something went wrong</title></head><body>'
            '<h1>Something went wrong</h1>'
            '<p>The server hit an unexpected error. Please try again in a '
            'moment.</p></body></html>'), 500


@app.route('/')
def index():
    return send_from_directory(PROJECT_ROOT, 'index.html')


# ---------------------------------------------------------------------------
# First-party analytics (Phase 5 step 5)
# ---------------------------------------------------------------------------
# One row per (path, day) in page_views. Deliberately simple and deliberately
# private: no third-party script, no cookie, no visitor identifier, and the
# query string is NOT stored (it can carry a destination or an email).
_analytics_broken = False


def _is_page_view(path):
    """True for a page request, false for an asset or an API call."""
    if path.startswith('/api/'):
        return False
    ext = os.path.splitext(path)[1].lower()
    return ext in ('', '.html')


@app.after_request
def count_page_view(response):
    """Count the page view (if it is one) in the page_views table.

    A failure here must never break the page the visitor asked for, so every
    error is swallowed and the counter switches itself off after the first
    one - a missing table would otherwise cost every request an error log
    line and a rolled-back transaction.
    """
    global _analytics_broken
    if _analytics_broken:
        return response
    if request.method not in ('GET', 'HEAD'):
        return response
    if response.status_code >= 400:
        return response
    if not _is_page_view(request.path):
        return response

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO page_views (path, day, count) VALUES (%s, CURRENT_DATE, 1) '
            'ON CONFLICT (path, day) DO UPDATE SET count = page_views.count + 1',
            (request.path[:255],),
        )
        conn.commit()
        cur.close()
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                # The connection is already broken - there is nothing to roll
                # back, and the failure below is the one worth reading.
                app.logger.info('page-view rollback failed: the connection was already gone')
        _analytics_broken = True
        app.logger.exception(
            'page-view counting failed - run migrations/phase5.sql '
            '(the counter is now off until the app restarts)')
    finally:
        if conn:
            conn.close()
    return response


# ---------------------------------------------------------------------------
# Security headers (Phase 5 step 3)
# ---------------------------------------------------------------------------
# The Content-Security-Policy lists every origin the pages actually use:
#   cdnjs.cloudflare.com  Font Awesome (the icons)
#   fonts.googleapis.com  the web font
#   fonts.gstatic.com     the font files themselves
#   unpkg.com             Leaflet (the map)
#   accounts.google.com   Google Sign-In
#   tile.openstreetmap.org / server.arcgisonline.com   the map tiles
#   www.google.com        the Google Maps embed on the contact page
# 'unsafe-inline' is needed for scripts and styles because every page carries
# its own <script> and <style> block; a nonce-per-page version would mean
# rewriting all 18 pages. Everything else is locked down: no plugins, no
# framing by strangers, forms can only post to us.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://accounts.google.com "
    "https://unpkg.com https://cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com "
    "https://cdnjs.cloudflare.com https://unpkg.com; "
    "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
    "img-src 'self' data: blob: https://tile.openstreetmap.org "
    "https://server.arcgisonline.com https://*.googleapis.com "
    "https://maps.gstatic.com; "
    "connect-src 'self' https://accounts.google.com; "
    "frame-src https://www.google.com https://accounts.google.com; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'"
)


@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = _CSP
    # The app never asks for the camera, the microphone or the location, so
    # no page may use them.
    response.headers['Permissions-Policy'] = (
        'geolocation=(), microphone=(), camera=(), payment=(), usb=()')
    # Strict-Transport-Security is only meaningful over HTTPS - sending it on
    # a plain http:// page would pin the visitor to a scheme the local
    # development server does not speak. ProxyFix makes request.is_secure
    # true behind the reverse proxy, so the live site gets the header.
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = (
            'max-age=31536000; includeSubDomains')
    # Auth responses carry tokens and password hashes; keep them out of any
    # shared/browser cache.
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
        return response

    # Static assets are served straight off disk by Flask's static handler, so
    # every response reaches this hook. Cache policy:
    #   images/fonts/pdf -> a year, immutable. These are only ever REPLACED BY
    #     A NEW FILENAME (homebackground.png -> homebackground.jpg), so a URL's
    #     bytes never change and there is nothing to invalidate.
    #   css/js/json      -> a week. No hashed filenames, so this is a tradeoff:
    #     long enough to skip re-downloading on repeat visits, short enough
    #     that an edit reaches returning browsers without a hard refresh.
    #   everything else (HTML) -> no-cache, i.e. revalidate every time. Flask
    #     sends ETag + Last-Modified, so an unchanged page costs a 304 and the
    #     markup that points at those assets is never stale after a deploy.
    if request.method in ('GET', 'HEAD'):
        ext = os.path.splitext(request.path)[1].lower()
        if ext in _CACHE_IMMUTABLE:
            response.headers['Cache-Control'] = (
                'public, max-age=31536000, immutable')
        elif ext in _CACHE_ONE_WEEK:
            response.headers['Cache-Control'] = 'public, max-age=604800'
        else:
            response.headers['Cache-Control'] = 'no-cache'
    return response


# Per-worker timestamp of the last global prune of the rate_limits table. This
# is only a scheduling hint (not correctness-critical): each worker prunes
# fully-stale buckets at most once a minute so the table stays small without a
# background job.
_last_rate_prune = 0.0


def _prune_rate_limits_if_due(now):
    """Best-effort cleanup of buckets whose newest hit has aged out.

    Uses the indexed last_seen column (not the hits array) so pruning is a
    cheap index scan. Fails silently - pruning is hygiene, never on the
    critical path of a request.
    """
    global _last_rate_prune
    if now - _last_rate_prune <= 60:
        return
    _last_rate_prune = now
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cutoff = datetime.utcnow() - timedelta(seconds=600)
        cur.execute("DELETE FROM rate_limits WHERE last_seen < %s", (cutoff,))
        conn.commit()
        cur.close()
    except Exception:
        app.logger.exception('Rate-limit prune failed')
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def _rate_limit_allowed(bucket, max_requests, window):
    """Atomically record a hit for `bucket` and report whether it is allowed.

    Lives in Postgres so the limit is SHARED across gunicorn workers - the old
    in-memory dict was per-worker, so 2 workers silently doubled every limit.
    A per-bucket advisory lock serialises concurrent hits for the same bucket so
    the count-then-append cannot race; other buckets proceed in parallel.
    Fails OPEN on a database error: a rate-limiter outage must not take the
    whole site down (the endpoints it protects need the DB anyway).
    """
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        # Serialise only this bucket; released at commit/rollback below.
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (bucket,))
        cur.execute("SELECT hits FROM rate_limits WHERE bucket = %s", (bucket,))
        row = cur.fetchone()
        cutoff = datetime.utcnow() - timedelta(seconds=window)
        hits = [t for t in (row[0] if row else []) if t > cutoff]
        if len(hits) >= max_requests:
            conn.rollback()
            cur.close()
            return False
        hits.append(datetime.utcnow())
        cur.execute(
            "INSERT INTO rate_limits (bucket, hits, last_seen) VALUES (%s, %s, %s) "
            "ON CONFLICT (bucket) DO UPDATE SET hits = EXCLUDED.hits, "
            "last_seen = EXCLUDED.last_seen",
            (bucket, hits, hits[-1])
        )
        conn.commit()
        cur.close()
        return True
    except Exception:
        app.logger.exception('Rate-limit check failed for %s - allowing request', bucket)
        if conn:
            conn.rollback()
        return True
    finally:
        if conn:
            conn.close()


def _client_ip():
    """Best-effort real client IP behind the host's load balancer.

    Render terminates TLS at a load balancer and forwards over HTTP, appending
    its own internal address (10.x) to X-Forwarded-For. ProxyFix(x_for=1) only
    trusts ONE hop, so request.remote_addr ended up being whatever internal
    address that hop used - and it is not stable per visitor, so every request
    landed in a different rate-limit bucket and NO limit ever fired in
    production (found by probing live bucket keys).

    Strategy: walk X-Forwarded-For right-to-left and take the first globally
    routable address. Proxy hops are private/internal, so the rightmost public
    entry is the visitor. Falls back to the socket peer when the header is
    missing or holds nothing usable, which is also the local-dev path.
    """
    forwarded = request.headers.get('X-Forwarded-For', '')
    for candidate in reversed([p.strip() for p in forwarded.split(',')]):
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue  # not an address: ignore junk entries
        if ip.is_global and not ip.is_multicast:
            return candidate
    return request.remote_addr or 'unknown'


def rate_limit(max_requests=30, window=60):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            ip = _client_ip()
            bucket = f"{f.__name__}:{ip}"
            _prune_rate_limits_if_due(time.time())
            if not _rate_limit_allowed(bucket, max_requests, window):
                return jsonify({'error': 'Too many requests. Please try again later.'}), 429
            return f(*args, **kwargs)
        return wrapper
    return decorator


def create_token(user_id, email):
    payload = {
        'user_id': user_id,
        'email': email,
        'exp': (datetime.utcnow() + timedelta(days=7)).isoformat(),
        'iat': datetime.utcnow().isoformat()
    }
    data = json.dumps(payload, sort_keys=True)
    sig = hmac.new(JWT_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
    encoded = base64url_encode(data.encode())
    return f"{encoded}.{sig}"


def verify_token(token):
    try:
        parts = token.split('.')
        if len(parts) != 2:
            return None
        data = base64url_decode(parts[0]).decode()
        sig = parts[1]
        expected = hmac.new(JWT_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(data)
        # Reset tokens share this signature scheme; without this check one would
        # be accepted as a session token. Session tokens always carry a user_id.
        if 'user_id' not in payload or 'email' not in payload:
            return None
        if datetime.fromisoformat(payload['exp']) < datetime.utcnow():
            return None
        return payload
    except Exception:
        return None


def _hash_reset_token(token):
    """SHA-256 of the reset token. Only this digest is persisted, so a database
    leak cannot be replayed into an account takeover."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def create_reset_token(email):
    payload = {
        'purpose': 'password-reset',
        'email': email,
        'exp': (datetime.utcnow() + timedelta(seconds=RESET_TOKEN_VALID_SECONDS)).isoformat()
    }
    data = json.dumps(payload, sort_keys=True)
    sig = hmac.new(JWT_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
    token = f"{base64url_encode(data.encode())}.{sig}"
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        # Opportunistically drop expired rows so a server that only ever issues
        # links (and never has one redeemed) does not accumulate them forever.
        cur.execute("DELETE FROM reset_tokens WHERE exp < %s", (datetime.utcnow(),))
        cur.execute(
            "INSERT INTO reset_tokens (token_hash, email, exp) VALUES (%s, %s, %s)",
            (_hash_reset_token(token), email,
             datetime.utcnow() + timedelta(seconds=RESET_TOKEN_VALID_SECONDS))
        )
        conn.commit()
        cur.close()
    finally:
        # A failure here must propagate: the caller (forgot-password) turns it
        # into a 500 so the user is never emailed a link that cannot be redeemed.
        if conn:
            conn.close()
    return token


def verify_reset_token(token):
    """Validate a reset token against the DB. Returns the payload or None.

    The HMAC signature and expiry are checked first (cheap, no DB hit) to reject
    garbage/tampered tokens; the DB row is the source of truth for issuance and
    single-use redemption across workers and restarts.
    """
    try:
        parts = token.split('.')
        if len(parts) != 2:
            return None
        data = base64url_decode(parts[0]).decode()
        sig = parts[1]
        expected = hmac.new(JWT_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(data)
        if payload.get('purpose') != 'password-reset' or not payload.get('email'):
            return None
        if datetime.fromisoformat(payload['exp']) < datetime.utcnow():
            return None
    except Exception:
        return None

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT exp, used_at FROM reset_tokens WHERE token_hash = %s",
            (_hash_reset_token(token),)
        )
        row = cur.fetchone()
        cur.close()
    finally:
        if conn:
            conn.close()
    if not row:
        # Never issued (or pruned after expiry) - refuse it.
        return None
    exp, used_at = row
    if used_at is not None:
        return None  # already redeemed - blocks reuse across workers/restarts
    if exp < datetime.utcnow():
        return None
    return payload


def email_provider():
    """The provider that would actually send right now, or '' if none is usable.

    Kept separate from EMAIL_PROVIDER so a half-configured deploy (key set,
    From missing) reads as 'not configured' instead of failing deep inside an
    API client on a judge's click.
    """
    if EMAIL_PROVIDER == 'smtp':
        return 'smtp' if (SMTP_USER and SMTP_PASSWORD) else ''
    if EMAIL_PROVIDER == 'resend':
        return 'resend' if (RESEND_API_KEY and EMAIL_FROM) else ''
    if EMAIL_PROVIDER == 'brevo':
        return 'brevo' if (BREVO_API_KEY and EMAIL_FROM) else ''
    return ''


_RESET_SUBJECT = 'Manzil Bharat - Password Reset'


def _reset_email_parts(reset_url):
    """Plain-text and HTML bodies for the reset mail."""
    text = (
        'You requested to reset your Manzil Bharat password.\n\n'
        'Click the link below to set a new password. It is valid for 30 minutes:\n\n'
        f'{reset_url}\n\n'
        'If you did not request this, you can ignore this email.'
    )
    html = (
        '<!doctype html><html><body style="font-family:Arial,sans-serif;'
        'color:#111;line-height:1.5">'
        '<p>You requested to reset your <strong>Manzil Bharat</strong> password.</p>'
        f'<p><a href="{reset_url}" style="display:inline-block;padding:10px 18px;'
        'background:#1d4ed8;color:#fff;text-decoration:none;border-radius:6px">'
        'Set a new password</a></p>'
        '<p style="font-size:13px;color:#555">This link is valid for 30 '
        'minutes. If you did not request this, you can ignore this email.</p>'
        '</body></html>'
    )
    return text, html


def send_reset_email(email, reset_url):
    """Send the reset link through the configured provider.

    Raises on any failure: forgot_password() turns that into a 500 instead of
    telling the user a link was sent that will never arrive.
    """
    provider = email_provider()
    text, html = _reset_email_parts(reset_url)

    # HTTPS APIs, port 443 - the only route that works on hosts which block
    # outbound SMTP (Render's free plan blocks 25/465/587).
    if provider == 'resend':
        resp = requests.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {RESEND_API_KEY}'},
            json={'from': EMAIL_FROM, 'to': [email],
                  'subject': _RESET_SUBJECT, 'text': text, 'html': html},
            timeout=20
        )
        resp.raise_for_status()
        return

    if provider == 'brevo':
        resp = requests.post(
            'https://api.brevo.com/v3/smtp/email',
            headers={'api-key': BREVO_API_KEY, 'Accept': 'application/json'},
            json={'sender': {'email': EMAIL_FROM, 'name': 'Manzil Bharat'},
                  'to': [{'email': email}], 'subject': _RESET_SUBJECT,
                  'textContent': text, 'htmlContent': html},
            timeout=20
        )
        resp.raise_for_status()
        return

    if provider == 'smtp':
        msg = MIMEText(text)
        msg['Subject'] = _RESET_SUBJECT
        msg['From'] = SMTP_USER
        msg['To'] = email
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, [email], msg.as_string())
        return

    raise RuntimeError('no email provider configured')


def _with_db_param(url, key, value):
    """Set one keyword in a libpq URL, replacing any value already there."""
    parts = urlsplit(url)
    query = [kv for kv in parse_qsl(parts.query, keep_blank_values=True)
             if kv[0] != key]
    query.append((key, value))
    return urlunsplit(parts._replace(query=urlencode(query)))


def get_db():
    """Open a short-lived Postgres connection.

    Hosted databases hand out a single DATABASE_URL that already carries the
    host, credentials and sslmode (Neon and Render both set sslmode=require),
    so that one variable wins when present. Plain DB_HOST/DB_PORT/DB_NAME/
    DB_USER/DB_PASSWORD keep working for local dev, and DB_SSLMODE overrides
    either side when you need to force it.

    DB_CONNECT_TIMEOUT bounds the attempt: Neon scales compute to zero after
    5 minutes, and a health check must fail fast rather than hang on a
    sleeping instance.
    """
    url = (os.getenv('DATABASE_URL') or '').strip()
    sslmode = (os.getenv('DB_SSLMODE') or '').strip()
    timeout = (os.getenv('DB_CONNECT_TIMEOUT') or '').strip() or '5'
    try:
        timeout = str(max(1, int(timeout)))
    except ValueError:
        timeout = '5'

    if url:
        # Some consoles still print postgres://; psycopg2 only accepts
        # postgresql:// and rejects the older scheme outright.
        if url.startswith('postgres://'):
            url = 'postgresql://' + url[len('postgres://'):]
        if sslmode:
            url = _with_db_param(url, 'sslmode', sslmode)
        conn = psycopg2.connect(_with_db_param(url, 'connect_timeout', timeout))
    else:
        kwargs = {
            'host': os.getenv('DB_HOST'),
            'port': os.getenv('DB_PORT'),
            'database': os.getenv('DB_NAME'),
            'user': os.getenv('DB_USER'),
            'password': os.getenv('DB_PASSWORD'),
            'connect_timeout': int(timeout)
        }
        if sslmode:
            kwargs['sslmode'] = sslmode
        conn = psycopg2.connect(**kwargs)
    conn.autocommit = False
    return conn


def _check_phase4_tables():
    """Warn once at boot when a table from Phase 4 or 5 is missing.

    The live database was created before those phases, so it still needs
    migrations/phase4.sql and migrations/phase5.sql. A missing table is a
    deployment mistake rather than a code bug, and saying so once at boot is
    clearer than letting every request fail with a 500.
    """
    wanted = ['saved_trips', 'geocode_cache', 'consents', 'page_views']
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename = ANY(%s)",
            (wanted,),
        )
        found = {row[0] for row in cur.fetchall()}
        cur.close()
        missing = [name for name in wanted if name not in found]
        if missing:
            print('NOTE: missing table(s) ' + ', '.join(missing) + ' - run '
                  'migrations/phase4.sql and migrations/phase5.sql against '
                  'the database')
    except Exception:
        # The database may be asleep (Neon scales to zero) or unreachable.
        # That is no reason to refuse to boot.
        app.logger.info('phase 4 table check skipped: the database is unreachable')
    finally:
        if conn:
            conn.close()


_phase4_tables_checked = False


@app.before_request
def _check_phase4_tables_once():
    """Run the table check on the first request, not at import.

    At import time the database may still be starting (or asleep), and the
    test suite imports the app before it creates its schema. The check is
    skipped in testing mode for the same reason.
    """
    global _phase4_tables_checked
    if _phase4_tables_checked or app.testing:
        return
    _phase4_tables_checked = True
    _check_phase4_tables()


@app.route('/api/health')
def health():
    """Readiness probe: Render's healthCheckPath and the post-deploy smoke test.

    Answers 503 when the database cannot be reached, so a deploy with a bad
    DATABASE_URL fails visibly instead of serving 500s on every page. The probe
    is a bare SELECT 1 bounded by DB_CONNECT_TIMEOUT, which keeps it inside the
    health-check budget even while a Neon instance wakes from scale-to-zero.
    Deliberately not rate-limited: the host polls it.
    """
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT 1')
        cur.fetchone()
        cur.close()
        return jsonify({'status': 'ok', 'db': 'ok'}), 200
    except Exception:
        app.logger.exception('health check: database unreachable')
        return jsonify({'status': 'degraded', 'db': 'error'}), 503
    finally:
        if conn:
            conn.close()


@app.route('/api/transport')
@rate_limit(max_requests=60, window=60)
def transport():
    """Curated metro/rail/airport info for a demo city.

    Static JSON stand-in matching the shape a live transport provider would
    supply. Unknown cities return a 404 with the list of cities that exist.
    """
    city = (request.args.get('city') or '').strip()
    if not city:
        return jsonify({'error': 'city is required'}), 400

    data = _transport(city)
    if data is None:
        try:
            with open(TRANSPORT_PATH, encoding='utf-8') as handle:
                table = json.load(handle)
            available = sorted(table.keys()) if isinstance(table, dict) else []
        except Exception:
            available = []
        return jsonify({
            'error': 'No curated transport info for that city yet',
            'city': city,
            'available': available,
        }), 404

    return jsonify({
        'city': city.strip().lower(),
        'label': data.get('label', city),
        'metro': data.get('metro', []),
        'rail': data.get('rail', []),
        'airport': data.get('airport', []),
        'source': 'curated',
    }), 200


@app.route('/api/places')
@rate_limit(max_requests=60, window=60)
def places():
    """Curated hotels, things to do and eateries for a demo city.

    Public and read-only, served from data/<city>.json. The response is the
    contract a real provider (Google Places, Foursquare) would fill in later -
    swapping the source means changing this handler, not the frontend. An
    unknown city is an honest 404 with the list of cities that do exist, not
    an empty 200 that looks like "found nothing".
    """
    city = (request.args.get('city') or '').strip()
    if not city:
        return jsonify({'error': 'city is required'}), 400

    data = _places(city)
    if data is None:
        return jsonify({
            'error': 'No curated places for that city yet',
            'city': city,
            'available': sorted(
                os.path.splitext(name)[0] for name in os.listdir(PLACES_DIR)
                if name.endswith('.json')
                and name not in ('city_costs.json', 'transport.json', 'blog_posts.json')
            ),
        }), 404

    return jsonify({
        'city': data.get('city', city.strip().lower()),
        'label': data.get('label', city),
        'tagline': data.get('tagline', ''),
        'hotels': data.get('hotels', []),
        'things_to_do': data.get('things_to_do', []),
        'eateries': data.get('eateries', []),
        'source': 'curated',
    }), 200


# ---------------------------------------------------------------------------
# Place search - server-side proxy for the public geocoders (Phase 4 step 7).
#
# Why this is not a browser fetch any more:
#
# * Nominatim's usage policy (operations.osmfoundation.org/policies/nominatim)
#   asks every client to identify itself with a real User-Agent that carries a
#   contact address. A browser cannot set User-Agent - it is a forbidden
#   header name - so a page calling Nominatim directly can only send the
#   browser's own UA, which is exactly what the policy asks clients NOT to do.
#   Routing the call through this server lets us send the UA we choose.
# * Both public geocoders are shared community infrastructure. Their policy
#   caps how hard they may be used, so the calls are cached in a table and
#   the outbound volume is capped as well.
#
# The response is normalised into one shape for every provider, so the
# frontend does not have to know which geocoder answered.
# ---------------------------------------------------------------------------

GEOCODE_PROVIDERS = ('nominatim', 'photon')
GEOCODE_MAX_QUERY = 120
GEOCODE_MAX_RESULTS = 10
GEOCODE_CACHE_TTL_DAYS = 7
GEOCODE_CACHE_PRUNE_DAYS = 30
GEOCODE_TIMEOUT_SECONDS = 10
# The OSM policy asks for at most ~1 request per second. This budget is
# counted in Postgres, so it is shared by every gunicorn worker rather than
# being per-process.
GEOCODE_UPSTREAM_BUDGET_PER_MINUTE = 60


def _geocoder_user_agent():
    """The User-Agent this app sends to the public geocoders.

    GEOCODER_USER_AGENT overrides it; otherwise it is built from the app name
    and the admin address, which is the contact the OSM policy asks for.
    """
    configured = (os.getenv('GEOCODER_USER_AGENT') or '').strip()
    if configured:
        return configured
    contact = (os.getenv('ADMIN_EMAIL') or '').strip()
    origin = (os.getenv('APP_ORIGIN') or '').strip().split(',')[0].strip()
    agent = 'ManzilBharat/1.0'
    if origin:
        agent += ' (+' + origin + ')'
    if contact:
        agent += '; contact: ' + contact
    return agent


def _geocode_cache_key(provider, query, limit):
    """Stable hash of the normalised request.

    Lowercasing and collapsing whitespace means "  Jaipur " and "jaipur" share
    one upstream call and one cache row.
    """
    canonical = json.dumps(
        [provider, ' '.join(query.split()).lower(), limit], separators=(',', ':')
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _cached_geocode(query_key):
    """The cached result for this request, or None. A cache failure is a miss."""
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            'SELECT payload FROM geocode_cache '
            'WHERE query_key = %s '
            'AND created_at > now() - make_interval(days => %s)',
            (query_key, GEOCODE_CACHE_TTL_DAYS),
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        results = json.loads(row[0])
        return results if isinstance(results, list) else None
    except Exception:
        app.logger.exception('geocode cache read failed; treating as a miss')
        return None
    finally:
        if conn:
            conn.close()


def _store_geocode(query_key, provider, results):
    """Save a geocoder answer. Best-effort: the answer is already in hand, so a
    database blip must not turn a working search into a 500."""
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO geocode_cache (query_key, provider, payload) '
            'VALUES (%s, %s, %s) '
            'ON CONFLICT (query_key) DO UPDATE SET '
            'provider = EXCLUDED.provider, payload = EXCLUDED.payload, '
            'created_at = now()',
            (query_key, provider, json.dumps(results)),
        )
        cur.execute(
            'DELETE FROM geocode_cache '
            'WHERE created_at < now() - make_interval(days => %s)',
            (GEOCODE_CACHE_PRUNE_DAYS,),
        )
        cur.close()
        conn.commit()
    except Exception:
        app.logger.exception('geocode cache write failed')
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def _normalise_nominatim(rows):
    """Nominatim rows -> the shape the frontend uses."""
    results = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            lat = float(row.get('lat'))
            lon = float(row.get('lon'))
        except (TypeError, ValueError):
            continue
        display = str(row.get('display_name') or '').strip()
        name = str(row.get('name') or '').strip() or (display.split(',')[0].strip()
                                                     if display else '')
        results.append({
            'label': name or display,
            'display': display or name,
            'lat': lat,
            'lon': lon,
            'type': str(row.get('type') or row.get('category') or 'place'),
        })
    return results


def _normalise_photon(features):
    """Photon GeoJSON features -> the shape the frontend uses."""
    results = []
    for feature in features or []:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get('geometry') or {}
        coordinates = geometry.get('coordinates') or []
        if len(coordinates) < 2:
            continue
        try:
            lon = float(coordinates[0])
            lat = float(coordinates[1])
        except (TypeError, ValueError):
            continue
        props = feature.get('properties') or {}
        if not isinstance(props, dict):
            props = {}
        name = str(props.get('name') or '').strip()
        city = str(props.get('city') or '').strip()
        state = str(props.get('state') or '').strip()
        country = str(props.get('country') or '').strip()
        label = name or city or state or country
        context = ', '.join(part for part in (city, state, country) if part)
        results.append({
            'label': label,
            'display': (label + ', ' + context).strip(', ') if context else label,
            'lat': lat,
            'lon': lon,
            'type': str(props.get('type') or 'place'),
        })
    return results


def _fetch_geocode(provider, query, limit):
    """Ask the provider for results. Returns (results, error_message)."""
    headers = {
        'User-Agent': _geocoder_user_agent(),
        'Accept': 'application/json',
    }
    try:
        if provider == 'photon':
            response = requests.get(
                'https://photon.komoot.io/api/',
                params={'q': query, 'limit': limit},
                headers=headers,
                timeout=GEOCODE_TIMEOUT_SECONDS,
            )
        else:
            response = requests.get(
                'https://nominatim.openstreetmap.org/search',
                params={
                    'q': query,
                    'format': 'jsonv2',
                    'limit': limit,
                    'addressdetails': 1,
                },
                headers=headers,
                timeout=GEOCODE_TIMEOUT_SECONDS,
            )
    except requests.Timeout:
        app.logger.warning('geocoder %s timed out for %r', provider, query)
        return None, 'Place search timed out. Please try again.'
    except requests.RequestException:
        app.logger.exception('geocoder %s request failed', provider)
        return None, 'Place search is unavailable right now. Please try again.'

    if response.status_code != 200:
        # The provider's own body is never forwarded to the browser.
        app.logger.warning('geocoder %s answered HTTP %s', provider,
                            response.status_code)
        return None, 'Place search is unavailable right now. Please try again.'

    try:
        data = response.json()
    except ValueError:
        app.logger.warning('geocoder %s sent a body that is not JSON', provider)
        return None, 'Place search is unavailable right now. Please try again.'

    if provider == 'photon':
        features = data.get('features') if isinstance(data, dict) else None
        return _normalise_photon(features), None
    return _normalise_nominatim(data if isinstance(data, list) else None), None


def _stale_geocode(query_key):
    """A cached answer that is past its TTL - better than an error."""
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT payload FROM geocode_cache WHERE query_key = %s', (query_key,))
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        results = json.loads(row[0])
        return results if isinstance(results, list) else None
    except Exception:
        app.logger.exception('stale geocode read failed')
        return None
    finally:
        if conn:
            conn.close()


@app.route('/api/geocode')
@rate_limit(max_requests=60, window=60)
def geocode():
    """Search for a place, through the server.

    Answers {query, provider, results, cached}. The results are normalised
    into one shape for every provider: {label, display, lat, lon, type}.
    """
    query = (request.args.get('q') or '').strip()
    if not query:
        return jsonify({'error': 'q is required'}), 400
    if len(query) > GEOCODE_MAX_QUERY:
        return jsonify({'error': 'Query is too long'}), 400

    provider = (request.args.get('provider') or 'nominatim').strip().lower()
    if provider not in GEOCODE_PROVIDERS:
        return jsonify({'error': 'Unknown provider'}), 400

    try:
        limit = int(request.args.get('limit') or 5)
    except ValueError:
        limit = 5
    limit = max(1, min(limit, GEOCODE_MAX_RESULTS))

    query_key = _geocode_cache_key(provider, query, limit)

    cached = _cached_geocode(query_key)
    if cached is not None:
        return jsonify({
            'query': query,
            'provider': provider,
            'results': cached,
            'cached': True,
        }), 200

    # Outbound budget, shared by every worker through the rate_limits table.
    # Without it a burst of searches would hammer a shared community service.
    if not _rate_limit_allowed('geocode:upstream', GEOCODE_UPSTREAM_BUDGET_PER_MINUTE, 60):
        # A cached answer is still better than an error, so try the cache once
        # more ignoring its TTL before giving up.
        stale = _stale_geocode(query_key)
        if stale is not None:
            return jsonify({
                'query': query,
                'provider': provider,
                'results': stale,
                'cached': True,
            }), 200
        return jsonify({
            'error': 'Too many place searches right now. Please try again in a minute.',
        }), 429

    results, error = _fetch_geocode(provider, query, limit)
    if error:
        stale = _stale_geocode(query_key)
        if stale is not None:
            return jsonify({
                'query': query,
                'provider': provider,
                'results': stale,
                'cached': True,
            }), 200
        return jsonify({'error': error}), 502

    _store_geocode(query_key, provider, results)
    return jsonify({
        'query': query,
        'provider': provider,
        'results': results,
        'cached': False,
    }), 200


def verify_google_token(credential):
    try:
        idinfo = id_token.verify_oauth2_token(credential, google_requests.Request(), GOOGLE_CLIENT_ID)
        # A Google account can have unverified emails; trusting one would let an
        # attacker take over an account by registering the victim's address.
        if not idinfo.get('email_verified'):
            return None
        return {
            'email': idinfo.get('email'),
            'name': idinfo.get('name', ''),
            'picture': idinfo.get('picture', '')
        }
    except Exception:
        return None


@app.route('/api/signup', methods=['POST'])
@rate_limit(max_requests=10, window=60)
def signup():
    data = request.get_json()
    name = (data.get('name') or '').strip()
    # Emails are compared and stored case-insensitively, otherwise A@b.com and
    # a@b.com become two separate accounts that cannot log into each other.
    email = (data.get('email') or '').strip().lower()
    contact = (data.get('contact') or '').strip()
    gender = (data.get('gender') or '').strip()
    dob = (data.get('dob') or '').strip()
    marital_status = (data.get('marital_status') or '').strip()
    residential_address = (data.get('residential_address') or '').strip()
    pincode = (data.get('pincode') or '').strip()
    password = data.get('password') or ''
    # DPDP: consent is an explicit, affirmative act, so the client has to send
    # it. The version says WHICH text was accepted (see CONSENT_TEXT_VERSION).
    consent = data.get('consent')
    consent_version = (data.get('consent_version') or CONSENT_TEXT_VERSION).strip()

    errors = {}

    if not name:
        errors['name'] = 'Full name is required'
    elif len(name) < 2:
        errors['name'] = 'Name must be at least 2 characters'
    elif not re.match(r'^[A-Za-z\s]+$', name):
        errors['name'] = 'Name must contain only letters and spaces'

    if not email:
        errors['email'] = 'Email is required'
    elif not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
        errors['email'] = 'Enter a valid email address'

    if not contact:
        errors['contact'] = 'Contact number is required'
    elif not re.match(r'^[6-9]\d{9}$', contact):
        errors['contact'] = 'Enter a valid 10-digit Indian mobile number'

    if not gender:
        errors['gender'] = 'Please select your gender'
    elif gender not in ('Male', 'Female', 'Other'):
        errors['gender'] = 'Invalid gender selection'

    if not dob:
        errors['dob'] = 'Date of birth is required'
    else:
        try:
            dob_date = date.fromisoformat(dob)
            today = date.today()
            age = today.year - dob_date.year - ((today.month, today.day) < (dob_date.month, dob_date.day))
            if dob_date > today:
                errors['dob'] = 'Date of birth cannot be in the future'
            elif age < 18:
                errors['dob'] = 'You must be at least 18 years old'
            elif age > 120:
                errors['dob'] = 'Enter a valid date of birth'
        except ValueError:
            errors['dob'] = 'Invalid date format'

    if not marital_status:
        errors['marital_status'] = 'Please select marital status'
    elif marital_status not in ('Single', 'Married', 'Divorced', 'Widowed'):
        errors['marital_status'] = 'Invalid marital status'

    if not residential_address:
        errors['residential_address'] = 'Residential address is required'
    elif len(residential_address) < 10:
        errors['residential_address'] = 'Address must be at least 10 characters'

    if not pincode:
        errors['pincode'] = 'Pincode is required'
    elif not re.match(r'^[1-9]\d{5}$', pincode):
        errors['pincode'] = 'Enter a valid 6-digit pincode'

    if not password:
        errors['password'] = 'Password is required'
    elif len(password) < 8:
        errors['password'] = 'Password must be at least 8 characters'
    elif not re.search(r'[A-Z]', password):
        errors['password'] = 'Password must contain at least one uppercase letter'
    elif not re.search(r'[0-9]', password):
        errors['password'] = 'Password must contain at least one number'
    elif not re.search(r'[^A-Za-z0-9]', password):
        errors['password'] = 'Password must contain at least one special character'

    # Consent is validated BEFORE the account is created, so a signup that
    # skips it creates nothing at all.
    if consent is not True and consent != 'true' and consent != 'on' and consent != 1:
        errors['consent'] = 'Please accept the Privacy Policy to create an account'
    elif consent_version not in CONSENT_TEXT_VERSIONS:
        # The policy text changed since the page was loaded: ask for the new
        # consent instead of recording this one against the wrong wording.
        errors['consent'] = 'The privacy policy has changed. Please reload the page and accept it again'

    if errors:
        app.logger.info('signup refused: %d invalid field(s)', len(errors))
        return jsonify({'error': 'Validation failed', 'errors': errors}), 400

    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO users (name, email, contact, gender, dob, marital_status, residential_address, pincode, password)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, name, email, contact""",
            (name, email, contact, gender, dob, marital_status, residential_address, pincode, hashed)
        )
        user = cur.fetchone()
        # The consent row is written in the SAME transaction as the account, so
        # an account can never exist without the consent that created it. If
        # the consents table is missing (the live database has not run
        # migrations/phase4.sql yet) the account is still created - the
        # operator is told about it in the boot log and in the response.
        consent_recorded = True
        cur.execute('SAVEPOINT consent_row')
        try:
            cur.execute(
                "INSERT INTO consents (user_id, text_version, source) VALUES (%s, %s, %s)",
                (user[0], consent_version, 'signup'),
            )
        except psycopg2.errors.UndefinedTable:
            cur.execute('ROLLBACK TO SAVEPOINT consent_row')
            consent_recorded = False
            app.logger.error(
                'the consents table is missing - run migrations/phase4.sql '
                'against this database, otherwise the consent is not recorded')
        cur.close()
        conn.commit()
        token = create_token(user[0], user[2])
        app.logger.info('signup: user %s (%s) created, consent %s',
                        user[0], user[2], consent_version)
        return jsonify({'message': 'Account created successfully', 'user': {'id': user[0], 'name': user[1], 'email': user[2], 'contact': user[3]}, 'token': token, 'consent_recorded': consent_recorded}), 201
    except psycopg2.IntegrityError:
        if conn:
            conn.rollback()
        app.logger.info('signup refused: the address is already registered')
        return jsonify({'error': 'Email already exists', 'errors': {'email': 'This email is already registered'}}), 409
    except Exception:
        if conn:
            conn.rollback()
        return jsonify({'error': 'An internal error occurred. Please try again later.'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/login', methods=['POST'])
@rate_limit(max_requests=10, window=60)
def login():
    data = request.get_json()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, name, email, contact, password FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()

        # Identical message for "no such account" and "wrong password" so the
        # endpoint cannot be used to enumerate registered email addresses.
        if not user or not user[4] or not bcrypt.checkpw(password.encode('utf-8'), user[4].encode('utf-8')):
            app.logger.warning('login failed for %s', email)
            return jsonify({'error': 'Invalid email or password'}), 401

        token = create_token(user[0], user[2])
        app.logger.info('login: user %s (%s) signed in', user[0], user[2])
        return jsonify({
            'message': 'Login successful',
            'user': {'id': user[0], 'name': user[1], 'email': user[2], 'contact': user[3]},
            'token': token
        }), 200
    except Exception:
        return jsonify({'error': 'An internal error occurred. Please try again later.'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/google-login', methods=['POST'])
@rate_limit(max_requests=15, window=60)
def google_login():
    data = request.get_json()
    credential = (data.get('credential') or '').strip()

    if not credential:
        return jsonify({'error': 'Google credential is required'}), 400

    google_user = verify_google_token(credential)
    if not google_user or not google_user.get('email'):
        return jsonify({'error': 'Invalid Google credential'}), 401

    name = google_user['name']
    email = (google_user['email'] or '').strip().lower()

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT id, name, email, contact FROM users WHERE email = %s", (email,))
        user = cur.fetchone()

        if user:
            token = create_token(user[0], user[2])
            cur.close()
            app.logger.info('login (google): user %s (%s) signed in', user[0], user[2])
            return jsonify({
                'message': 'Login successful',
                'user': {'id': user[0], 'name': user[1], 'email': user[2], 'contact': user[3]},
                'token': token
            }), 200
        else:
            cur.execute(
                """INSERT INTO users (name, email) VALUES (%s, %s) RETURNING id, name, email, contact""",
                (name, email)
            )
            new_user = cur.fetchone()
            # A Google sign-in creates the account too, so the consent is
            # recorded here as well - the page says that pressing the button
            # means accepting the policy (source = 'google').
            cur.execute(
                "INSERT INTO consents (user_id, text_version, source) VALUES (%s, %s, %s)",
                (new_user[0], CONSENT_TEXT_VERSION, 'google'),
            )
            conn.commit()
            token = create_token(new_user[0], new_user[2])
            cur.close()
            app.logger.info('signup (google): user %s (%s) created', new_user[0], new_user[2])
            return jsonify({
                'message': 'Account created and logged in',
                'user': {'id': new_user[0], 'name': new_user[1], 'email': new_user[2], 'contact': new_user[3]},
                'token': token
            }), 201

    except psycopg2.IntegrityError:
        if conn:
            conn.rollback()
        return jsonify({'error': 'Email already exists'}), 409
    except Exception:
        if conn:
            conn.rollback()
        return jsonify({'error': 'An internal error occurred. Please try again later.'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/forgot-password', methods=['POST'])
@rate_limit(max_requests=3, window=300)
def forgot_password():
    data = request.get_json()
    email = (data.get('email') or '').strip().lower()

    if not email:
        return jsonify({'error': 'Email is required'}), 400

    # Truthiness, not `is None`: `SMTP_USER=` in .env yields an empty string,
    # which must count as "not configured" instead of attempting a login
    # with no credentials and failing with a misleading send error. The same
    # now covers a resend/brevo config missing its API key or From address.
    if not email_provider():
        return jsonify({'error': 'Email service is not configured on the server'}), 500

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()
        if not user:
            return jsonify({'message': 'If an account exists for this email, a password reset link has been sent.'}), 200
    except Exception:
        if conn:
            conn.rollback()
        return jsonify({'error': 'An internal error occurred. Please try again later.'}), 500
    finally:
        if conn:
            conn.close()

    origin = (data.get('origin') or request.host_url.rstrip('/')).rstrip('/')
    if origin not in ALLOWED_ORIGINS:
        origin = request.host_url.rstrip('/')

    try:
        token = create_reset_token(email)
        reset_url = f"{origin}/reset-password.html?token={token}"
        send_reset_email(email, reset_url)
    except Exception:
        # The bare except previously hid the SMTP reason (auth failure, bad
        # credentials, network) from both the operator and the log.
        app.logger.exception('Password-reset email could not be sent to %s', email)
        return jsonify({'error': 'Could not send the reset email. Please try again later.'}), 500

    app.logger.info('password reset: a link was sent to %s', email)
    return jsonify({'message': 'A password reset link has been sent to your email. It is valid for 30 minutes.'}), 200


@app.route('/api/reset-password', methods=['POST'])
@rate_limit(max_requests=5, window=60)
def reset_password():
    data = request.get_json()
    token = (data.get('token') or '').strip()
    new_password = data.get('new_password') or ''

    if not token or not new_password:
        return jsonify({'error': 'Reset token and new password are required'}), 400

    payload = verify_reset_token(token)
    if not payload:
        return jsonify({'error': 'Invalid or expired reset link. Please request a new one.'}), 400

    if len(new_password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    if not re.search(r'[A-Z]', new_password):
        return jsonify({'error': 'Password must contain at least one uppercase letter'}), 400
    if not re.search(r'[0-9]', new_password):
        return jsonify({'error': 'Password must contain at least one number'}), 400
    if not re.search(r'[^A-Za-z0-9]', new_password):
        return jsonify({'error': 'Password must contain at least one special character'}), 400

    email = payload['email']
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        if not user:
            return jsonify({'error': 'No account found for this link'}), 404

        hashed = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        cur.execute("UPDATE users SET password = %s WHERE id = %s", (hashed, user[0]))
        # Mark the token redeemed in the SAME transaction as the password change,
        # so a crash can never leave a still-valid token behind. This is what
        # blocks reuse across workers and restarts.
        cur.execute("UPDATE reset_tokens SET used_at = %s WHERE token_hash = %s",
                    (datetime.utcnow(), _hash_reset_token(token)))
        conn.commit()
        cur.close()
        app.logger.info('password reset: the password of user %s (%s) was changed', user[0], email)
        return jsonify({'message': 'Password updated successfully'}), 200
    except Exception:
        if conn:
            conn.rollback()
        return jsonify({'error': 'An internal error occurred. Please try again later.'}), 500
    finally:
        if conn:
            conn.close()


def get_bearer_token():
    header = request.headers.get('Authorization', '')
    if header[:7].lower() == 'bearer ':
        return header[7:].strip()
    return ''


def require_user(f):
    """Guard an endpoint with a user session token.

    Sent as:  Authorization: Bearer <token>
    Admin sessions carry user_id 0 (the admin sentinel) and are refused
    here, so an admin login can never act as a normal user's identity.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        payload = verify_token(get_bearer_token())
        if not payload:
            return jsonify({'error': 'Unauthorized'}), 401
        if payload.get('user_id') == 0:
            return jsonify({'error': 'Forbidden'}), 403
        g.user = payload
        return f(*args, **kwargs)
    return wrapper


@app.route('/api/me', methods=['GET'])
@require_user
def get_me():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT id, name, email, contact, created_at FROM users WHERE id = %s",
            (g.user['user_id'],)
        )
        user = cur.fetchone()
        cur.close()
        if not user:
            # Token verifies but the account is gone (deleted by admin):
            # tell the client so it can drop the stale session.
            return jsonify({'error': 'Account no longer exists'}), 404
        return jsonify(dict(user)), 200
    except Exception:
        return jsonify({'error': 'Could not load your profile'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/me/consent', methods=['POST'])
@require_user
@rate_limit(max_requests=10, window=60)
def record_consent():
    """Record that the user has given explicit consent (DPDP).

    Used to re-record consent after the policy text changes. The version is
    checked against the ones this build knows about, so the consent history
    always says which wording was accepted.
    """
    data = request.get_json(silent=True) or {}
    version = (data.get('version') or CONSENT_TEXT_VERSION).strip()
    if not version:
        version = CONSENT_TEXT_VERSION
    if version not in CONSENT_TEXT_VERSIONS:
        return jsonify({'error': 'Unknown consent version', 'versions': sorted(CONSENT_TEXT_VERSIONS)}), 400
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "INSERT INTO consents (user_id, text_version, source) VALUES (%s, %s, %s) RETURNING id, user_id, text_version, source, created_at",
            (g.user['user_id'], version, 'renewal'),
        )
        row = dict(cur.fetchone())
        conn.commit()
        cur.close()
        app.logger.info('consent: user %s recorded version %s (source: renewal)',
                        g.user['user_id'], version)
        return jsonify({'message': 'Consent recorded', 'consent': row}), 201
    except psycopg2.errors.UndefinedTable:
        if conn:
            conn.rollback()
        app.logger.exception('consents is missing - run migrations/phase4.sql')
        return jsonify({'error': 'Consent is not available right now.'}), 503
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('record_consent failed for user %s', g.user['user_id'])
        return jsonify({'error': 'Could not record consent'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/me/consent', methods=['GET'])
@require_user
def list_consents():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT id, user_id, text_version, source, created_at FROM consents WHERE user_id = %s ORDER BY created_at DESC, id DESC",
            (g.user['user_id'],),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return jsonify(rows), 200
    except psycopg2.errors.UndefinedTable:
        if conn:
            conn.rollback()
        return jsonify([]), 200
    except Exception:
        app.logger.exception('list_consents failed for user %s', g.user['user_id'])
        return jsonify({'error': 'Could not load consents'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/me/export', methods=['GET'])
@require_user
def export_me():
    """Self-serve data export (DPDP).

    Returns everything the server holds about the caller as one JSON document:
    the profile, the bookings, the saved itineraries, the payments and the
    consent history. Only the caller's own rows are read - the token decides
    whose data it is, so nobody can ask for somebody else's.
    """
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, name, email, contact, gender, dob, marital_status, residential_address, pincode, created_at FROM users WHERE id = %s", (g.user['user_id'],))
        user = cur.fetchone()
        if not user:
            cur.close()
            return jsonify({'error': 'Account no longer exists'}), 404
        cur.execute("SELECT id, booking_type, source, destination, travel_date, amount, status, created_at FROM bookings WHERE user_id = %s ORDER BY created_at DESC", (g.user['user_id'],))
        bookings = [dict(r) for r in cur.fetchall()]
        # The payload holds the actual itinerary the user saved, so it belongs
        # in the export too.
        cur.execute("SELECT id, title, payload, created_at FROM saved_trips WHERE user_id = %s ORDER BY created_at DESC", (g.user['user_id'],))
        saved_trips = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT id, text_version, source, created_at FROM consents WHERE user_id = %s ORDER BY created_at DESC", (g.user['user_id'],))
        consents = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT id, amount, currency, booking_type, status, created_at FROM payments WHERE user_id = %s ORDER BY created_at DESC", (g.user['user_id'],))
        payments = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT id, email, created_at FROM subscribers WHERE email = %s", (user['email'],))
        newsletter = [dict(r) for r in cur.fetchall()]
        cur.close()
        app.logger.info('privacy: user %s exported their data', g.user['user_id'])
        return jsonify({
            'user': dict(user),
            'bookings': bookings,
            'saved_trips': saved_trips,
            'payments': payments,
            'consents': consents,
            'newsletter': newsletter,
            'exported_at': datetime.utcnow().isoformat() + 'Z',
        }), 200
    except Exception:
        app.logger.exception('export_me failed for user %s', g.user['user_id'])
        return jsonify({'error': 'Could not export your data'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/me', methods=['DELETE'])
@require_user
@rate_limit(max_requests=5, window=300)
def delete_me():
    """Self-serve account deletion (DPDP).

    The bookings, saved itineraries, payments and consent rows of that
    account go with it (ON DELETE CASCADE in the schema), so nothing about
    the person is left behind.
    """
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE id = %s RETURNING id", (g.user['user_id'],))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            cur.close()
            return jsonify({'error': 'Account no longer exists'}), 404
        conn.commit()
        cur.close()
        app.logger.info('privacy: user %s deleted their account and its data', g.user['user_id'])
        return jsonify({'message': 'Account deleted'}), 200
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('delete_me failed for user %s', g.user['user_id'])
        return jsonify({'error': 'Could not delete your account'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/me/password', methods=['PUT'])
@require_user
@rate_limit(max_requests=10, window=60)
def change_password():
    """Password change for a signed-in user.

    A reset email alone is not enough (DPDP + basic hygiene): someone at a
    shared computer must be able to lock the account without inbox access.
    The endpoint is bcrypt-expensive and a brute-force target, hence the
    rate limit on top of require_user.
    """
    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or ''
    new_password = data.get('new_password') or ''

    errors = {}
    if not current_password:
        errors['current_password'] = 'Current password is required'
    if not new_password:
        errors['new_password'] = 'New password is required'
    elif len(new_password) < 8:
        errors['new_password'] = 'Password must be at least 8 characters'
    elif not re.search(r'[A-Z]', new_password):
        errors['new_password'] = 'Password must contain at least one uppercase letter'
    elif not re.search(r'[0-9]', new_password):
        errors['new_password'] = 'Password must contain at least one number'
    elif not re.search(r'[^A-Za-z0-9]', new_password):
        errors['new_password'] = 'Password must contain at least one special character'
    elif new_password == current_password:
        errors['new_password'] = 'New password must be different from the current one'
    if errors:
        return jsonify({'error': 'Validation failed', 'errors': errors}), 400

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT password FROM users WHERE id = %s", (g.user['user_id'],))
        row = cur.fetchone()
        if not row:
            cur.close()
            return jsonify({'error': 'Account no longer exists'}), 404
        stored = row[0] or ''
        # NOTE: a WRONG current password answers 401 on purpose, but its body
        # carries errors.current_password so the client can tell it apart
        # from require_user's "your session is dead" 401.
        if not stored or not bcrypt.checkpw(current_password.encode('utf-8'), stored.encode('utf-8')):
            cur.close()
            return jsonify({'error': 'Current password is incorrect',
                            'errors': {'current_password': 'Current password is incorrect'}}), 401
        hashed = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        cur.execute("UPDATE users SET password = %s WHERE id = %s", (hashed, g.user['user_id']))
        conn.commit()
        cur.close()
        app.logger.info('Password changed for user %s', g.user['user_id'])
        return jsonify({'message': 'Password updated. Use your new password the next time you log in.'}), 200
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('change_password failed for user %s', g.user['user_id'])
        return jsonify({'error': 'Could not change your password. Please try again later.'}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# Newsletter + contact - public, no account needed. The old frontend
# "confirmed" a subscription without asking the server; these routes make
# both forms store something real.
# ---------------------------------------------------------------------------

EMAIL_RE = r'^[^\s@]+@[^\s@]+\.[^\s@]+$'


@app.route('/api/newsletter', methods=['POST'])
@rate_limit(max_requests=10, window=60)
def newsletter_subscribe():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()

    if not email:
        return jsonify({'error': 'Email is required', 'errors': {'email': 'Email is required'}}), 400
    if len(email) > 255 or not re.match(EMAIL_RE, email):
        return jsonify({'error': 'Validation failed', 'errors': {'email': 'Enter a valid email address'}}), 400

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        # Idempotent: a re-submit (or someone else typing your address)
        # must not explode on the UNIQUE constraint or leak subscribers.
        cur.execute(
            "INSERT INTO subscribers (email) VALUES (%s) ON CONFLICT (email) DO NOTHING",
            (email,)
        )
        inserted = cur.rowcount
        conn.commit()
        cur.close()
        if inserted:
            return jsonify({'message': 'Subscribed! Watch your inbox for travel deals.'}), 201
        return jsonify({'message': 'You are already subscribed. Nothing to do!'}), 200
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('newsletter_subscribe failed for %s', email)
        return jsonify({'error': 'Could not subscribe right now. Please try again later.'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/contact', methods=['POST'])
@rate_limit(max_requests=5, window=60)
def contact_message():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    subject = (data.get('subject') or '').strip()
    body = (data.get('body') or '').strip()

    errors = {}
    if not name:
        errors['name'] = 'Name is required'
    elif len(name) > 255:
        errors['name'] = 'Name is too long (max 255 characters)'
    if not email:
        errors['email'] = 'Email is required'
    elif len(email) > 255 or not re.match(EMAIL_RE, email):
        errors['email'] = 'Enter a valid email address'
    if not subject:
        errors['subject'] = 'Subject is required'
    elif len(subject) > 255:
        errors['subject'] = 'Subject is too long (max 255 characters)'
    if not body:
        errors['body'] = 'Message is required'
    elif len(body) < 10:
        errors['body'] = 'Message must be at least 10 characters'
    elif len(body) > 5000:
        errors['body'] = 'Message is too long (max 5000 characters)'

    if errors:
        return jsonify({'error': 'Validation failed', 'errors': errors}), 400

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO messages (name, email, subject, body) VALUES (%s, %s, %s, %s)",
            (name, email, subject, body)
        )
        conn.commit()
        cur.close()
        return jsonify({'message': 'Thanks for reaching out! We will get back to you within 1 working day.'}), 201
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('contact_message failed for %s', email)
        return jsonify({'error': 'Could not send your message. Please try again later.'}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# Bookings - the user-facing half. Admin CRUD (GET/PUT/DELETE on
# /api/bookings/<id>) stays behind require_admin further down; users get
# create / list-own / cancel-own here so a customer can never touch
# someone else's row or hard-delete history (cancelling keeps the audit
# trail the admin panel reports on).
# ---------------------------------------------------------------------------

BOOKING_TYPES = ('flight', 'bus', 'train', 'cab', 'hotel', 'package')
MAX_BOOKING_AMOUNT = 1000000


@app.route('/api/bookings', methods=['POST'])
@require_user
def create_booking():
    data = request.get_json(silent=True) or {}
    booking_type = (data.get('booking_type') or '').strip().lower()
    source = (data.get('source') or '').strip()
    destination = (data.get('destination') or '').strip()
    travel_date_raw = (data.get('travel_date') or '').strip()
    amount_raw = data.get('amount')

    errors = {}

    if booking_type not in BOOKING_TYPES:
        errors['booking_type'] = 'Choose a trip type: ' + ', '.join(BOOKING_TYPES)
    if not source:
        errors['source'] = 'Source city is required'
    elif len(source) > 255:
        errors['source'] = 'Source is too long (max 255 characters)'
    if not destination:
        errors['destination'] = 'Destination is required'
    elif len(destination) > 255:
        errors['destination'] = 'Destination is too long (max 255 characters)'

    travel_date = None
    if not travel_date_raw:
        errors['travel_date'] = 'Travel date is required'
    else:
        try:
            travel_date = date.fromisoformat(travel_date_raw)
            if travel_date < date.today():
                errors['travel_date'] = 'Travel date cannot be in the past'
        except ValueError:
            errors['travel_date'] = 'Use the YYYY-MM-DD format'

    amount = None
    if isinstance(amount_raw, bool) or amount_raw is None or amount_raw == '':
        errors['amount'] = 'Estimated amount is required'
    else:
        try:
            amount = round(float(amount_raw), 2)
            # NaN fails both comparisons, inf fails the upper bound.
            if not (0 <= amount <= MAX_BOOKING_AMOUNT):
                raise ValueError
        except (TypeError, ValueError):
            amount = None
            errors['amount'] = f'Amount must be a number between 0 and {MAX_BOOKING_AMOUNT:,}'

    if errors:
        return jsonify({'error': 'Validation failed', 'errors': errors}), 400

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """INSERT INTO bookings (user_id, booking_type, source, destination, travel_date, amount, status)
               VALUES (%s, %s, %s, %s, %s, %s, 'pending')
               RETURNING *""",
            (g.user['user_id'], booking_type, source, destination, travel_date, amount)
        )
        booking = dict(cur.fetchone())
        conn.commit()
        cur.close()
        return jsonify({'message': 'Booking created', 'booking': booking}), 201
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('create_booking failed for user %s', g.user['user_id'])
        return jsonify({'error': 'Could not save the booking. Please try again.'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/bookings/mine', methods=['GET'])
@require_user
def my_bookings():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM bookings WHERE user_id = %s ORDER BY created_at DESC, id DESC",
            (g.user['user_id'],)
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return jsonify(rows), 200
    except Exception:
        app.logger.exception('my_bookings failed for user %s', g.user['user_id'])
        return jsonify({'error': 'Could not load your bookings'}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# Saved itineraries (Phase 4 step 6).
#
# A saved trip is the WHOLE plan the visitor built on trip.html: the day
# cards, the budget estimate and the places/transport cards for one
# destination. The row belongs to exactly one user (ON DELETE CASCADE takes
# it with the account) and the plan itself is stored as JSON, so a saved
# itinerary can be re-rendered later without asking the AI for it again.
#
# This is what makes the product personalised: the plan follows the user
# between visits, instead of living in one browser tab.
# ---------------------------------------------------------------------------

SAVED_TRIP_MAX_TITLE = 255
SAVED_TRIP_MAX_PAYLOAD_BYTES = 200_000
# A ceiling on how many plans one account can keep. It sits below the POST
# rate limit (30/min) on purpose, so the two limits never overlap.
SAVED_TRIP_MAX_PER_USER = 25


def _saved_trip_json(payload):
    """Check the saved plan and return (text, error_message).

    The payload must be a JSON object - the plan trip.html builds is a dict -
    and must not be absurdly large. Its CONTENTS are not interpreted here:
    trip.html owns the shape, and a plan saved before a page change must
    still render afterwards.
    """
    if not isinstance(payload, dict):
        return None, 'Payload must be an object'
    try:
        text = json.dumps(payload)
    except (TypeError, ValueError):
        return None, 'Payload must be JSON-serialisable'
    if len(text.encode('utf-8')) > SAVED_TRIP_MAX_PAYLOAD_BYTES:
        return None, 'Payload is too large'
    return text, None


@app.route('/api/me/trips', methods=['POST'])
@require_user
@rate_limit(max_requests=30, window=60)
def create_saved_trip():
    """Save the current plan for the signed-in user.

    Saving the SAME plan twice is not an error: the existing row is returned
    (200) instead of a duplicate, so a double-click on the button cannot fill
    My Trips with copies of one itinerary.
    """
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    if len(title) > SAVED_TRIP_MAX_TITLE:
        return jsonify({'error': 'Title is too long'}), 400
    if 'payload' not in data or data.get('payload') is None:
        return jsonify({'error': 'Payload is required'}), 400

    payload_text, error = _saved_trip_json(data.get('payload'))
    if error:
        return jsonify({'error': error}), 400

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Same user, same title, same plan -> return the row that is already
        # there instead of writing a second copy.
        cur.execute(
            "SELECT id, user_id, title, payload, created_at FROM saved_trips "
            "WHERE user_id = %s AND title = %s AND payload = %s "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (g.user['user_id'], title, payload_text),
        )
        existing = cur.fetchone()
        if existing:
            row = dict(existing)
            try:
                row['payload'] = json.loads(row['payload'])
            except ValueError:
                row['payload'] = None
            cur.close()
            return jsonify({
                'message': 'This itinerary is already saved',
                'trip': row,
            }), 200

        cur.execute(
            'SELECT COUNT(*) AS saved FROM saved_trips WHERE user_id = %s',
            (g.user['user_id'],),
        )
        count = cur.fetchone()['saved']
        if count >= SAVED_TRIP_MAX_PER_USER:
            cur.close()
            return jsonify({
                'error': 'You have reached the limit of saved itineraries. '
                         'Remove one to save another.',
            }), 409

        cur.execute(
            """INSERT INTO saved_trips (user_id, title, payload)
               VALUES (%s, %s, %s)
               RETURNING id, user_id, title, payload, created_at""",
            (g.user['user_id'], title, payload_text),
        )
        row = dict(cur.fetchone())
        conn.commit()
        cur.close()
        try:
            row['payload'] = json.loads(row['payload'])
        except ValueError:
            row['payload'] = None
        return jsonify({'message': 'Itinerary saved', 'trip': row}), 201
    except psycopg2.errors.UndefinedTable:
        if conn:
            conn.rollback()
        app.logger.exception('saved_trips is missing - run migrations/phase4.sql')
        return jsonify({
            'error': 'Saved itineraries are not available right now. Please try again later.',
        }), 503
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('create_saved_trip failed for user %s', g.user['user_id'])
        return jsonify({'error': 'Could not save the itinerary'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/me/trips', methods=['GET'])
@require_user
@rate_limit(max_requests=60, window=60)
def list_saved_trips():
    """The signed-in user's saved itineraries, newest first."""
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT id, user_id, title, payload, created_at FROM saved_trips "
            "WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
            (g.user['user_id'], SAVED_TRIP_MAX_PER_USER),
        )
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            try:
                d['payload'] = json.loads(d['payload'])
            except ValueError:
                d['payload'] = None
            rows.append(d)
        cur.close()
        return jsonify(rows), 200
    except psycopg2.errors.UndefinedTable:
        if conn:
            conn.rollback()
        app.logger.exception('saved_trips is missing - run migrations/phase4.sql')
        return jsonify({
            'error': 'Saved itineraries are not available right now. Please try again later.',
        }), 503
    except Exception:
        app.logger.exception('list_saved_trips failed for user %s', g.user['user_id'])
        return jsonify({'error': 'Could not load saved itineraries'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/me/trips/<int:trip_id>', methods=['DELETE'])
@require_user
def delete_saved_trip(trip_id):
    """Remove one saved itinerary.

    404 when the row does not exist, 403 when it belongs to somebody else -
    the ownership check happens before the delete, so a guessed id cannot
    remove another user's plan.
    """
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, user_id, title FROM saved_trips WHERE id = %s", (trip_id,))
        trip = cur.fetchone()
        if not trip:
            cur.close()
            return jsonify({'error': 'Saved itinerary not found'}), 404
        if trip['user_id'] != g.user['user_id']:
            cur.close()
            return jsonify({'error': 'You can only remove your own saved itineraries'}), 403

        cur.execute("DELETE FROM saved_trips WHERE id = %s RETURNING id, title", (trip_id,))
        deleted = dict(cur.fetchone())
        conn.commit()
        cur.close()
        return jsonify({'message': 'Saved itinerary removed', 'trip': deleted}), 200
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('delete_saved_trip %s failed for user %s',
                             trip_id, g.user['user_id'])
        return jsonify({'error': 'Could not remove the saved itinerary'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/bookings/<int:booking_id>/cancel', methods=['POST'])
@require_user
def cancel_booking(booking_id):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM bookings WHERE id = %s", (booking_id,))
        booking = cur.fetchone()
        if not booking:
            cur.close()
            return jsonify({'error': 'Booking not found'}), 404
        if booking['user_id'] != g.user['user_id']:
            cur.close()
            return jsonify({'error': 'You can only cancel your own bookings'}), 403
        if booking['status'] == 'completed':
            cur.close()
            return jsonify({'error': 'A completed booking cannot be cancelled'}), 409
        if booking['status'] == 'cancelled':
            cur.close()
            return jsonify({'message': 'Booking is already cancelled', 'booking': dict(booking)}), 200

        cur.execute("UPDATE bookings SET status = 'cancelled' WHERE id = %s RETURNING *", (booking_id,))
        updated = dict(cur.fetchone())
        conn.commit()
        cur.close()
        return jsonify({'message': 'Booking cancelled', 'booking': updated}), 200
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('cancel_booking %s failed for user %s', booking_id, g.user['user_id'])
        return jsonify({'error': 'Could not cancel the booking'}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# AI travel guide - server-side proxy for the Gemini API.
#
# The key is read from .env and never leaves this process. index.html and
# map.html used to call Google directly with a key from config.js, which
# publishes the key to every visitor; a published key gets scraped and
# billed within days. The rate limit protects the quota as well.
#
# Two kinds answer with prose ('destination', 'trip'). A third, 'itinerary',
# answers with STRUCTURED day cards: Gemini is asked for JSON, the reply is
# parsed and validated here, and the result is cached in itinerary_cache so
# the same request does not spend Gemini quota twice. Itinerary responses also
# carry a budget estimate (data/city_costs.json) for the same days.
# ---------------------------------------------------------------------------

AI_PROMPTS = {
    'destination': (
        'You are Manzil Bharat, a travel guide. Given the destination "{query}", '
        'respond as a compact travel card using these EXACT section headers on '
        'separate lines: "Why go", "Best time to visit", "Top things to do", '
        '"Travel tip". Keep it under 120 words, no markdown bold, plain text with '
        'each section on its own line starting with the header.'
    ),
    'trip': (
        'You are Manzil Bharat, a travel assistant for India travel. User query: '
        '"{query}". Reply as 3 suggested destinations, one per line, formatted as '
        '"PlaceName - one-line reason". Then on its own line start with "Travel '
        'tip:" give one general tip. Under 120 words. No markdown.'
    ),
}

# Every extra day is another Gemini output block, and the free tier's quota
# is shared with the prose kinds, so the planner is deliberately bounded.
ITINERARY_MAX_DAYS = 10
ITINERARY_CACHE_TTL_DAYS = 7
# Gemini is asked for 12 words per slot; this cap only stops a runaway reply
# from bloating both the cache row and the rendered card.
ITINERARY_MAX_SLOT_CHARS = 500
ITINERARY_MAX_DAY_COST = 1_000_000  # rupees; anything above is an artefact
ITINERARY_CACHE_PRUNE_DAYS = 30

# Hand-written per-day cost model for the demo cities (data/city_costs.json).
# The AI is good at *what to do* and unreliable at arithmetic, so it supplies
# the day plan while these numbers supply the price shape: the breakdown is a
# fixed split of the trip total, which is why it is a file a human edits
# rather than another model call. Missing/invalid file = no breakdown, and
# the itinerary still renders.
CITY_COSTS_PATH = os.path.join(PROJECT_ROOT, 'data', 'city_costs.json')
BUDGET_HEADS = ('stay', 'transport', 'food', 'activities')
BUDGET_LEVELS = ('budget', 'mid-range', 'luxury')

# Hand-written places for the demo cities (data/<city>.json): hotels, things to
# do and eateries, chosen by a person and checkable against a map. This is the
# provider-shaped stand-in the roadmap asks for - the API contract is the real
# one, the data is curated until a paid provider's key exists.
PLACES_DIR = os.path.join(PROJECT_ROOT, 'data')
PLACES_CITY_RE = re.compile(r'^[a-z][a-z0-9-]{1,40}$')


class _ItineraryError(Exception):
    """The model's reply could not be turned into a usable itinerary."""


def _city_costs():
    """The price model, read fresh but cheaply.

    Cached on the file's mtime so editing data/city_costs.json on a running
    server takes effect without a restart, while a warm server does not hit
    the disk on every request.
    """
    try:
        mtime = os.path.getmtime(CITY_COSTS_PATH)
    except OSError:
        app.logger.warning('city cost table is missing: %s', CITY_COSTS_PATH)
        return None
    cached = getattr(_city_costs, '_cache', None)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(CITY_COSTS_PATH, encoding='utf-8') as handle:
            table = json.load(handle)
    except (OSError, ValueError):
        app.logger.exception('city cost table is unreadable: %s', CITY_COSTS_PATH)
        return None
    if not isinstance(table, dict) or not isinstance(table.get('cities'), dict):
        app.logger.warning('city cost table has an unexpected shape')
        return None
    _city_costs._cache = (mtime, table)
    return table


def _places(city):
    """Curated places for one city, or None when there is no file for it.

    The slug is validated against a strict pattern *before* it touches the
    filesystem, so a request like ?city=../../etc/passwd cannot read anything:
    the only reachable names are lowercase words under data/. Same mtime cache
    trick as the price table - editing a city file applies without a restart.
    """
    slug = str(city or '').strip().lower()
    if not PLACES_CITY_RE.match(slug):
        return None
    path = os.path.join(PLACES_DIR, slug + '.json')
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cache = getattr(_places, '_cache', {})
    if cache.get(slug, (None,))[0] == mtime:
        return cache[slug][1]
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        app.logger.exception('city place file is unreadable: %s', path)
        return None
    if not isinstance(data, dict):
        app.logger.warning('city place file has an unexpected shape: %s', path)
        return None
    _places._cache = {**cache, slug: (mtime, data)}
    return data


# Hand-written transport info for the demo cities (data/transport.json): metro,
# rail and airport details, curated to be judge-checkable. Same pattern as
# places - static JSON, validated slugs, mtime cached.
TRANSPORT_PATH = os.path.join(PROJECT_ROOT, 'data', 'transport.json')


def _transport(city):
    """Curated transport info for one city, or None when unavailable.

    Validates the slug against the same strict pattern used for places so
    filesystem access cannot be escaped. Cached on mtime.
    """
    slug = str(city or '').strip().lower()
    if not PLACES_CITY_RE.match(slug):
        return None
    cache = getattr(_transport, '_cache', {})
    try:
        mtime = os.path.getmtime(TRANSPORT_PATH)
    except OSError:
        return None
    if cache.get('mtime') == mtime:
        table = cache.get('table')
    else:
        try:
            with open(TRANSPORT_PATH, encoding='utf-8') as handle:
                table = json.load(handle)
        except (OSError, ValueError):
            app.logger.exception('transport table is unreadable: %s', TRANSPORT_PATH)
            return None
        if not isinstance(table, dict):
            app.logger.warning('transport table has an unexpected shape')
            return None
        _transport._cache = {'mtime': mtime, 'table': table}
    if not isinstance(table, dict):
        return None
    data = table.get(slug)
    if not isinstance(data, dict):
        return None
    return data


def _budget_level(value):
    """Map whatever the visitor typed onto a table key ('Upscale' -> luxury)."""
    text = str(value or '').strip().lower()
    if text in BUDGET_LEVELS:
        return text
    if any(word in text for word in ('luxury', 'lux', 'premium', 'upscale', 'high')):
        return 'luxury'
    if any(word in text for word in ('cheap', 'budget', 'backpack', 'low', 'economy')):
        return 'budget'
    return 'mid-range'


def _budget_estimate(plan, days):
    """Total, per-day and the four-head breakdown for a plan.

    Every figure is derived from the itinerary the visitor actually got: the
    total is the sum of the AI's day costs, and the heads are fixed
    percentages of that total, so the parts always add up to the whole
    (integers, with the rounding remainder given to the last head). Cities
    outside data/city_costs.json answer with the fallback rate and
    matched=false, which the page states plainly instead of pretending.
    """
    total = sum(day['cost'] for day in days)
    level = _budget_level(plan['budget'])
    table = _city_costs()

    city = None
    matched = False
    if table:
        cities = table['cities']
        city = cities.get(plan['destination'].strip().lower())
        matched = city is not None
        if not matched:
            city = table.get('default')
    if not isinstance(city, dict):
        city = {}

    per_day = city.get('per_day') if isinstance(city.get('per_day'), dict) else {}
    split = city.get('split') if isinstance(city.get('split'), dict) else {}
    reference = per_day.get(level)
    if not isinstance(reference, (int, float)) or reference <= 0:
        reference = 0

    # A split that does not add up (hand-edited file) would quietly invent or
    # lose money, so fall back to an even one.
    weights = [split.get(head) for head in BUDGET_HEADS]
    if any(not isinstance(w, (int, float)) or w < 0 for w in weights) or sum(weights) <= 0:
        weights = None
    if weights is None:
        weights = [1 / len(BUDGET_HEADS)] * len(BUDGET_HEADS)

    breakdown = {}
    allocated = 0
    for head, weight in zip(BUDGET_HEADS, weights):
        amount = round(total * weight)
        breakdown[head] = amount
        allocated += amount
    breakdown[BUDGET_HEADS[-1]] += total - allocated

    label = city.get('label')
    return {
        'trip_total': total,
        'per_day': total // len(days),
        'breakdown': breakdown,
        'level': level,
        'baseline_per_day': int(reference),
        # Only a matched city may rename the trip; for anywhere else the
        # visitor's own words are the better label.
        'city': label.strip() if matched and isinstance(label, str) and label.strip()
        else plan['destination'],
        'matched': matched,
    }


def _gemini_text(prompt, json_mode=False):
    """One Gemini call. Returns (text, None) or (None, (message, status)).

    Shared by every guide kind so timeout, quota and upstream-error handling
    (including the rule that Google's own body is never forwarded) cannot
    drift between them. json_mode asks Gemini for an application/json reply
    instead of prose.
    """
    payload = {'contents': [{'parts': [{'text': prompt}]}]}
    if json_mode:
        payload['generationConfig'] = {'responseMimeType': 'application/json'}

    try:
        resp = requests.post(
            'https://generativelanguage.googleapis.com/v1beta/models/'
            + GEMINI_MODEL + ':generateContent',
            params={'key': GEMINI_API_KEY},
            json=payload,
            timeout=30,
        )
    except requests.exceptions.Timeout:
        return None, ('The AI service timed out. Please try again.', 504)
    except requests.exceptions.RequestException:
        return None, ('Could not reach the AI service. Please try again later.', 502)

    if resp.status_code == 429:
        return None, ('The AI service is rate-limited right now. Please try again in a minute.', 429)
    if resp.status_code != 200:
        # Log server-side for debugging, but never forward Google's body to
        # the client (it can echo request details).
        app.logger.warning('Gemini returned HTTP %s: %s', resp.status_code, resp.text[:300])
        return None, ('The AI service returned an error. Please try again later.', 502)

    try:
        body = resp.json()
        parts = body['candidates'][0]['content']['parts']
        text = ''.join(p.get('text') or '' for p in parts).strip()
    except (ValueError, KeyError, IndexError, TypeError):
        app.logger.warning('Gemini response had unexpected shape: %s', resp.text[:300])
        return None, ('The AI service returned an unexpected response.', 502)

    if not text:
        return None, ('The AI service returned an empty response. Please try again.', 502)
    return text, None


def _itinerary_request(data):
    """Validate the itinerary body. Returns (plan, None) or (None, (error, status))."""
    destination = str(data.get('destination') or '').strip()
    if not destination:
        return None, ('A destination is required', 400)
    if len(destination) > 120:
        return None, ('Destination is too long (max 120 characters)', 400)

    raw_days = data.get('days')
    if isinstance(raw_days, bool) or not isinstance(raw_days, (int, str)):
        return None, ('days must be a whole number from 1 to 10', 400)
    try:
        days = int(str(raw_days).strip())
    except ValueError:
        return None, ('days must be a whole number from 1 to 10', 400)
    if days < 1 or days > ITINERARY_MAX_DAYS:
        return None, (f'days must be between 1 and {ITINERARY_MAX_DAYS}', 400)

    budget = str(data.get('budget') or '').strip() or 'mid-range'
    if len(budget) > 60:
        return None, ('Budget is too long (max 60 characters)', 400)
    interests = str(data.get('interests') or '').strip() or 'sightseeing, local food'
    if len(interests) > 200:
        return None, ('Interests are too long (max 200 characters)', 400)

    return {
        'destination': destination,
        'days': days,
        'budget': budget,
        'interests': interests,
    }, None


def _itinerary_cache_key(plan):
    """Stable hash of the normalised request.

    Lowercasing and sorting the comma-separated interests means
    "Jaipur, 3 days, forts, food" and "jaipur, 3 days, food, forts" share one
    Gemini call.
    """
    interests = sorted(
        part.strip().lower() for part in plan['interests'].split(',') if part.strip()
    )
    canonical = json.dumps(
        [plan['destination'].strip().lower(), plan['days'],
         plan['budget'].strip().lower(), interests],
        separators=(',', ':'),
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _cached_itinerary(query_hash):
    """Fresh cached days for this request, or None. A cache failure is a miss."""
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            'SELECT payload FROM itinerary_cache '
            'WHERE query_hash = %s '
            'AND created_at > now() - make_interval(days => %s)',
            (query_hash, ITINERARY_CACHE_TTL_DAYS),
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        return json.loads(row[0])['days']
    except Exception:
        app.logger.exception('itinerary cache read failed; treating as a miss')
        return None
    finally:
        if conn:
            conn.close()


def _store_itinerary(query_hash, days):
    """Save validated days. Best-effort: the plan is already built, so a
    database blip must not turn a working itinerary into a 500."""
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO itinerary_cache (query_hash, payload) VALUES (%s, %s) '
            'ON CONFLICT (query_hash) DO UPDATE SET '
            'payload = EXCLUDED.payload, created_at = now()',
            (query_hash, json.dumps({'days': days})),
        )
        cur.execute(
            'DELETE FROM itinerary_cache WHERE created_at < now() - make_interval(days => %s)',
            (ITINERARY_CACHE_PRUNE_DAYS,),
        )
        cur.close()
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('itinerary cache write failed')
    finally:
        if conn:
            conn.close()


def _itinerary_slot(value):
    """One morning/afternoon/evening cell: non-empty text, length-capped."""
    text = ('' if value is None else str(value)).strip()
    if not text:
        raise _ItineraryError('empty itinerary slot')
    return text[:ITINERARY_MAX_SLOT_CHARS]


def _itinerary_cost(value):
    """A day's estimated spend in rupees. Gemini sometimes writes "₹1,200"
    even when asked for a plain number, so anything digit-like is accepted."""
    if isinstance(value, bool):
        raise _ItineraryError('non-numeric day cost')
    if isinstance(value, (int, float)):
        cost = int(value)
    else:
        digits = re.sub(r'[^0-9]', '', str(value or ''))
        if not digits:
            raise _ItineraryError('non-numeric day cost')
        cost = int(digits)
    if cost < 0 or cost > ITINERARY_MAX_DAY_COST:
        raise _ItineraryError('day cost out of range')
    return cost


def _parse_itinerary(text, expected_days):
    """Turn Gemini's JSON reply into the validated day list.

    Strict on purpose: half-rendered cards are worse than a retry. Extra days
    are trimmed (models like to add a bonus day); an unparseable body, a
    missing day or an empty slot raises _ItineraryError, which ai_guide() maps to
    502 without echoing the model's text.
    """
    cleaned = text.strip()
    if cleaned.startswith('```'):
        # Tolerate a markdown fence even though responseMimeType should
        # prevent it.
        cleaned = cleaned.split('\n', 1)[1] if '\n' in cleaned else ''
        if cleaned.rstrip().endswith('```'):
            cleaned = cleaned.rstrip()[:-3].rstrip()

    try:
        data = json.loads(cleaned)
    except ValueError as exc:
        raise _ItineraryError('itinerary was not JSON') from exc

    raw_days = data.get('days') if isinstance(data, dict) else None
    if not isinstance(raw_days, list) or not raw_days:
        raise _ItineraryError('itinerary had no days')
    if len(raw_days) < expected_days:
        raise _ItineraryError('itinerary was shorter than requested')

    days = []
    for index, entry in enumerate(raw_days[:expected_days], start=1):
        if not isinstance(entry, dict):
            raise _ItineraryError('day was not an object')
        days.append({
            'day': index,
            'morning': _itinerary_slot(entry.get('morning')),
            'afternoon': _itinerary_slot(entry.get('afternoon')),
            'evening': _itinerary_slot(entry.get('evening')),
            'cost': _itinerary_cost(entry.get('cost')),
        })
    return days


def _itinerary_prompt(plan):
    return (
        'You are Manzil Bharat, a travel planner for India. Create a day-wise '
        f"itinerary for {plan['destination']} covering {plan['days']} day(s) on a "
        f"{plan['budget']} budget, focused on {plan['interests']}. "
        'Respond with ONLY a JSON object, no markdown and no commentary, shaped '
        'exactly like: {"days": [{"day": 1, "morning": "...", "afternoon": '
        '"...", "evening": "...", "cost": 1200}]}. '
        f"Give exactly {plan['days']} entries numbered 1 to {plan['days']}. "
        'morning, afternoon and evening must each name one concrete activity '
        "or place in 12 words or fewer. cost is that day's estimated spend for "
        'one traveller in Indian rupees, as a plain number.'
    )


def _itinerary_guide(data):
    plan, error = _itinerary_request(data)
    if error:
        return jsonify({'error': error[0]}), error[1]

    query_hash = _itinerary_cache_key(plan)
    cached = _cached_itinerary(query_hash)
    if cached is not None:
        return jsonify({
            'days': cached,
            'budget': _budget_estimate(plan, cached),
            'cached': True,
        }), 200

    # The cache is checked first so a warm itinerary still works if the key is
    # later removed; everything else needs the key.
    if not GEMINI_API_KEY:
        return jsonify({'error': 'AI is not configured on this server. Set GEMINI_API_KEY in .env.'}), 503

    text, error = _gemini_text(_itinerary_prompt(plan), json_mode=True)
    if error:
        return jsonify({'error': error[0]}), error[1]

    try:
        days = _parse_itinerary(text, plan['days'])
    except _ItineraryError as exc:
        app.logger.warning('Gemini itinerary was invalid (%s): %s', exc, text[:300])
        return jsonify({'error': 'The AI returned an unusable itinerary. Please try again.'}), 502

    _store_itinerary(query_hash, days)
    return jsonify({
        'days': days,
        'budget': _budget_estimate(plan, days),
        'cached': False,
    }), 200


@app.route('/api/ai-guide', methods=['POST'])
@rate_limit(max_requests=15, window=60)
def ai_guide():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    kind = str(data.get('kind') or '').strip()
    if kind == 'itinerary':
        return _itinerary_guide(data)

    query = str(data.get('query') or '').strip()

    if kind not in AI_PROMPTS:
        return jsonify({'error': 'Unknown guide type'}), 400
    if not query:
        return jsonify({'error': 'A destination or query is required'}), 400
    if len(query) > 300:
        return jsonify({'error': 'Query is too long (max 300 characters)'}), 400
    if not GEMINI_API_KEY:
        return jsonify({'error': 'AI is not configured on this server. Set GEMINI_API_KEY in .env.'}), 503

    text, error = _gemini_text(AI_PROMPTS[kind].replace('{query}', query))
    if error:
        return jsonify({'error': error[0]}), error[1]
    return jsonify({'text': text}), 200


@app.route('/api/admin/login', methods=['POST'])
@rate_limit(max_requests=5, window=60)
def admin_login():
    data = request.get_json()
    email = (data.get('email') or '').strip()
    password = data.get('password') or ''

    if email != ADMIN_EMAIL:
        # Spend the same bcrypt work as a real check so response timing does
        # not reveal whether the submitted email is the admin's.
        bcrypt.checkpw(password.encode('utf-8'), _DUMMY_ADMIN_HASH)
        app.logger.warning('admin login failed for %s', email)
        return jsonify({'error': 'Invalid admin credentials'}), 401

    if not bcrypt.checkpw(password.encode('utf-8'), ADMIN_PASSWORD_HASH.encode('utf-8')):
        app.logger.warning('admin login failed for %s (wrong password)', email)
        return jsonify({'error': 'Invalid admin credentials'}), 401

    token = create_token(0, email)
    app.logger.info('admin login: %s signed in', email)
    return jsonify({'token': token, 'message': 'Admin login successful'}), 200


def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get('x-admin-token', '')
        payload = verify_token(token)
        if not payload:
            return jsonify({'error': 'Unauthorized'}), 401
        if payload.get('user_id') != 0 or payload.get('email') != ADMIN_EMAIL:
            return jsonify({'error': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return wrapper


@app.route('/api/stats', methods=['GET'])
@require_admin
def get_stats():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM bookings")
        total_bookings = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM payments")
        total_payments = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'completed'")
        total_revenue = cur.fetchone()[0]
        cur.close()
        return jsonify({
            'totalUsers': total_users,
            'totalBookings': total_bookings,
            'totalPayments': total_payments,
            'totalRevenue': float(total_revenue)
        })
    except Exception:
        # Surface the failure instead of returning zeros: a DB outage rendered
        # as "0 users, ₹0 revenue" is silently wrong data.
        return jsonify({'error': 'Could not load stats'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/recent', methods=['GET'])
@require_admin
def get_recent():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT b.*, u.name as user_name FROM bookings b
                       LEFT JOIN users u ON b.user_id = u.id
                       ORDER BY b.created_at DESC LIMIT 5""")
        bookings = cur.fetchall()
        cur.execute("""SELECT p.*, u.name as user_name FROM payments p
                       LEFT JOIN users u ON p.user_id = u.id
                       ORDER BY p.created_at DESC LIMIT 5""")
        payments = cur.fetchall()
        cur.execute("SELECT * FROM users ORDER BY id DESC LIMIT 5")
        users = cur.fetchall()
        cur.close()
        return jsonify({
            'bookings': [dict(b) for b in bookings],
            'payments': [dict(p) for p in payments],
            'users': [dict(u) for u in users]
        })
    except Exception:
        return jsonify({'error': 'Could not load recent activity'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/analytics', methods=['GET'])
@require_admin
def get_analytics():
    """The page-view counter, read back for the admin panel (Phase 5 step 5).

    Only paths and days - the counter never stored a visitor identifier, a
    cookie or a query string, so there is nothing personal in here.
    """
    days = request.args.get('days', type=int) or 14
    if days < 1 or days > 90:
        days = 14
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT day::text AS day, SUM(count)::int AS views FROM page_views "
            "WHERE day >= CURRENT_DATE - %s "
            "GROUP BY day ORDER BY day",
            (days - 1,),
        )
        by_day = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT path, SUM(count)::int AS views FROM page_views "
            "WHERE day >= CURRENT_DATE - %s "
            "GROUP BY path ORDER BY views DESC, path LIMIT 10",
            (days - 1,),
        )
        top_paths = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT COALESCE(SUM(count), 0)::int AS total FROM page_views")
        total_views = cur.fetchone()['total']
        cur.close()
        return jsonify({
            'days': days,
            'total_views': total_views,
            'by_day': by_day,
            'top_paths': top_paths,
        }), 200
    except psycopg2.errors.UndefinedTable:
        if conn:
            conn.rollback()
        return jsonify({
            'days': days,
            'total_views': 0,
            'by_day': [],
            'top_paths': [],
            'error': 'The page_views table is missing - run migrations/phase5.sql',
        }), 200
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('get_analytics failed')
        return jsonify({'error': 'Could not load the traffic report'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/users', methods=['GET'])
@require_admin
def get_users():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, name, email, contact as phone, created_at FROM users ORDER BY id DESC")
        users = cur.fetchall()
        cur.close()
        return jsonify([dict(u) for u in users])
    except Exception:
        return jsonify({'error': 'Could not load users'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/users/<int:user_id>', methods=['GET'])
@require_admin
def get_user(user_id):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, name, email, contact as phone, gender, dob, marital_status, residential_address, pincode, created_at FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        cur.close()
        if not user:
            return jsonify({'error': 'User not found'}), 404
        return jsonify(dict(user))
    except Exception:
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@require_admin
def delete_user(user_id):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        if cur.rowcount == 0:
            conn.rollback()
            cur.close()
            return jsonify({'error': 'User not found'}), 404
        conn.commit()
        cur.close()
        app.logger.info('admin: deleted user %s', user_id)
        return jsonify({'message': 'User deleted'})
    except psycopg2.errors.ForeignKeyViolation:
        if conn:
            conn.rollback()
        return jsonify({
            'error': 'This user still has bookings or payments. '
                     'Apply the ON DELETE CASCADE migration in schema.sql, '
                     'then retry.'
        }), 409
    except Exception:
        if conn:
            conn.rollback()
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/bookings', methods=['GET'])
@require_admin
def admin_get_bookings():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT b.*, u.name as user_name, u.email as user_email
                       FROM bookings b LEFT JOIN users u ON b.user_id = u.id
                       ORDER BY b.created_at DESC""")
        bookings = cur.fetchall()
        cur.close()
        return jsonify([dict(b) for b in bookings])
    except Exception:
        return jsonify({'error': 'Could not load bookings'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/bookings/<int:booking_id>', methods=['GET'])
@require_admin
def admin_get_booking(booking_id):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT b.*, u.name as user_name, u.email as user_email
                       FROM bookings b LEFT JOIN users u ON b.user_id = u.id
                       WHERE b.id = %s""", (booking_id,))
        booking = cur.fetchone()
        cur.close()
        if not booking:
            return jsonify({'error': 'Booking not found'}), 404
        return jsonify(dict(booking))
    except Exception:
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/bookings/<int:booking_id>', methods=['PUT'])
@require_admin
def admin_update_booking(booking_id):
    data = request.get_json()
    status = (data.get('status') or '').strip().lower()
    valid_statuses = ['pending', 'confirmed', 'completed', 'cancelled']
    if status not in valid_statuses:
        return jsonify({'error': 'Invalid status'}), 400

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE bookings SET status = %s WHERE id = %s", (status, booking_id))
        if cur.rowcount == 0:
            conn.rollback()
            cur.close()
            return jsonify({'error': 'Booking not found'}), 404
        conn.commit()
        cur.close()
        app.logger.info('admin: booking %s set to %s', booking_id, status)
        return jsonify({'message': 'Booking updated'})
    except Exception:
        if conn:
            conn.rollback()
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/bookings/<int:booking_id>', methods=['DELETE'])
@require_admin
def admin_delete_booking(booking_id):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM bookings WHERE id = %s", (booking_id,))
        if cur.rowcount == 0:
            conn.rollback()
            cur.close()
            return jsonify({'error': 'Booking not found'}), 404
        conn.commit()
        cur.close()
        app.logger.info('admin: deleted booking %s', booking_id)
        return jsonify({'message': 'Booking deleted'})
    except Exception:
        if conn:
            conn.rollback()
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/payments', methods=['GET'])
@require_admin
def admin_get_payments():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT p.*, u.name as user_name, u.email as user_email
                       FROM payments p LEFT JOIN users u ON p.user_id = u.id
                       ORDER BY p.created_at DESC""")
        payments = cur.fetchall()
        cur.close()
        return jsonify([dict(p) for p in payments])
    except Exception:
        return jsonify({'error': 'Could not load payments'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/payments/<int:payment_id>', methods=['GET'])
@require_admin
def admin_get_payment(payment_id):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT p.*, u.name as user_name, u.email as user_email
                       FROM payments p LEFT JOIN users u ON p.user_id = u.id
                       WHERE p.id = %s""", (payment_id,))
        payment = cur.fetchone()
        cur.close()
        if not payment:
            return jsonify({'error': 'Payment not found'}), 404
        return jsonify(dict(payment))
    except Exception:
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/admin/messages', methods=['GET'])
@require_admin
def admin_get_messages():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM messages ORDER BY created_at DESC LIMIT 500")
        messages = cur.fetchall()
        cur.close()
        return jsonify([dict(m) for m in messages]), 200
    except Exception:
        app.logger.exception('admin_get_messages failed')
        return jsonify({'error': 'Could not load messages'}), 500
    finally:
        if conn:
            conn.close()


if __name__ == '__main__':
    app.run(port=int(os.getenv('PORT', '5000')))
