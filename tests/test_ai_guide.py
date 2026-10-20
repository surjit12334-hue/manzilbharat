"""The AI proxy: offline by default, mocks for the Gemini paths, and the
quota guard in front of the endpoint.

Nothing here touches the network. When GEMINI_API_KEY is unset (as conftest
leaves it) the endpoint answers 503; the tests that need a response patch
requests.post and the module's key.
"""
import pytest

import backend.app as app_module

VALID = {'kind': 'destination', 'query': 'Jaipur'}


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


def gemini_body(text):
    return {'candidates': [{'content': {'parts': [{'text': text}]}}]}


def test_validation_happens_before_anything_else(client):
    unknown = client.post('/api/ai-guide', json={'kind': 'horoscope', 'query': 'x'})
    assert unknown.status_code == 400

    empty = client.post('/api/ai-guide', json={'kind': 'destination', 'query': '   '})
    assert empty.status_code == 400

    too_long = client.post('/api/ai-guide', json={'kind': 'destination', 'query': 'x' * 301})
    assert too_long.status_code == 400


def test_without_a_key_the_endpoint_says_so(client):
    """conftest drops GEMINI_API_KEY, so the suite never spends quota."""
    response = client.post('/api/ai-guide', json=VALID)

    assert response.status_code == 503
    assert 'not configured' in response.get_json()['error']


def test_a_successful_gemini_call_is_relayed(client, monkeypatch):
    monkeypatch.setattr(app_module, 'GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(app_module.requests, 'post',
                        lambda *a, **k: FakeResponse(payload=gemini_body('Why go: x')))

    response = client.post('/api/ai-guide', json=VALID)

    assert response.status_code == 200
    assert response.get_json() == {'text': 'Why go: x'}


@pytest.mark.parametrize('gemini_status,expected', [
    (429, 429),   # Google rate-limiting us
    (500, 502),   # anything else upstream
])
def test_upstream_errors(client, monkeypatch, gemini_status, expected):
    monkeypatch.setattr(app_module, 'GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(app_module.requests, 'post',
                        lambda *a, **k: FakeResponse(status_code=gemini_status,
                                                     text='upstream says no'))

    response = client.post('/api/ai-guide', json=VALID)

    assert response.status_code == expected
    # Google's own body is never forwarded to the browser.
    assert 'upstream says no' not in response.get_data(as_text=True)


def test_timeout_and_connection_failure(client, monkeypatch):
    monkeypatch.setattr(app_module, 'GEMINI_API_KEY', 'test-key')

    def boom(*a, **k):
        raise app_module.requests.exceptions.Timeout

    monkeypatch.setattr(app_module.requests, 'post', boom)
    assert client.post('/api/ai-guide', json=VALID).status_code == 504

    def unreachable(*a, **k):
        raise app_module.requests.exceptions.ConnectionError

    monkeypatch.setattr(app_module.requests, 'post', unreachable)
    assert client.post('/api/ai-guide', json=VALID).status_code == 502


def test_malformed_or_empty_gemini_response(client, monkeypatch):
    monkeypatch.setattr(app_module, 'GEMINI_API_KEY', 'test-key')

    monkeypatch.setattr(app_module.requests, 'post',
                        lambda *a, **k: FakeResponse(payload={'unexpected': True}))
    assert client.post('/api/ai-guide', json=VALID).status_code == 502

    monkeypatch.setattr(app_module.requests, 'post',
                        lambda *a, **k: FakeResponse(payload=gemini_body('   ')))
    assert client.post('/api/ai-guide', json=VALID).status_code == 502


def test_rate_limit_returns_429_after_15_requests(client):
    """The limiter runs before the handler, so the missing key (503) does not
    hide it. 15 in the window, the 16th is refused."""
    statuses = [client.post('/api/ai-guide', json=VALID).status_code for _ in range(16)]

    assert statuses[:15] == [503] * 15
    assert statuses[15] == 429
