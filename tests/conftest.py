"""Shared pytest fixtures for the backend test suite.

Isolation is the point of this file:

* the app is pointed at a SEPARATE database - ``TEST_DATABASE_URL``, or
  locally ``DB_NAME`` + ``"_test"`` - so a test run can never write to the
  development database. A guard refuses to start if the resolved database
  name does not contain "test".
* ``schema.sql`` is applied once per session (it is all ``IF NOT EXISTS``).
* every test starts with an EMPTY database (``TRUNCATE ... RESTART IDENTITY
  CASCADE``), which also clears the DB-backed rate limiter and reset tokens,
  so one test's login attempts cannot push another test into 429.
* the settings the app refuses to boot without (``JWT_SECRET``,
  ``ADMIN_EMAIL``, ``ADMIN_PASSWORD``) get deterministic values here, and
  email is switched off so no test can send mail.

Creating the test database is a one-off, and it needs a role with CREATEDB
(the app's own user usually has neither):

    sudo -u postgres createdb -O manzil_user manzil_bharat_test

CI gets the same thing from a service container - see .github/workflows/ci.yml.
"""
from __future__ import annotations

import os
import pathlib
import sys
from datetime import date, timedelta
from urllib.parse import quote

import pytest
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# .env carries the local Postgres credentials. Read it before backend.app is
# imported: app.py resolves its settings at import time.
load_dotenv(ROOT / '.env')

# Values used by the tests themselves. ADMIN_* are also exported below, so a
# test can log in as the admin without knowing anything about the real .env.
ADMIN_EMAIL = 'admin@manzil.test'
ADMIN_PASSWORD = 'admin-test-password'   # the app requires >= 12 characters
DEFAULT_PASSWORD = 'Str0ng!Pass1'


def _local_test_dsn():
    """Build a DSN for <DB_NAME>_test from the DB_* variables in .env."""
    keys = ('DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD')
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            'Cannot work out the test database: ' + ', '.join(missing)
            + ' are not set. Either fix .env or set TEST_DATABASE_URL.'
        )
    return 'postgresql://{}:{}@{}:{}/{}'.format(
        quote(os.environ['DB_USER'], safe=''),
        quote(os.environ['DB_PASSWORD'], safe=''),
        os.environ['DB_HOST'],
        os.environ['DB_PORT'],
        os.environ['DB_NAME'] + '_test',
    )


def _test_dsn():
    dsn = (os.getenv('TEST_DATABASE_URL') or '').strip() or _local_test_dsn()
    # Trailing query (sslmode=...) is not part of the database name.
    name = dsn.rsplit('/', 1)[-1].split('?')[0]
    if 'test' not in name.lower():
        raise RuntimeError(
            f'Refusing to run the suite against {name!r}: the database name '
            'must contain "test", because every test TRUNCATEs the tables.'
        )
    return dsn


TEST_DSN = _test_dsn()

# Whatever the shell or .env says, these are what the app under test sees.
os.environ['DATABASE_URL'] = TEST_DSN
os.environ['JWT_SECRET'] = 'test-jwt-secret-' + 'a' * 32
os.environ['ADMIN_EMAIL'] = ADMIN_EMAIL
os.environ['ADMIN_PASSWORD'] = ADMIN_PASSWORD
# '' and 'disabled' both mean "no provider"; anything outside
# smtp/resend/brevo is treated as not configured by app.email_provider().
os.environ['EMAIL_PROVIDER'] = 'disabled'
# MUST be present-but-empty, not popped: app.py calls load_dotenv() at import
# and python-dotenv only fills variables that are ABSENT, so deleting the key
# here would let .env put a real one back and the suite would quietly spend
# the live Gemini quota.
os.environ['GEMINI_API_KEY'] = ''

import psycopg2  # noqa: E402  (import after the env above is settled)


def _connect():
    return psycopg2.connect(TEST_DSN)


