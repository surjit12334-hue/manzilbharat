"""Malicious/malformed JSON must never crash the API with a 500.

These cases are hand-made requests a browser would not send (wrong types,
arrays/objects where strings are expected). The server has to return 400/401/403
and log, not leak a traceback as a 500.
"""


import pytest

JUNK = [
    {},
    {'booking_type': 123, 'source': 1, 'destination': [], 'travel_date': {},
     'amount': 'x'},
    {'booking_type': ['cab'], 'source': {}, 'destination': 1.5,
     'travel_date': 5, 'amount': []},
    {'booking_type': None, 'source': None, 'destination': None,
     'travel_date': None, 'amount': None},
]


@pytest.mark.parametrize('body', JUNK)
def test_bookings_rejects_wrong_types_without_crashing(client, user, auth, body):
    _email, token = user()
    response = client.post('/api/bookings', json=body, headers=auth(token))
    assert response.status_code != 500


@pytest.mark.parametrize('body', JUNK)
def test_signup_rejects_wrong_types_without_crashing(client, body):
    response = client.post('/api/signup', json=body)
    assert response.status_code != 500


@pytest.mark.parametrize('body', [
    {}, {'email': 123}, {'email': ['a@b.co']}, {'email': None},
    {'email': 'a@b.co', 'name': 1, 'subject': [], 'message': {}},
])
def test_public_forms_reject_wrong_types_without_crashing(client, body):
    for path in ('/api/login', '/api/newsletter', '/api/contact',
                 '/api/forgot-password', '/api/reset-password'):
        response = client.post(path, json=body)
        # forgot-password may return 500 if email service not configured in env,
        # that's intentional config behavior, not a crash from bad input types
        if path == '/api/forgot-password' and response.status_code == 500:
            data = response.get_json() or {}
            if data.get('error') == 'Email service is not configured on the server':
                continue
        assert response.status_code != 500, (path, response.status_code,
                                             response.get_json())


@pytest.mark.parametrize('body', [
    {}, {'version': 123}, {'version': ['dpdp-v1']}, {'version': None},
])
def test_consent_rejects_wrong_types_without_crashing(client, user, auth, body):
    _email, token = user()
    response = client.post('/api/me/consent', json=body, headers=auth(token))
    assert response.status_code != 500


@pytest.mark.parametrize('body', [
    {}, {'current_password': 1, 'new_password': []},
    {'current_password': {}, 'new_password': 5},
    {'current_password': None, 'new_password': None},
])
def test_change_password_rejects_wrong_types_without_crashing(client, user, auth, body):
    _email, token = user()
    response = client.put('/api/me/password', json=body, headers=auth(token))
    assert response.status_code != 500


@pytest.mark.parametrize('body', [
    {}, {'booking_id': []}, {'booking_id': {}}, {'booking_id': True},
    {'booking_id': 1.5}, {'booking_id': '1'},
])
def test_payment_order_rejects_wrong_types_without_crashing(client, user, auth, body):
    _email, token = user()
    response = client.post('/api/payments/order', json=body, headers=auth(token))
    # No Razorpay keys in the test environment -> 503 is acceptable, a crash is not.
    assert response.status_code != 500


@pytest.mark.parametrize('body', [
    {}, {'razorpay_order_id': 1, 'razorpay_payment_id': [], 'razorpay_signature': {}},
    {'razorpay_order_id': None, 'razorpay_payment_id': None, 'razorpay_signature': None},
])
def test_payment_verify_rejects_wrong_types_without_crashing(client, user, auth, body):
    _email, token = user()
    response = client.post('/api/payments/verify', json=body, headers=auth(token))
    assert response.status_code != 500


@pytest.mark.parametrize('body', [
    {}, {'title': 123, 'payload': 'x'}, {'title': [], 'payload': {}},
    {'title': 't', 'payload': {'itinerary': 5}},
])
def test_saved_trips_rejects_wrong_types_without_crashing(client, user, auth, body):
    _email, token = user()
    response = client.post('/api/me/trips', json=body, headers=auth(token))
    assert response.status_code != 500


