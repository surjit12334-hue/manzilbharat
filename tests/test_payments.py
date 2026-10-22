"""Razorpay TEST mode (Phase 5 step 2).

The rules under test:

* the checkout is only opened for a booking that belongs to the caller;
* no key, or a LIVE key, means no payment - the server refuses to take one;
* the payment is only recorded when the signature the browser sends back is
  the one the SECRET key produces: a forged signature buys nothing;
* every payment this build records is marked 'test';
* the secret key never reaches the browser.

The Razorpay API itself is not called: the tests replace the one function
that talks to it, so the suite is offline and never needs a real account.
"""
import hashlib
import hmac

import pytest

TEST_KEY_ID = 'rzp_test_ManzilFake'
TEST_KEY_SECRET = 'fake-test-secret-for-the-suite'


@pytest.fixture()
def razorpay(monkeypatch):
    """Configure fake TEST keys and stub the call to Razorpay's API."""
    import backend.app as app_module

    order = {
        'id': 'order_TEST123',
        'amount': 240000,
        'currency': 'INR',
        'status': 'created',
    }
    monkeypatch.setattr(app_module, 'RAZORPAY_KEY_ID', TEST_KEY_ID)
    monkeypatch.setattr(app_module, 'RAZORPAY_KEY_SECRET', TEST_KEY_SECRET)

    def fake_create_order(amount_paise, receipt, notes):
        return {**order, 'amount': amount_paise, 'receipt': receipt}

    monkeypatch.setattr(app_module, 'razorpay_create_order', fake_create_order)
    return order


@pytest.fixture()
def no_keys(monkeypatch):
    """Make sure no Razorpay keys are configured, whatever .env says."""
    import backend.app as app_module

    monkeypatch.setattr(app_module, 'RAZORPAY_KEY_ID', '')
    monkeypatch.setattr(app_module, 'RAZORPAY_KEY_SECRET', '')


def signature_for(order_id, payment_id, secret=TEST_KEY_SECRET):
    """The signature Razorpay's docs define: HMAC-SHA256 of order|payment."""
    message = f'{order_id}|{payment_id}'.encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def open_order(client, headers, booking_payload, **overrides):
    """Create a booking and open a payment order for it."""
    booking = client.post('/api/bookings', headers=headers,
                          json=booking_payload(**overrides))
    assert booking.status_code == 201, booking.get_json()
    booking_id = booking.get_json()['booking']['id']

    response = client.post('/api/payments/order', headers=headers,
                           json={'booking_id': booking_id})
    assert response.status_code == 201, response.get_json()
    return booking_id, response.get_json()


# --------------------------------------------------------------------------
# Opening an order
# --------------------------------------------------------------------------

def test_the_order_endpoint_needs_a_session(client, booking_payload):
    assert client.post('/api/payments/order',
                       json={'booking_id': 1}).status_code == 401


def test_without_keys_the_checkout_is_refused(client, user, auth,
                                              booking_payload, no_keys):
    _email, token = user()
    headers = auth(token)
    booking = client.post('/api/bookings', headers=headers, json=booking_payload())

    response = client.post('/api/payments/order', headers=headers,
                           json={'booking_id': booking.get_json()['booking']['id']})

    assert response.status_code == 503
    assert 'not configured' in response.get_json()['error'].lower()


def test_a_live_key_is_refused(client, user, auth, booking_payload, monkeypatch):
    """This build only takes TEST money - a live key must not be accepted."""
    import backend.app as app_module

    monkeypatch.setattr(app_module, 'RAZORPAY_KEY_ID', 'rzp_live_ThisIsReal')
    monkeypatch.setattr(app_module, 'RAZORPAY_KEY_SECRET', TEST_KEY_SECRET)
    _email, token = user()
    headers = auth(token)

    booking = client.post('/api/bookings', headers=headers, json=booking_payload())
    response = client.post('/api/payments/order', headers=headers,
                           json={'booking_id': booking.get_json()['booking']['id']})

    assert response.status_code == 503
    assert 'TEST' in response.get_json()['error']


def test_the_order_opens_a_pending_test_payment(client, user, auth,
                                                booking_payload, razorpay, sql):
    _email, token = user()
    headers = auth(token)

    booking_id, order = open_order(client, headers, booking_payload)

    assert order['order_id'] == 'order_TEST123'
    assert order['booking_id'] == booking_id
    assert order['amount'] == 2400
    assert order['amount_paise'] == 240000
    assert order['currency'] == 'INR'
    assert order['mode'] == 'test'
    # The browser gets the PUBLIC key id, never the secret.
    assert order['key_id'] == TEST_KEY_ID

    row = sql('SELECT status, mode, razorpay_order_id, booking_id FROM payments')[0]
    assert row == ('pending', 'test', 'order_TEST123', booking_id)


