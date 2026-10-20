"""Bookings: create, list only your own, cancel, and the admin-only routes.

The rule this file exists to protect: a customer can never read, cancel or
delete somebody else's booking.
"""


def test_create_booking(client, user, auth, booking_payload, sql):
    email, token = user()

    response = client.post('/api/bookings', json=booking_payload(), headers=auth(token))

    assert response.status_code == 201
    booking = response.get_json()['booking']
    assert booking['status'] == 'pending'
    assert booking['destination'] == 'Jaipur'
    assert sql('SELECT count(*) FROM bookings')[0][0] == 1


def test_create_booking_requires_a_token(client, booking_payload):
    assert client.post('/api/bookings', json=booking_payload()).status_code == 401


def test_booking_validation(client, user, auth, booking_payload, sql):
    email, token = user()
    headers = auth(token)

    past = client.post('/api/bookings', headers=headers,
                       json=booking_payload(travel_date='2020-01-01'))
    assert past.status_code == 400
    assert 'travel_date' in past.get_json()['errors']

    kind = client.post('/api/bookings', headers=headers,
                       json=booking_payload(booking_type='teleport'))
    assert kind.status_code == 400
    assert 'booking_type' in kind.get_json()['errors']

    for bad in (-1, 1000001, 'not-a-number', None):
        response = client.post('/api/bookings', headers=headers,
                               json=booking_payload(amount=bad))
        assert response.status_code == 400, bad
        assert 'amount' in response.get_json()['errors'], bad

    for field in ('source', 'destination'):
        response = client.post('/api/bookings', headers=headers,
                               json=booking_payload(**{field: ''}))
        assert response.status_code == 400, field
        assert field in response.get_json()['errors'], field

    assert sql('SELECT count(*) FROM bookings')[0][0] == 0


def test_mine_lists_only_your_own(client, user, auth, booking_payload):
    email_a, token_a = user(email='a@example.com')
    email_b, token_b = user(email='b@example.com')

    client.post('/api/bookings', headers=auth(token_a),
                json=booking_payload(destination='Manali'))
    client.post('/api/bookings', headers=auth(token_b),
                json=booking_payload(destination='Goa'))

    mine_a = client.get('/api/bookings/mine', headers=auth(token_a)).get_json()
    mine_b = client.get('/api/bookings/mine', headers=auth(token_b)).get_json()

    assert [b['destination'] for b in mine_a] == ['Manali']
    assert [b['destination'] for b in mine_b] == ['Goa']
    assert client.get('/api/bookings/mine').status_code == 401


def test_cancel_your_own_booking_is_idempotent(client, user, auth, booking_payload):
    email, token = user()
    booking = client.post('/api/bookings', headers=auth(token),
                          json=booking_payload()).get_json()['booking']

    response = client.post(f"/api/bookings/{booking['id']}/cancel", headers=auth(token))
    assert response.status_code == 200
    assert response.get_json()['booking']['status'] == 'cancelled'

    again = client.post(f"/api/bookings/{booking['id']}/cancel", headers=auth(token))
    assert again.status_code == 200
    assert 'already cancelled' in again.get_json()['message']


def test_cannot_cancel_someone_elses_booking(client, user, auth, booking_payload, sql):
    email_a, token_a = user(email='a@example.com')
    email_b, token_b = user(email='b@example.com')
    booking = client.post('/api/bookings', headers=auth(token_a),
                          json=booking_payload()).get_json()['booking']

    response = client.post(f"/api/bookings/{booking['id']}/cancel", headers=auth(token_b))

    assert response.status_code == 403
    assert sql("SELECT status FROM bookings WHERE id = %s",
               (booking['id'],))[0][0] == 'pending'


def test_a_completed_booking_cannot_be_cancelled(client, user, auth, booking_payload, sql):
    email, token = user()
    booking = client.post('/api/bookings', headers=auth(token),
                          json=booking_payload()).get_json()['booking']
    sql("UPDATE bookings SET status = 'completed' WHERE id = %s", (booking['id'],))

    response = client.post(f"/api/bookings/{booking['id']}/cancel", headers=auth(token))

    assert response.status_code == 409


def test_cancelling_a_missing_booking_is_404(client, user, auth):
    email, token = user()

    assert client.post('/api/bookings/9999/cancel', headers=auth(token)).status_code == 404


def test_hard_delete_is_admin_only(client, user, auth, booking_payload):
    """Users cancel (soft); only an admin deletes. A user session - even one
    presented in the admin header - must not get through."""
    email, token = user()
    booking = client.post('/api/bookings', headers=auth(token),
                          json=booking_payload()).get_json()['booking']

    assert client.delete(f"/api/bookings/{booking['id']}").status_code == 401
    assert client.delete(f"/api/bookings/{booking['id']}",
                         headers={'x-admin-token': token}).status_code == 403