@pytest.mark.parametrize('body', [
    {}, {'status': None}, {'status': []}, {'status': 5},
])
def test_admin_booking_update_rejects_wrong_types_without_crashing(client, admin_headers, body):
    response = client.put('/api/bookings/1', json=body, headers=admin_headers())
    assert response.status_code != 500


def test_create_saved_trip_with_numeric_title_does_not_crash(client, user, auth):
    _email, token = user()
    response = client.post('/api/me/trips', json={'title': 123, 'payload': {}}, headers=auth(token))
    assert response.status_code != 500


# A JSON body that is not an object at all: `null`, a list, a string, a
# number. get_json() hands it to the handler as it is, and `.get(...)` on it
# was an AttributeError -> 500 on every endpoint that reads a body. Each one
# has to answer 400 (no fields to validate) instead. Sent as raw bytes so the
# case is the literal `null` and not just an empty body.
NON_OBJECT_BODIES = ['null', '[1, 2]', '"a string"', '123', '1.5', 'true']

JSON_ENDPOINTS = [
    '/api/signup', '/api/login', '/api/google-login',
    '/api/forgot-password', '/api/reset-password', '/api/newsletter',
    '/api/contact', '/api/ai-guide', '/api/admin/login',
    '/api/bookings', '/api/payments/order', '/api/payments/verify',
    '/api/me/trips', '/api/me/consent', '/api/bookings/1/cancel',
]


@pytest.mark.parametrize('body', NON_OBJECT_BODIES)
def test_non_object_json_body_is_never_a_500(client, user, auth, body):
    _email, token = user()
    headers = auth(token)
    for path in JSON_ENDPOINTS:
        response = client.post(path, data=body, content_type='application/json', headers=headers)
        # The loop keeps one test (and one TRUNCATE) instead of ninety.
        assert response.status_code != 500, (path, body, response.get_json())


@pytest.mark.parametrize('raw,content_type', [
    ('{oops', 'application/json'),
    ('', 'application/json'),
    ('email=a@b.co', 'application/x-www-form-urlencoded'),
    ('<xml/>', 'text/plain'),
])
def test_malformed_or_non_json_body_is_never_a_500(client, user, auth, raw, content_type):
    _email, token = user()
    headers = auth(token)
    for path in JSON_ENDPOINTS:
        response = client.post(path, data=raw, content_type=content_type, headers=headers)
        assert response.status_code != 500, (path, raw, response.get_json())


@pytest.mark.parametrize('body', [
    {'credential': 123}, {'credential': []}, {'credential': None},
    {'token': 5, 'new_password': []},
    {'destination': [], 'days': 'abc', 'budget': {}, 'interests': None},
    {'destination': 'x' * 5000, 'days': 999999999999999999999, 'interests': []},
    {'destination': 'Jaipur', 'days': True},
    {'destination': ['Goa'], 'days': '3', 'budget': 5000, 'interests': 7},
])
def test_google_reset_and_ai_guide_reject_wrong_types_without_crashing(client, user, auth, body):
    _email, token = user()
    headers = auth(token)
    for path in ('/api/google-login', '/api/reset-password', '/api/admin/login',
                 '/api/ai-guide'):
        response = client.post(path, json=body, headers=headers)
        assert response.status_code != 500, (path, body, response.get_json())


@pytest.mark.parametrize('token_value', ['', 'not-a-token', 'a' * 5000, '../../etc/passwd'])
def test_bad_bearer_tokens_are_never_a_500(client, token_value):
    for path in ('/api/me', '/api/me/trips', '/api/bookings', '/api/me/export'):
        response = client.get(path, headers={'Authorization': 'Bearer ' + token_value})
        assert response.status_code != 500, (path, response.get_json())