def test_the_secret_never_reaches_the_browser(client, user, auth,
                                              booking_payload, razorpay):
    _email, token = user()

    _booking_id, order = open_order(client, auth(token), booking_payload)

    assert TEST_KEY_SECRET not in str(order)


def test_another_users_booking_cannot_be_paid(client, user, auth,
                                              booking_payload, razorpay, sql):
    _email_a, token_a = user(email='a@example.com')
    _email_b, token_b = user(email='b@example.com')

    booking = client.post('/api/bookings', headers=auth(token_a),
                          json=booking_payload())
    booking_id = booking.get_json()['booking']['id']

    response = client.post('/api/payments/order', headers=auth(token_b),
                           json={'booking_id': booking_id})

    assert response.status_code == 403
    assert sql('SELECT count(*) FROM payments')[0][0] == 0


def test_an_unknown_booking_is_not_payable(client, user, auth, razorpay):
    _email, token = user()

    response = client.post('/api/payments/order', headers=auth(token),
                           json={'booking_id': 99999})

    assert response.status_code == 404


def test_a_booking_without_an_id_is_refused(client, user, auth, razorpay):
    _email, token = user()

    for body in ({}, {'booking_id': 'not-a-number'}, {'booking_id': None}):
        response = client.post('/api/payments/order', headers=auth(token), json=body)
        assert response.status_code == 400, body


def test_a_cancelled_booking_is_not_payable(client, user, auth,
                                            booking_payload, razorpay):
    _email, token = user()
    headers = auth(token)
    booking = client.post('/api/bookings', headers=headers, json=booking_payload())
    booking_id = booking.get_json()['booking']['id']
    client.post(f'/api/bookings/{booking_id}/cancel', headers=headers)

    response = client.post('/api/payments/order', headers=headers,
                           json={'booking_id': booking_id})

    assert response.status_code == 400
    assert 'cancelled' in response.get_json()['error'].lower()


def test_a_zero_amount_booking_is_not_payable(client, user, auth,
                                              booking_payload, razorpay):
    _email, token = user()
    headers = auth(token)
    booking = client.post('/api/bookings', headers=headers,
                          json=booking_payload(amount=0))

    response = client.post('/api/payments/order', headers=headers,
                           json={'booking_id': booking.get_json()['booking']['id']})

    assert response.status_code == 400
    assert 'amount' in response.get_json()['error'].lower()


def test_an_amount_above_the_razorpay_limit_is_refused(client, user, auth,
                                                      booking_payload, razorpay,
                                                      sql):
    """Razorpay accepts ₹50,000 in one order - the server says so up front
    instead of letting the API reject it."""
    _email, token = user()
    headers = auth(token)
    booking = client.post('/api/bookings', headers=headers,
                          json=booking_payload(amount=60000))

    response = client.post('/api/payments/order', headers=headers,
                           json={'booking_id': booking.get_json()['booking']['id']})

    assert response.status_code == 400
    assert '50,000' in response.get_json()['error']
    assert sql('SELECT count(*) FROM payments')[0][0] == 0


# --------------------------------------------------------------------------
# The signature - the heart of it
# --------------------------------------------------------------------------

def test_the_signature_is_the_documented_hmac(razorpay):
    """A fixed vector: the value Razorpay's docs say these inputs produce.

    Hardcoded on purpose - if the server ever computes something else, this
    fails even though a test that recomputes the HMAC itself would pass.
    """
    from backend.app import razorpay_signature

    assert razorpay_signature('order_TEST123', 'pay_TEST456') == (
        'd4928cbf6e2a7c1b5150a180862434b9571b9258d4a6bcece964ce7c71f067fa'
    )


def test_a_real_signature_captures_the_payment(client, user, auth,
                                               booking_payload, razorpay, sql):
    _email, token = user()
    headers = auth(token)
    booking_id, order = open_order(client, headers, booking_payload)

    response = client.post('/api/payments/verify', headers=headers, json={
        'razorpay_order_id': order['order_id'],
        'razorpay_payment_id': 'pay_TEST456',
        'razorpay_signature': signature_for(order['order_id'], 'pay_TEST456'),
    })

    assert response.status_code == 200, response.get_json()
    assert response.get_json()['status'] == 'captured'
    assert response.get_json()['mode'] == 'test'

    payment = sql('SELECT status, mode, razorpay_payment_id FROM payments')[0]
    assert payment == ('captured', 'test', 'pay_TEST456')
    # A paid trip is a confirmed trip.
    assert sql('SELECT status FROM bookings WHERE id = %s', (booking_id,))[0][0] == 'confirmed'


