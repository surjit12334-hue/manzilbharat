"""Phase 3 step 1: prove the test rig works.

These assertions are about the HARNESS (right database, empty tables, a
client that can talk to the app), not about product behaviour - the product
suite lands in step 2. If this file is green, a failing assertion in another
file is about the product, not about the fixtures.
"""
import os

import psycopg2


def _query(sql):
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    finally:
        conn.close()


def test_connects_to_a_test_database():
    """The suite must never talk to the development database."""
    name = _query('SELECT current_database()')[0][0]
    assert name.endswith('_test'), name
    assert name != 'manzil_bharat'


def test_schema_tables_exist():
    tables = {row[0] for row in _query(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert {'users', 'bookings', 'payments', 'rate_limits',
            'reset_tokens'} <= tables, tables


def test_database_starts_empty():
    assert _query('SELECT count(*) FROM users')[0][0] == 0
    assert _query('SELECT count(*) FROM bookings')[0][0] == 0


def test_client_serves_health(client):
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.get_json() == {'status': 'ok', 'db': 'ok'}


def test_register_helper_writes_through_the_app(client, user, auth):
    """The request helpers really do hit the test database."""
    email, token = user()

    assert _query('SELECT count(*) FROM users')[0][0] == 1
    assert _query('SELECT email FROM users')[0][0] == email

    me = client.get('/api/me', headers=auth(token))
    assert me.status_code == 200
    assert me.get_json()['email'] == email


def test_previous_test_did_not_leak():
    """Runs after the test above: proof that each test starts clean.

    (pytest runs a file's tests in order; this is the second half of the
    TRUNCATE check, not a coincidence.)
    """
    assert _query('SELECT count(*) FROM users')[0][0] == 0
    assert _query('SELECT count(*) FROM rate_limits')[0][0] == 0


def test_private_files_are_refused(client):
    """The static root is the whole project, so this guard matters."""
    assert client.get('/.env').status_code == 404
    assert client.get('/backend/app.py').status_code == 404
    assert client.get('/schema.sql').status_code == 404
    assert client.get('/seed.sql').status_code == 404


def test_a_public_page_still_works(client):
    assert client.get('/index.html').status_code == 200
