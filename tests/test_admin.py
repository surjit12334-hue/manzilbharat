"""Admin endpoints: who gets in, and which rejection they get.

401 means "not an admin session at all"; 403 means "a valid session, but not
the admin one". Keeping those apart matters because the frontend shows a
different message for each.
"""


def test_admin_login_rejects_wrong_credentials(client, admin_credentials):
    email, password = admin_credentials

    wrong_email = client.post('/api/admin/login',
                              json={'email': 'nope@example.com', 'password': password})
    wrong_password = client.post('/api/admin/login',
                                 json={'email': email, 'password': 'WrongPassword1!'})

    assert wrong_email.status_code == wrong_password.status_code == 401
    assert wrong_email.get_json() == wrong_password.get_json() == {'error': 'Invalid admin credentials'}


def test_admin_routes_reject_a_missing_token(client):
    assert client.get('/api/users').status_code == 401
    assert client.get('/api/stats').status_code == 401
    assert client.get('/api/recent').status_code == 401


def test_a_user_token_in_the_admin_header_is_forbidden(client, user):
    _email, token = user()

    response = client.get('/api/users', headers={'x-admin-token': token})

    assert response.status_code == 403


def test_admin_can_read_users_and_stats(client, user, admin_headers):
    email, _token = user()

    users = client.get('/api/users', headers=admin_headers())
    assert users.status_code == 200
    assert [row['email'] for row in users.get_json()] == [email]

    stats = client.get('/api/stats', headers=admin_headers())
    assert stats.status_code == 200
    assert stats.get_json() == {
        'totalUsers': 1, 'totalBookings': 0, 'totalPayments': 0, 'totalRevenue': 0.0,
    }


def test_admin_can_delete_a_user(client, user, admin_headers, sql):
    _email, _token = user()
    user_id = sql('SELECT id FROM users')[0][0]

    response = client.delete(f'/api/users/{user_id}', headers=admin_headers())

    assert response.status_code == 200
    assert sql('SELECT count(*) FROM users')[0][0] == 0


def test_deleting_a_missing_user_is_404(client, admin_headers):
    assert client.delete('/api/users/9999', headers=admin_headers()).status_code == 404


def test_deleting_a_user_takes_their_bookings_with_them(
        client, user, auth, admin_headers, booking_payload, sql):
    """ON DELETE CASCADE from schema.sql - without it this is a 409 and a user
    can never be removed."""
    _email, token = user()
    assert client.post('/api/bookings', json=booking_payload(),
                       headers=auth(token)).status_code == 201
    user_id = sql('SELECT id FROM users')[0][0]

    assert client.delete(f'/api/users/{user_id}',
                         headers=admin_headers()).status_code == 200
    assert sql('SELECT count(*) FROM bookings')[0][0] == 0