def test_a_forged_signature_is_refused(client, user, auth,
                                       booking_payload, razorpay, sql):
    _email, token = user()
    headers = auth(token)
    _booking_id, order = open_order(client, headers, booking_payload)

    response = client.post('/api/payments/verify', headers=headers, json={
        'razorpay_order_id': order['order_id'],
        'razorpay_payment_id': 'pay_TEST456',
        'razorpay_signature': '0' * 64,
    })

    assert response.status_code == 400
    assert sql('SELECT status FROM payments')[0][0] == 'pending'
    assert sql('SELECT status FROM bookings')[0][0] == 'pending'


def test_a_signature_for_another_payment_is_refused(client, user, auth,
                                                    booking_payload, razorpay, sql):
    """The signature is bound to the payment id: reusing the signature of a
    cheaper payment for a dearer one must not work."""
    _email, token = user()
    headers = auth(token)
    _booking_id, order = open_order(client, headers, booking_payload)

    response = client.post('/api/payments/verify', headers=headers, json={
        'razorpay_order_id': order['order_id'],
        'razorpay_payment_id': 'pay_TEST456',
        'razorpay_signature': signature_for(order['order_id'], 'pay_CHEAPER'),
    })

    assert response.status_code == 400
    assert sql('SELECT status FROM payments')[0][0] == 'pending'


def test_a_signature_made_with_the_wrong_secret_is_refused(client, user, auth,
                                                          booking_payload, razorpay,
                                                          sql):
    _email, token = user()
    headers = auth(token)
    _booking_id, order = open_order(client, headers, booking_payload)

    response = client.post('/api/payments/verify', headers=headers, json={
        'razorpay_order_id': order['order_id'],
        'razorpay_payment_id': 'pay_TEST456',
        'razorpay_signature': signature_for(order['order_id'], 'pay_TEST456',
                                            secret='not-the-secret'),
    })

    assert response.status_code == 400
    assert sql('SELECT status FROM payments')[0][0] == 'pending'


def test_an_incomplete_verification_is_refused(client, user, auth, razorpay):
    _email, token = user()

    for body in ({}, {'razorpay_order_id': 'order_TEST123'},
                 {'razorpay_order_id': 'order_TEST123',
                  'razorpay_payment_id': 'pay_TEST456'}):
        response = client.post('/api/payments/verify', headers=auth(token), json=body)
        assert response.status_code == 400, body


def test_another_users_order_cannot_be_confirmed(client, user, auth,
                                                 booking_payload, razorpay,
                                                 sql):
    _email_a, token_a = user(email='a@example.com')
    _email_b, token_b = user(email='b@example.com')
    _booking_id, order = open_order(client, auth(token_a), booking_payload)

    # B knows the order id and can compute a signature (the test knows the
    # secret) - it still must not confirm A's payment.
    response = client.post('/api/payments/verify', headers=auth(token_b), json={
        'razorpay_order_id': order['order_id'],
        'razorpay_payment_id': 'pay_TEST456',
        'razorpay_signature': signature_for(order['order_id'], 'pay_TEST456'),
    })

    assert response.status_code == 404
    assert sql('SELECT status FROM payments')[0][0] == 'pending'