def _truncate_all(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        tables = [row[0] for row in cur.fetchall()]
        if tables:
            cur.execute(
                'TRUNCATE TABLE ' + ', '.join(f'"{t}"' for t in tables)
                + ' RESTART IDENTITY CASCADE'
            )
    conn.commit()


@pytest.fixture(scope='session', autouse=True)
def _schema():
    """Create the tables once per session."""
    sql = (ROOT / 'schema.sql').read_text(encoding='utf-8')
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()
    yield


@pytest.fixture(autouse=True)
def _empty_database(_schema):
    """Empty every table before and after each test."""
    conn = _connect()
    try:
        _truncate_all(conn)
    finally:
        conn.close()
    yield
    conn = _connect()
    try:
        _truncate_all(conn)
    finally:
        conn.close()


@pytest.fixture(scope='session')
def flask_app():
    from backend.app import app

    app.config.update(TESTING=True)
    return app


@pytest.fixture()
def sql():
    """Run a statement against the test database.

    Some invariants are cheaper to check (or to set up) in SQL than through
    HTTP: 'was a row really inserted on validation failure', expiring a reset
    token, forcing a booking to 'completed'.
    """
    def _sql(statement, params=None):
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(statement, params)
                rows = cur.fetchall() if cur.description else None
            conn.commit()
            return rows
        finally:
            conn.close()

    return _sql


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


# --------------------------------------------------------------------------
# Helpers: register / login / admin
# --------------------------------------------------------------------------

# Every signup field with a valid value, so a test only passes what it means
# to exercise (register(weak_password='123') rather than nine fields).
# 'consent' is part of it: the API refuses an account without it (DPDP), so
# every test that registers a user has to give consent too.
SIGNUP_DEFAULTS = {
    'name': 'Test User',
    'email': 'test.user@example.com',
    'contact': '9876543210',
    'gender': 'Male',
    'dob': '1995-04-12',
    'marital_status': 'Single',
    'residential_address': '1 Test Road, Bengaluru',
    'pincode': '560001',
    'password': DEFAULT_PASSWORD,
    'consent': True,
    'consent_version': 'dpdp-v1',
}


@pytest.fixture()
def register(client):
    """POST /api/signup with defaults; returns the response.

    Returning the response (not a token) so a test can assert on the failure
    cases too: register(email='') -> 400.
    """
    def _register(**overrides):
        payload = {**SIGNUP_DEFAULTS, **overrides}
        return client.post('/api/signup', json=payload)

    return _register


@pytest.fixture()
def user(register):
    """Register a user and return (email, token); asserts the signup worked."""
    def _user(**overrides):
        payload = {**SIGNUP_DEFAULTS, **overrides}
        response = register(**overrides)
        assert response.status_code == 201, response.get_json()
        body = response.get_json()
        return payload['email'], body['token']

    return _user


@pytest.fixture()
def login(client):
    """Log in and return the auth token."""
    def _login(email, password=DEFAULT_PASSWORD):
        response = client.post('/api/login', json={'email': email, 'password': password})
        assert response.status_code == 200, response.get_json()
        return response.get_json()['token']

    return _login


@pytest.fixture()
def auth():
    """Build the Authorization header for a user token."""
    def _auth(token):
        return {'Authorization': f'Bearer {token}'}

    return _auth


@pytest.fixture()
def admin_headers(client):
    """Log in as the admin and return the x-admin-token header."""
    def _admin_headers():
        response = client.post('/api/admin/login',
                               json={'email': ADMIN_EMAIL, 'password': ADMIN_PASSWORD})
        assert response.status_code == 200, response.get_json()
        return {'x-admin-token': response.get_json()['token']}

    return _admin_headers


@pytest.fixture()
def password():
    """The password that register/user/login use when a test does not pass one."""
    return DEFAULT_PASSWORD


@pytest.fixture()
def admin_credentials():
    """(email, password) of the admin account, for the tests about rejection."""
    return ADMIN_EMAIL, ADMIN_PASSWORD


@pytest.fixture()
def booking_payload():
    """A valid POST /api/bookings body, so a test only overrides what it means.

    The travel date is in the future because the endpoint refuses the past.
    """
    def _payload(**overrides):
        return {
            'booking_type': 'cab',
            'source': 'Delhi',
            'destination': 'Jaipur',
            'travel_date': (date.today() + timedelta(days=30)).isoformat(),
            'amount': 2400,
            **overrides,
        }

    return _payload
