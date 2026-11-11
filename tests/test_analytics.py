"""First-party page-view counting (Phase 5 step 5).

The counter is deliberately dull: one row per (path, day), no visitor
identifier, no cookies and no query string. The admin panel reads it back
through GET /api/analytics.
"""


def _views(sql):
    return {row[0]: row[1] for row in sql('SELECT path, count FROM page_views')}


def test_a_page_request_is_counted(client, sql):
    client.get('/index.html')

    assert _views(sql) == {'/index.html': 1}


def test_the_counter_adds_up(client, sql):
    client.get('/index.html')
    client.get('/index.html')
    client.get('/map.html')

    assert _views(sql) == {'/index.html': 2, '/map.html': 1}


def test_api_calls_are_not_counted(client, sql):
    client.get('/api/health')

    assert _views(sql) == {}


def test_static_assets_are_not_counted(client, sql):
    client.get('/auth.js')
    client.get('/icon.png')

    assert _views(sql) == {}


def test_a_missing_page_is_not_counted(client, sql):
    response = client.get('/no-such-page.html')

    assert response.status_code == 404
    assert _views(sql) == {}


def test_the_query_string_is_never_stored(client, sql):
    """The query string can carry a destination or an email - it is not
    personal data we want to keep."""
    client.get('/index.html?destination=Jaipur&email=someone@example.com')

    assert _views(sql) == {'/index.html': 1}


# --------------------------------------------------------------------------
# The admin endpoint
# --------------------------------------------------------------------------

def test_analytics_needs_an_admin_token(client):
    assert client.get('/api/analytics').status_code == 401


def test_a_user_token_is_not_an_admin_token(client, user):
    """A normal session token must not open the admin endpoint, even when it
    is put in the admin header."""
    _email, token = user()

    response = client.get('/api/analytics', headers={'x-admin-token': token})

    assert response.status_code == 403


def test_analytics_reports_the_counted_views(client, admin_headers, sql):
    headers = admin_headers()
    client.get('/index.html')
    client.get('/index.html')

    response = client.get('/api/analytics', headers=headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body['total_views'] >= 2
    assert body['by_day']
    assert body['by_day'][0]['day']
    paths = {row['path']: row['views'] for row in body['top_paths']}
    assert paths['/index.html'] >= 2


def test_analytics_is_empty_on_a_fresh_database(client, admin_headers):
    headers = admin_headers()

    body = client.get('/api/analytics', headers=headers).get_json()

    assert body['total_views'] == 0
    assert body['by_day'] == []
    assert body['top_paths'] == []


def test_the_day_range_is_bounded(client, admin_headers):
    """A visitor cannot ask for a 1000-day report and get one."""
    headers = admin_headers()

    body = client.get('/api/analytics?days=1000', headers=headers).get_json()

    assert body['days'] == 14
