"""Saved itineraries: POST/GET/DELETE /api/me/trips (Phase 4 step 6).

A saved trip is the whole plan the visitor built on trip.html. It belongs to
one user, is deleted with the account, and can be removed by its owner.
"""
import os
import re

import backend.app as app_module

MIGRATION_PATH = os.path.join(app_module.PROJECT_ROOT, 'migrations', 'phase4.sql')
SCHEMA_PATH = os.path.join(app_module.PROJECT_ROOT, 'schema.sql')


def table_columns(sql, table):
    """The column names of a CREATE TABLE block in a SQL file."""
    match = re.search(
        r'CREATE TABLE IF NOT EXISTS ' + table + r'\s*\((.*?)\n\);',
        sql,
        re.DOTALL,
    )
    assert match, f'{table} is not created in the file'
    columns = []
    for line in match.group(1).splitlines():
        line = line.strip().rstrip(',')
        if not line:
            continue
        first = line.split()[0].upper()
        if first in ('PRIMARY', 'UNIQUE', 'FOREIGN', 'CONSTRAINT', 'CHECK'):
            continue
        columns.append(line.split()[0])
    return columns


def payload(destination='Jaipur', days=3, budget='mid-range'):
    """A saved plan shaped like the one trip.html sends."""
    return {
        'plan': {
            'kind': 'itinerary',
            'destination': destination,
            'days': days,
            'budget': budget,
            'interests': 'forts',
        },
        'itinerary': {
            'destination': destination,
            'days': [{'day': n, 'morning': 'a', 'afternoon': 'b', 'evening': 'c',
                      'cost': 100} for n in range(1, days + 1)],
            'budget': budget,
            'estimate': {'trip_total': 300, 'per_day': 100, 'level': budget},
            'savedAt': '2026-01-01T00:00:00Z',
        },
    }


def body(title='Jaipur · 3 days · mid-range', plan=None):
    return {'title': title, 'payload': plan if plan is not None else payload()}


# --------------------------------------------------------------------------
# POST /api/me/trips
# --------------------------------------------------------------------------

def test_saving_without_a_token_is_refused(client):
    assert client.post('/api/me/trips', json=body()).status_code == 401


def test_saving_returns_201_and_the_trip(client, user, auth):
    _, token = user()
    response = client.post('/api/me/trips', json=body(), headers=auth(token))

    assert response.status_code == 201
    data = response.get_json()
    assert data['message'] == 'Itinerary saved'
    trip = data['trip']
    assert trip['title'] == 'Jaipur · 3 days · mid-range'
    assert trip['id'] > 0
    # The plan comes back as an object, not as the JSON text it is stored as.
    assert trip['payload']['itinerary']['destination'] == 'Jaipur'


def test_saved_trip_is_written_to_the_database(client, user, auth, sql):
    _, token = user()
    client.post('/api/me/trips', json=body(), headers=auth(token))

    rows = sql('SELECT user_id, title, payload FROM saved_trips')
    assert len(rows) == 1
    assert rows[0][0] > 0
    assert rows[0][1] == 'Jaipur · 3 days · mid-range'


def test_validation_rejects_a_bad_request_and_writes_nothing(client, user, auth, sql):
    _, token = user()

    cases = [
        ({'payload': payload()}, 'no title'),
        ({'title': '', 'payload': payload()}, 'empty title'),
        ({'title': 'x' * 256, 'payload': payload()}, 'title too long'),
        ({'title': 'ok'}, 'no payload'),
        ({'title': 'ok', 'payload': None}, 'null payload'),
        ({'title': 'ok', 'payload': 'not an object'}, 'payload is a string'),
        ({'title': 'ok', 'payload': [1, 2, 3]}, 'payload is a list'),
        ({'title': 'ok', 'payload': {'blob': 'x' * 300000}}, 'payload too large'),
    ]
    for request_body, why in cases:
        response = client.post('/api/me/trips', json=request_body, headers=auth(token))
        assert response.status_code == 400, why
        assert sql('SELECT id FROM saved_trips') == [], why


def test_saving_the_same_plan_twice_does_not_duplicate_it(client, user, auth, sql):
    _, token = user()

    first = client.post('/api/me/trips', json=body(), headers=auth(token))
    second = client.post('/api/me/trips', json=body(), headers=auth(token))

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.get_json()['trip']['id'] == first.get_json()['trip']['id']
    assert len(sql('SELECT id FROM saved_trips')) == 1


def test_saving_a_different_plan_keeps_both(client, user, auth, sql):
    _, token = user()

    client.post('/api/me/trips', json=body(), headers=auth(token))
    client.post('/api/me/trips', json=body('Goa · 2 days · budget'),
                headers=auth(token))

    assert len(sql('SELECT id FROM saved_trips')) == 2