def test_verifying_twice_is_harmless(client, user, auth,
                                     booking_payload, razorpay, sql):
    """A double-submit (or a browser retry) must not fail or duplicate."""
    _email, token = user()
    headers = auth(token)
    _booking_id, order = open_order(client, headers, booking_payload)
    body = {
        'razorpay_order_id': order['order_id'],
        'razorpay_payment_id': 'pay_TEST456',
        'razorpay_signature': signature_for(order['order_id'], 'pay_TEST456'),
    }

    first = client.post('/api/payments/verify', headers=headers, json=body)
    second = client.post('/api/payments/verify', headers=headers, json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()['status'] == 'captured'
    assert sql('SELECT count(*) FROM payments')[0][0] == 1


def test_the_verify_endpoint_needs_a_session(client, razorpay):
    response = client.post('/api/payments/verify', json={
        'razorpay_order_id': 'order_TEST123',
        'razorpay_payment_id': 'pay_TEST456',
        'razorpay_signature': 'x',
    })

    assert response.status_code == 401


# --------------------------------------------------------------------------
# The payment list and the 'test' marking
# --------------------------------------------------------------------------

def test_a_paid_trip_cannot_be_paid_again(client, user, auth,
                                          booking_payload, razorpay):
    _email, token = user()
    headers = auth(token)
    booking_id, order = open_order(client, headers, booking_payload)
    client.post('/api/payments/verify', headers=headers, json={
        'razorpay_order_id': order['order_id'],
        'razorpay_payment_id': 'pay_TEST456',
        'razorpay_signature': signature_for(order['order_id'], 'pay_TEST456'),
    })

    response = client.post('/api/payments/order', headers=headers,
                           json={'booking_id': booking_id})

    assert response.status_code == 400
    assert 'already paid' in response.get_json()['error'].lower()


def test_mine_lists_only_your_own_payments(client, user, auth,
                                           booking_payload, razorpay):
    _email_a, token_a = user(email='a@example.com')
    _email_b, token_b = user(email='b@example.com')
    booking_a, _order = open_order(client, auth(token_a), booking_payload)
    # B has a booking but no payment: it must not show up in A's list.
    client.post('/api/bookings', headers=auth(token_b), json=booking_payload())

    own = client.get('/api/payments/mine', headers=auth(token_a))
    assert own.status_code == 200
    rows = own.get_json()
    assert len(rows) == 1
    assert rows[0]['booking_id'] == booking_a

    theirs = client.get('/api/payments/mine', headers=auth(token_b))
    assert theirs.get_json() == []


def test_every_payment_is_marked_as_a_test_payment(client, user, auth,
                                                   booking_payload, razorpay,
                                                   sql):
    _email, token = user()
    headers = auth(token)
    _booking_id, order = open_order(client, headers, booking_payload)
    client.post('/api/payments/verify', headers=headers, json={
        'razorpay_order_id': order['order_id'],
        'razorpay_payment_id': 'pay_TEST456',
        'razorpay_signature': signature_for(order['order_id'], 'pay_TEST456'),
    })

    rows = client.get('/api/payments/mine', headers=headers).get_json()

    assert rows[0]['mode'] == 'test'
    assert sql("SELECT count(*) FROM payments WHERE mode <> 'test'")[0][0] == 0


def test_mine_needs_a_session(client):
    assert client.get('/api/payments/mine').status_code == 401


def test_the_admin_revenue_counts_what_the_checkout_recorded(client, user, auth,
                                                            booking_payload, razorpay,
                                                            admin_headers, sql):
    """The dashboard has to reflect the money the checkout recorded.

    The stats query used to sum status='completed' only - a status the
    checkout never writes - so a successful test payment left the Revenue
    card at Rs.0. The seeded demo rows are still 'completed' and must keep
    counting, so the query has to know both.
    """
    _email, token = user()
    headers = auth(token)
    booking_id, order = open_order(client, headers, booking_payload)
    client.post('/api/payments/verify', headers=headers, json={
        'razorpay_order_id': order['order_id'],
        'razorpay_payment_id': 'pay_TEST456',
        'razorpay_signature': signature_for(order['order_id'], 'pay_TEST456'),
    })
    # A row shaped like seed.sql's demo history.
    sql(
        "INSERT INTO payments (user_id, booking_id, amount, currency, status, mode) "
        "SELECT user_id, id, 950, 'INR', 'completed', 'test' FROM bookings "
        "WHERE id = %s",
        (booking_id,),
    )

    stats = client.get('/api/stats', headers=admin_headers())

    assert stats.status_code == 200
    assert stats.get_json()['totalPayments'] == 2
    assert stats.get_json()['totalRevenue'] == 3350.0


def test_deleting_the_account_takes_the_payment_with_it(client, user, auth,
                                                       booking_payload, razorpay,
                                                       sql):
    """DPDP erasure has to reach the payment row too - it is personal data
    (it says what this person bought and when)."""
    _email, token = user()
    headers = auth(token)
    _booking_id, order = open_order(client, headers, booking_payload)
    client.post('/api/payments/verify', headers=headers, json={
        'razorpay_order_id': order['order_id'],
        'razorpay_payment_id': 'pay_TEST456',
        'razorpay_signature': signature_for(order['order_id'], 'pay_TEST456'),
    })
    assert sql('SELECT count(*) FROM payments')[0][0] == 1

    response = client.delete('/api/me', headers=headers)

    assert response.status_code == 200, response.get_json()
    assert sql('SELECT count(*) FROM payments')[0][0] == 0
    assert sql('SELECT count(*) FROM bookings')[0][0] == 0
