"""GET /api/geocode - the server-side proxy for the public geocoders
(Phase 4 step 7).

Nothing here touches the network: requests.get is patched in every test that
needs an answer from Nominatim or Photon. What the tests actually pin down
is the part the roadmap cares about:

* a real User-Agent goes out to the provider, because a browser cannot set
  one and the OSM usage policy asks every client to identify itself;
* the same search is answered from the cache instead of hitting the shared
  community service a second time;
* the provider's own body is never forwarded to the browser;
* the outbound volume is capped.
"""
import backend.app as app_module


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


NOMINATIM_ROWS = [
    {
        'lat': '26.9124',
        'lon': '75.7873',
        'display_name': 'Hawa Mahal, Jaipur, Rajasthan, India',
        'name': 'Hawa Mahal',
        'type': 'attraction',
    },
    {
        'lat': '26.9124',
        'lon': '75.7873',
        'display_name': 'Jaipur, Rajasthan, India',
        'name': 'Jaipur',
        'type': 'city',
    },
]

PHOTON_FEATURES = {
    'features': [
        {
            'geometry': {'coordinates': [75.7873, 26.9124]},
            'properties': {
                'name': 'Hawa Mahal',
                'city': 'Jaipur',
                'state': 'Rajasthan',
                'country': 'India',
                'type': 'tourism',
            },
        }
    ]
}


def patch_get(monkeypatch, response=None, recorder=None):
    """Replace requests.get with a fake that records the call."""
    calls = recorder if recorder is not None else []

    def _get(url, params=None, headers=None, timeout=None):
        calls.append({'url': url, 'params': params, 'headers': headers or {}})
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(app_module.requests, 'get', _get)
    return calls


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def test_a_missing_query_is_400(client):
    assert client.get('/api/geocode').status_code == 400
    assert client.get('/api/geocode?q=').status_code == 400
    assert client.get('/api/geocode?q=%20%20').status_code == 400


def test_a_too_long_query_is_400(client):
    assert client.get('/api/geocode?q=' + 'x' * 121).status_code == 400


def test_an_unknown_provider_is_400(client):
    assert client.get('/api/geocode?q=jaipur&provider=google').status_code == 400


# --------------------------------------------------------------------------
# The User-Agent - the reason this endpoint exists
# --------------------------------------------------------------------------

def test_the_request_carries_a_real_user_agent(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResponse(payload=NOMINATIM_ROWS))

    client.get('/api/geocode?q=jaipur')

    assert len(calls) == 1
    agent = calls[0]['headers'].get('User-Agent', '')
    assert 'ManzilBharat' in agent
    # The OSM policy asks for a contact address, so the admin address is in it.
    assert '@' in agent


def test_the_user_agent_can_be_configured(client, monkeypatch):
    monkeypatch.setenv('GEOCODER_USER_AGENT', 'MyApp/2.0 (mailto:me@example.com)')
    calls = patch_get(monkeypatch, FakeResponse(payload=NOMINATIM_ROWS))

    client.get('/api/geocode?q=jaipur')

    assert calls[0]['headers']['User-Agent'] == 'MyApp/2.0 (mailto:me@example.com)'


def test_the_provider_is_nominatim_by_default(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResponse(payload=NOMINATIM_ROWS))

    client.get('/api/geocode?q=jaipur')

    assert 'nominatim.openstreetmap.org' in calls[0]['url']


def test_photon_is_used_when_asked_for(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResponse(payload=PHOTON_FEATURES))

    client.get('/api/geocode?q=jaipur&provider=photon')

    assert 'photon.komoot.io' in calls[0]['url']


# --------------------------------------------------------------------------
# Normalised answers
# --------------------------------------------------------------------------

def test_nominatim_answers_are_normalised(client, monkeypatch):
    patch_get(monkeypatch, FakeResponse(payload=NOMINATIM_ROWS))

    response = client.get('/api/geocode?q=hawa%20mahal')

    assert response.status_code == 200
    data = response.get_json()
    assert data['provider'] == 'nominatim'
    assert data['cached'] is False
    first = data['results'][0]
    assert first['label'] == 'Hawa Mahal'
    assert first['display'] == 'Hawa Mahal, Jaipur, Rajasthan, India'
    assert first['lat'] == 26.9124
    assert first['lon'] == 75.7873
    assert first['type'] == 'attraction'


def test_photon_answers_are_normalised(client, monkeypatch):
    patch_get(monkeypatch, FakeResponse(payload=PHOTON_FEATURES))

    response = client.get('/api/geocode?q=jaipur&provider=photon')

    assert response.status_code == 200
    first = response.get_json()['results'][0]
    assert first['label'] == 'Hawa Mahal'
    assert first['display'] == 'Hawa Mahal, Jaipur, Rajasthan, India'
    assert first['lat'] == 26.9124
    assert first['lon'] == 75.7873


def test_a_result_without_coordinates_is_dropped(client, monkeypatch):
    patch_get(monkeypatch, FakeResponse(payload=[
        {'lat': 'not-a-number', 'lon': '75.7', 'name': 'Broken'},
        NOMINATIM_ROWS[0],
    ]))

    results = client.get('/api/geocode?q=jaipur').get_json()['results']

    assert len(results) == 1
    assert results[0]['label'] == 'Hawa Mahal'


def test_the_limit_is_clamped(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResponse(payload=NOMINATIM_ROWS))

    client.get('/api/geocode?q=jaipur&limit=99')

    assert calls[0]['params']['limit'] == app_module.GEOCODE_MAX_RESULTS