def test_an_admin_token_cannot_save_a_trip(client, admin_credentials):
    """Admin sessions carry user_id 0 and are refused by require_user."""
    email, password = admin_credentials
    login = client.post('/api/admin/login',
                        json={'email': email, 'password': password})
    token = login.get_json()['token']

    response = client.post('/api/me/trips', json=body(),
                            headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 403


# --------------------------------------------------------------------------
# GET /api/me/trips
# --------------------------------------------------------------------------

def test_listing_without_a_token_is_refused(client):
    assert client.get('/api/me/trips').status_code == 401


def test_listing_returns_the_users_saved_trips(client, user, auth):
    _, token = user()
    client.post('/api/me/trips', json=body(), headers=auth(token))

    response = client.get('/api/me/trips', headers=auth(token))

    assert response.status_code == 200
    saved = response.get_json()
    assert len(saved) == 1
    assert saved[0]['title'] == 'Jaipur · 3 days · mid-range'
    assert saved[0]['payload']['plan']['destination'] == 'Jaipur'
    assert saved[0]['created_at']


def test_listing_only_returns_your_own_trips(client, user, auth, sql):
    _, token_a = user(email='a@example.com')
    _, token_b = user(email='b@example.com')
    client.post('/api/me/trips', json=body(), headers=auth(token_a))

    assert len(client.get('/api/me/trips', headers=auth(token_a)).get_json()) == 1
    assert client.get('/api/me/trips', headers=auth(token_b)).get_json() == []
    assert len(sql('SELECT id FROM saved_trips')) == 1


def test_listing_is_newest_first(client, user, auth):
    _, token = user()
    client.post('/api/me/trips', json=body('First'), headers=auth(token))
    client.post('/api/me/trips', json=body('Second'), headers=auth(token))

    titles = [t['title'] for t in client.get('/api/me/trips', headers=auth(token)).get_json()]
    assert titles == ['Second', 'First']


# --------------------------------------------------------------------------
# DELETE /api/me/trips/<id>
# --------------------------------------------------------------------------

def test_removing_your_own_saved_trip(client, user, auth, sql):
    _, token = user()
    created = client.post('/api/me/trips', json=body(), headers=auth(token))
    trip_id = created.get_json()['trip']['id']

    response = client.delete(f'/api/me/trips/{trip_id}', headers=auth(token))

    assert response.status_code == 200
    assert response.get_json()['trip']['id'] == trip_id
    assert sql('SELECT id FROM saved_trips') == []


def test_removing_a_trip_that_is_not_yours_is_forbidden(client, user, auth, sql):
    _, token_a = user(email='a@example.com')
    _, token_b = user(email='b@example.com')
    created = client.post('/api/me/trips', json=body(), headers=auth(token_a))
    trip_id = created.get_json()['trip']['id']

    response = client.delete(f'/api/me/trips/{trip_id}', headers=auth(token_b))

    assert response.status_code == 403
    # The other user's plan is still there.
    assert len(sql('SELECT id FROM saved_trips')) == 1


def test_removing_a_missing_trip_is_404(client, user, auth):
    _, token = user()
    assert client.delete('/api/me/trips/999999', headers=auth(token)).status_code == 404


def test_removing_without_a_token_is_refused(client, user, auth):
    _, token = user()
    created = client.post('/api/me/trips', json=body(), headers=auth(token))
    trip_id = created.get_json()['trip']['id']

    assert client.delete(f'/api/me/trips/{trip_id}').status_code == 401


def test_saved_trips_go_away_with_the_account(client, user, auth, sql):
    """ON DELETE CASCADE: the admin's "delete user" takes the plans with it."""
    _, token = user()
    client.post('/api/me/trips', json=body(), headers=auth(token))
    user_id = sql('SELECT id FROM users')[0][0]

    sql('DELETE FROM users WHERE id = %s', (user_id,))

    assert sql('SELECT id FROM saved_trips') == []


def test_the_save_rate_limit_answers_429(client, user, auth):
    _, token = user()
    statuses = []
    for i in range(31):
        response = client.post('/api/me/trips', json=body(f'Plan {i}'),
                                headers=auth(token))
        statuses.append(response.status_code)
        # Remove it again, so the per-user plan cap is not what stops us -
        # this test is about the rate limiter.
        if response.status_code == 201:
            trip_id = response.get_json()['trip']['id']
            client.delete(f'/api/me/trips/{trip_id}', headers=auth(token))

    assert statuses[:30] == [201] * 30
    assert statuses[30] == 429


def test_the_saved_trip_limit_is_enforced(client, user, auth, sql):
    """A user cannot fill the table with plans."""
    _, token = user()
    for i in range(app_module.SAVED_TRIP_MAX_PER_USER):
        response = client.post('/api/me/trips', json=body(f'Plan {i}'),
                                headers=auth(token))
        assert response.status_code == 201

    response = client.post('/api/me/trips', json=body('One too many'),
                            headers=auth(token))

    assert response.status_code == 409
    assert len(sql('SELECT id FROM saved_trips')) == app_module.SAVED_TRIP_MAX_PER_USER


# --------------------------------------------------------------------------
# The schema and the migration must not drift apart
# --------------------------------------------------------------------------

def test_the_migration_file_creates_the_same_tables_as_the_schema():
    """migrations/phase4.sql is what the live database gets; schema.sql is what
    a fresh database gets. A column added to one and not the other would mean
    a fresh install and a migrated install behave differently."""
    with open(SCHEMA_PATH, encoding='utf-8') as handle:
        schema = handle.read()
    with open(MIGRATION_PATH, encoding='utf-8') as handle:
        migration = handle.read()

    for table in ('saved_trips', 'geocode_cache'):
        assert table_columns(schema, table) == table_columns(migration, table), table


def test_the_migration_file_is_safe_to_run_twice():
    """Every statement in the migration is idempotent."""
    with open(MIGRATION_PATH, encoding='utf-8') as handle:
        migration = handle.read()

    creates = re.findall(r'CREATE (TABLE|INDEX)(?!.*IF NOT EXISTS)', migration)
    assert creates == []
    # The GRANTs are wrapped in a psql conditional, so the file also works
    # when the app role is not known.
    assert r'\if :{?app_role}' in migration
