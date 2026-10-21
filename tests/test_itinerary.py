"""Day-wise itineraries: request validation, the Gemini JSON contract, and
itinerary_cache (hit, normalisation, staleness).

Nothing here touches the network. Gemini is faked where a reply is needed,
and the cache-hit tests blank the key on purpose: with no key the endpoint
answers 503 unless the plan came from the cache, which proves the cached path.
"""
import json

import pytest

import backend.app as app_module

VALID = {
    'kind': 'itinerary',
    'destination': 'Jaipur',
    'days': 3,
    'budget': 'mid-range',
    'interests': 'forts, street food',
}


class FakeResponse:
    def __init__(self, payload_text):
        self.status_code = 200
        self.text = payload_text

    def json(self):
        return {'candidates': [{'content': {'parts': [{'text': self.text}]}}]}


def fake_gemini(monkeypatch, text):
    """Make the endpoint think Gemini answered with ``text``."""
    monkeypatch.setattr(app_module, 'GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(app_module.requests, 'post', lambda *a, **k: FakeResponse(text))


def itinerary_text(days=3, **overrides):
    """A well-formed Gemini reply, one entry per day."""
    entries = [
        {
            'day': number,
            'morning': f'Morning {number}',
            'afternoon': f'Afternoon {number}',
            'evening': f'Evening {number}',
            'cost': 1000 * number,
            **overrides,
        }
        for number in range(1, days + 1)
    ]
    return json.dumps({'days': entries})


def test_destination_is_required(client):
    payload = dict(VALID)
    del payload['destination']

    response = client.post('/api/ai-guide', json=payload)

    assert response.status_code == 400
    assert 'destination' in response.get_json()['error']


@pytest.mark.parametrize('overrides, fragment', [
    ({'destination': 'x' * 121}, 'Destination is too long'),
    ({'days': None}, 'days must be'),
    ({'days': 0}, 'between 1 and 10'),
    ({'days': 11}, 'between 1 and 10'),
    ({'days': 'three'}, 'days must be'),
    ({'days': True}, 'days must be'),
    ({'days': 3.5}, 'days must be'),
    ({'budget': 'x' * 61}, 'Budget is too long'),
    ({'interests': 'x' * 201}, 'Interests are too long'),
])
def test_bad_requests_are_rejected_before_the_key_check(client, overrides, fragment):
    response = client.post('/api/ai-guide', json={**VALID, **overrides})

    assert response.status_code == 400
    assert fragment in response.get_json()['error']


def test_a_non_object_body_is_not_a_crash(client):
    response = client.post('/api/ai-guide', json=['itinerary'])

    assert response.status_code == 400


def test_without_a_key_a_new_itinerary_is_unavailable(client):
    response = client.post('/api/ai-guide', json=VALID)

    assert response.status_code == 503
    assert 'not configured' in response.get_json()['error']


def test_generated_itinerary_is_returned_and_cached(client, monkeypatch, sql):
    fake_gemini(monkeypatch, itinerary_text(3))

    response = client.post('/api/ai-guide', json=VALID)

    assert response.status_code == 200
    body = response.get_json()
    assert body['cached'] is False
    assert body['days'] == [
        {
            'day': number,
            'morning': f'Morning {number}',
            'afternoon': f'Afternoon {number}',
            'evening': f'Evening {number}',
            'cost': 1000 * number,
        }
        for number in range(1, 4)
    ]
    rows = sql('SELECT payload FROM itinerary_cache')
    assert len(rows) == 1
    assert json.loads(rows[0][0])['days'] == body['days']


def test_gemini_is_asked_for_json_with_the_trip_details(client, monkeypatch):
    sent = {}

    def post(*args, **kwargs):
        sent.update(kwargs)
        return FakeResponse(itinerary_text(1))

    monkeypatch.setattr(app_module, 'GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(app_module.requests, 'post', post)

    assert client.post('/api/ai-guide', json={**VALID, 'days': 1}).status_code == 200

    assert sent['json']['generationConfig'] == {'responseMimeType': 'application/json'}
    prompt = sent['json']['contents'][0]['parts'][0]['text']
    assert 'Jaipur' in prompt
    assert '1 day' in prompt
    assert 'mid-range' in prompt
    assert 'forts, street food' in prompt


def test_a_repeat_request_is_served_from_cache_without_calling_gemini(client, monkeypatch):
    fake_gemini(monkeypatch, itinerary_text(3))
    first = client.post('/api/ai-guide', json=VALID)
    assert first.status_code == 200
    assert first.get_json()['cached'] is False

    monkeypatch.setattr(app_module, 'GEMINI_API_KEY', '')

    def must_not_run(*args, **kwargs):
        raise AssertionError('a cache hit must not call Gemini')

    monkeypatch.setattr(app_module.requests, 'post', must_not_run)

    second = client.post('/api/ai-guide', json=VALID)

    assert second.status_code == 200
    assert second.get_json()['cached'] is True
    assert second.get_json()['days'] == first.get_json()['days']


def test_cache_key_ignores_case_and_interest_order(client, monkeypatch):
    fake_gemini(monkeypatch, itinerary_text(2))
    first = {**VALID, 'days': 2, 'budget': 'Mid-Range', 'interests': 'forts, street food'}
    assert client.post('/api/ai-guide', json=first).status_code == 200

    monkeypatch.setattr(app_module, 'GEMINI_API_KEY', '')
    second = {**first, 'destination': '  jaipur ', 'interests': 'Street Food, FORTS'}

    response = client.post('/api/ai-guide', json=second)

    assert response.status_code == 200
    assert response.get_json()['cached'] is True


def test_an_expired_cache_row_is_not_served(client, sql):
    key = app_module._itinerary_cache_key({
        'destination': 'Jaipur',
        'days': 3,
        'budget': 'mid-range',
        'interests': 'forts, street food',
    })
    sql(
        'INSERT INTO itinerary_cache (query_hash, payload, created_at) '
        "VALUES (%s, %s, now() - interval '8 days')",
        (key, json.dumps({'days': [{'day': 1}]})),
    )

    # No key in the suite: a fresh cache would answer 200, a stale one falls
    # through to the key check.
    assert client.post('/api/ai-guide', json=VALID).status_code == 503


@pytest.mark.parametrize('text', [
    'not json at all',
    json.dumps({'days': []}),
    json.dumps({'itinerary': []}),
    json.dumps([1, 2, 3]),
    # One day when three were requested.
    itinerary_text(1),
    # A day with no evening slot.
    json.dumps({
        'days': [{'day': 1, 'morning': 'a', 'afternoon': 'b', 'cost': 100}] * 3
    }),
    # An unparseable cost.
    json.dumps({
        'days': [{'day': 1, 'morning': 'a', 'afternoon': 'b',
                  'evening': 'c', 'cost': 'lots'}] * 3
    }),
])
def test_unusable_gemini_replies_become_502_and_are_not_cached(client, monkeypatch, sql, text):
    fake_gemini(monkeypatch, text)

    response = client.post('/api/ai-guide', json=VALID)

    assert response.status_code == 502
    assert 'unusable itinerary' in response.get_json()['error']
    assert sql('SELECT count(*) FROM itinerary_cache')[0][0] == 0


def test_fenced_json_extra_days_and_rupee_costs_are_tolerated(client, monkeypatch):
    entries = [
        {'day': 0, 'morning': f'M{number}', 'afternoon': f'A{number}',
         'evening': f'E{number}', 'cost': f'₹{number},200'}
        for number in range(1, 5)
    ]
    fake_gemini(monkeypatch, '```json\n' + json.dumps({'days': entries}) + '\n```')

    response = client.post('/api/ai-guide', json=VALID)  # three days

    assert response.status_code == 200
    days = response.get_json()['days']
    # The fourth day is trimmed and the server numbers the days itself.
    assert [day['day'] for day in days] == [1, 2, 3]
    assert days[0]['cost'] == 1200
    assert days[2]['cost'] == 3200


def test_the_rate_limit_still_covers_itineraries(client):
    statuses = [client.post('/api/ai-guide', json=VALID).status_code for _ in range(16)]

    assert statuses[:15] == [503] * 15
    assert statuses[15] == 429
