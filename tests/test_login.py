"""Login: right credentials, wrong ones, and the anti-enumeration rule."""


def test_login_succeeds_with_the_right_password(client, register, password):
    register()

    response = client.post('/api/login',
                           json={'email': 'test.user@example.com', 'password': password})

    assert response.status_code == 200
    body = response.get_json()
    assert body['user']['email'] == 'test.user@example.com'
    assert body['token']


def test_login_ignores_email_case_and_surrounding_spaces(client, register, password):
    register()

    response = client.post('/api/login',
                           json={'email': '  TEST.User@Example.com ', 'password': password})

    assert response.status_code == 200


def test_wrong_password_and_unknown_email_are_indistinguishable(client, register, password):
    """The same status and body for both, so the endpoint cannot be used to
    enumerate registered addresses."""
    register()

    wrong = client.post('/api/login',
                        json={'email': 'test.user@example.com', 'password': 'NotThePassword1!'})
    unknown = client.post('/api/login',
                          json={'email': 'nobody@example.com', 'password': password})

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.get_json() == unknown.get_json() == {'error': 'Invalid email or password'}


def test_login_requires_both_fields(client, register):
    register()

    assert client.post('/api/login', json={'email': 'test.user@example.com'}).status_code == 400
    assert client.post('/api/login', json={'password': 'whatever'}).status_code == 400
    assert client.post('/api/login', json={}).status_code == 400


def test_a_passwordless_account_cannot_log_in(client, register, sql):
    """Google sign-in stores a NULL password; this must not match anything."""
    register()
    sql('UPDATE users SET password = NULL')

    response = client.post('/api/login',
                           json={'email': 'test.user@example.com', 'password': 'Anything1!'})

    assert response.status_code == 401


def test_a_tampered_or_missing_token_is_refused(client, user, auth):
    email, token = user()

    assert client.get('/api/me', headers=auth(token)).status_code == 200

    tampered = token[:-1] + ('a' if token[-1] != 'a' else 'b')
    assert client.get('/api/me', headers=auth(tampered)).status_code == 401
    assert client.get('/api/me').status_code == 401
