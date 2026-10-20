import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import smtplib
import time
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from functools import wraps
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

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)

app = Flask(__name__, static_folder=PROJECT_ROOT, static_url_path='')

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


@app.route('/')
def index():
    return send_from_directory(PROJECT_ROOT, 'index.html')


@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
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

    if errors:
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
        cur.close()
        conn.commit()
        token = create_token(user[0], user[2])
        return jsonify({'message': 'Account created successfully', 'user': {'id': user[0], 'name': user[1], 'email': user[2], 'contact': user[3]}, 'token': token}), 201
    except psycopg2.IntegrityError:
        if conn:
            conn.rollback()
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
            return jsonify({'error': 'Invalid email or password'}), 401

        token = create_token(user[0], user[2])
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
            conn.commit()
            token = create_token(new_user[0], new_user[2])
            cur.close()
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


@app.route('/api/ai-guide', methods=['POST'])
@rate_limit(max_requests=15, window=60)
def ai_guide():
    data = request.get_json(silent=True) or {}
    kind = (data.get('kind') or '').strip()
    query = (data.get('query') or '').strip()

    if kind not in AI_PROMPTS:
        return jsonify({'error': 'Unknown guide type'}), 400
    if not query:
        return jsonify({'error': 'A destination or query is required'}), 400
    if len(query) > 300:
        return jsonify({'error': 'Query is too long (max 300 characters)'}), 400
    if not GEMINI_API_KEY:
        return jsonify({'error': 'AI is not configured on this server. Set GEMINI_API_KEY in .env.'}), 503

    prompt = AI_PROMPTS[kind].replace('{query}', query)

    try:
        resp = requests.post(
            'https://generativelanguage.googleapis.com/v1beta/models/'
            + GEMINI_MODEL + ':generateContent',
            params={'key': GEMINI_API_KEY},
            json={'contents': [{'parts': [{'text': prompt}]}]},
            timeout=30,
        )
    except requests.exceptions.Timeout:
        return jsonify({'error': 'The AI service timed out. Please try again.'}), 504
    except requests.exceptions.RequestException:
        return jsonify({'error': 'Could not reach the AI service. Please try again later.'}), 502

    if resp.status_code == 429:
        return jsonify({'error': 'The AI service is rate-limited right now. Please try again in a minute.'}), 429
    if resp.status_code != 200:
        # Log server-side for debugging, but never forward Google's body to
        # the client (it can echo request details).
        app.logger.warning('Gemini returned HTTP %s: %s', resp.status_code, resp.text[:300])
        return jsonify({'error': 'The AI service returned an error. Please try again later.'}), 502

    try:
        body = resp.json()
        parts = body['candidates'][0]['content']['parts']
        text = ''.join(p.get('text') or '' for p in parts).strip()
    except (ValueError, KeyError, IndexError, TypeError):
        app.logger.warning('Gemini response had unexpected shape: %s', resp.text[:300])
        return jsonify({'error': 'The AI service returned an unexpected response.'}), 502

    if not text:
        return jsonify({'error': 'The AI service returned an empty response. Please try again.'}), 502
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
        return jsonify({'error': 'Invalid admin credentials'}), 401

    if not bcrypt.checkpw(password.encode('utf-8'), ADMIN_PASSWORD_HASH.encode('utf-8')):
        return jsonify({'error': 'Invalid admin credentials'}), 401

    token = create_token(0, email)
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