# --------------------------------------------------------------------------
# The cache - the second search must not hit the provider again
# --------------------------------------------------------------------------

def test_the_second_search_is_served_from_the_cache(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResponse(payload=NOMINATIM_ROWS))

    first = client.get('/api/geocode?q=jaipur')
    second = client.get('/api/geocode?q=jaipur')

    assert first.get_json()['cached'] is False
    assert second.get_json()['cached'] is True
    assert second.get_json()['results'] == first.get_json()['results']
    # One upstream call, not two.
    assert len(calls) == 1


def test_the_cache_key_ignores_case_and_extra_spaces(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResponse(payload=NOMINATIM_ROWS))

    client.get('/api/geocode?q=Jaipur')
    client.get('/api/geocode?q=%20%20jaipur%20%20')

    assert len(calls) == 1


def test_a_different_query_is_not_served_from_the_cache(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResponse(payload=NOMINATIM_ROWS))

    client.get('/api/geocode?q=jaipur')
    client.get('/api/geocode?q=goa')

    assert len(calls) == 2


def test_the_cached_answer_is_stored_in_the_table(client, monkeypatch, sql):
    patch_get(monkeypatch, FakeResponse(payload=NOMINATIM_ROWS))

    client.get('/api/geocode?q=jaipur')

    rows = sql('SELECT provider, payload FROM geocode_cache')
    assert len(rows) == 1
    assert rows[0][0] == 'nominatim'
    assert 'Hawa Mahal' in rows[0][1]


def test_a_stale_row_is_refreshed(client, monkeypatch, sql):
    calls = patch_get(monkeypatch, FakeResponse(payload=NOMINATIM_ROWS))
    sql("INSERT INTO geocode_cache (query_key, provider, payload, created_at) "
        "VALUES ('stale', 'nominatim', '[]', now() - make_interval(days => 30))")

    # The stale row is under a key the endpoint will not look up, so it only
    # proves the prune path: old rows are removed, the answer still works.
    response = client.get('/api/geocode?q=jaipur')

    assert response.status_code == 200
    assert len(calls) == 1
    assert sql("SELECT count(*) FROM geocode_cache "
               "WHERE created_at < now() - make_interval(days => %s)",
               (app_module.GEOCODE_CACHE_PRUNE_DAYS,)) == [(0,)]


# --------------------------------------------------------------------------
# Upstream failures
# --------------------------------------------------------------------------

def test_an_upstream_error_is_502_and_the_body_is_not_forwarded(client, monkeypatch):
    patch_get(monkeypatch, FakeResponse(status_code=503, payload={'error': 'nope'}))

    response = client.get('/api/geocode?q=jaipur')

    assert response.status_code == 502
    assert 'nope' not in response.get_data(as_text=True)


def test_a_timeout_is_502(client, monkeypatch):
    patch_get(monkeypatch, app_module.requests.exceptions.Timeout())

    assert client.get('/api/geocode?q=jaipur').status_code == 502


def test_a_connection_failure_is_502(client, monkeypatch):
    patch_get(monkeypatch, app_module.requests.exceptions.ConnectionError())

    assert client.get('/api/geocode?q=jaipur').status_code == 502


def test_a_body_that_is_not_json_is_502(client, monkeypatch):
    patch_get(monkeypatch, FakeResponse(status_code=200, payload=None))

    assert client.get('/api/geocode?q=jaipur').status_code == 502


def test_a_stale_answer_is_served_when_the_provider_is_down(client, monkeypatch, sql):
    """A cached plan beats an error page."""
    patch_get(monkeypatch, FakeResponse(payload=NOMINATIM_ROWS))
    client.get('/api/geocode?q=jaipur')
    # Age the cached row past its TTL, then break the provider.
    sql('UPDATE geocode_cache SET created_at = now() - make_interval(days => 30)')
    patch_get(monkeypatch, app_module.requests.exceptions.Timeout())

    response = client.get('/api/geocode?q=jaipur')

    assert response.status_code == 200
    assert response.get_json()['cached'] is True
    assert response.get_json()['results'][0]['label'] == 'Hawa Mahal'


# --------------------------------------------------------------------------
# The outbound budget
# --------------------------------------------------------------------------

def test_the_outbound_budget_is_capped(client, monkeypatch):
    """After the budget is spent the endpoint stops asking the provider."""
    calls = patch_get(monkeypatch, FakeResponse(payload=NOMINATIM_ROWS))

    budget = app_module.GEOCODE_UPSTREAM_BUDGET_PER_MINUTE
    for i in range(budget):
        assert client.get(f'/api/geocode?q=city-{i}').status_code == 200

    response = client.get('/api/geocode?q=one-too-many')

    assert response.status_code == 429
    assert len(calls) == budget


def test_a_cached_search_does_not_spend_the_budget(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResponse(payload=NOMINATIM_ROWS))

    client.get('/api/geocode?q=jaipur')
    for _ in range(10):
        assert client.get('/api/geocode?q=jaipur').status_code == 200

    assert len(calls) == 1


def test_the_endpoint_rate_limit_answers_429(client, monkeypatch):
    patch_get(monkeypatch, FakeResponse(payload=NOMINATIM_ROWS))

    statuses = [client.get(f'/api/geocode?q=city-{i}').status_code for i in range(61)]

    assert statuses[:60] == [200] * 60
    assert statuses[60] == 429
