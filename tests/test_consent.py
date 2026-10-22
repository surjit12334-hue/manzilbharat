"""DPDP: consent at signup, the consent history, data export and deletion.

The rules under test:

* an account cannot be created without an explicit consent;
* the consent is stored with the version of the policy text that was accepted,
  so we can always prove WHICH wording somebody agreed to;
* an unknown version is refused instead of being recorded against the wrong
  text;
* /api/me/export returns the caller's own data and nobody else's;
* DELETE /api/me removes the account and everything attached to it.
"""


# --------------------------------------------------------------------------
# Consent at signup
# --------------------------------------------------------------------------

def test_signup_without_consent_is_refused(client, register, sql):
    response = register(consent=False)

    assert response.status_code == 400
    assert 'consent' in response.get_json()['errors']
    # Nothing was created.
    assert sql('SELECT count(*) FROM users')[0][0] == 0


def test_signup_with_a_missing_consent_field_is_refused(client, register, sql):
    """The key has to be there - a client that simply forgets it gets no
    account."""
    # Build the body directly instead of going through the fixture, so the
    # consent key really is absent.
    body = {
        'name': 'Test User',
        'email': 'test.user@example.com',
        'contact': '9876543210',
        'gender': 'Male',
        'dob': '1995-04-12',
        'marital_status': 'Single',
        'residential_address': '1 Test Road, Bengaluru',
        'pincode': '560001',
        'password': 'Str0ng!Pass1',
    }

    response = client.post('/api/signup', json=body)

    assert response.status_code == 400
    assert 'consent' in response.get_json()['errors']
    assert sql('SELECT count(*) FROM users')[0][0] == 0


def test_signup_records_the_consent(client, register, sql):
    response = register()

    assert response.status_code == 201
    rows = sql('SELECT user_id, text_version FROM consents')
    assert len(rows) == 1
    user_id, version = rows[0]
    assert version == 'dpdp-v1'
    assert user_id == sql('SELECT id FROM users')[0][0]


def test_the_consent_row_belongs_to_the_new_user(client, register, sql):
    register(email='first@example.com')
    register(email='second@example.com')

    rows = sql('SELECT c.user_id, u.email FROM consents c JOIN users u ON u.id = c.user_id')
    assert len(rows) == 2
    assert {row[1] for row in rows} == {'first@example.com', 'second@example.com'}


def test_an_unknown_consent_version_is_refused(client, register, sql):
    """A page that quotes an old policy must not be recorded against the new
    wording."""
    response = register(consent_version='dpdp-v0')

    assert response.status_code == 400
    assert 'consent' in response.get_json()['errors']
    assert sql('SELECT count(*) FROM users')[0][0] == 0


# --------------------------------------------------------------------------
# Consent history
# --------------------------------------------------------------------------

def test_consent_history_is_listed_newest_first(client, user, auth, sql):
    _email, token = user()
    headers = auth(token)

    client.post('/api/me/consent', json={'version': 'dpdp-v1'}, headers=headers)
    client.post('/api/me/consent', json={'version': 'dpdp-v1'}, headers=headers)

    response = client.get('/api/me/consent', headers=headers)

    assert response.status_code == 200
    rows = response.get_json()
    assert len(rows) == 3  # one at signup + two more
    assert all(row['text_version'] == 'dpdp-v1' for row in rows)
    # Newest first.
    assert rows[0]['created_at'] >= rows[-1]['created_at']


def test_an_unknown_version_cannot_be_recorded(client, user, auth, sql):
    _email, token = user()
    headers = auth(token)

    response = client.post('/api/me/consent', json={'version': 'made-up'}, headers=headers)

    assert response.status_code == 400
    assert sql('SELECT count(*) FROM consents')[0][0] == 1  # only the signup row


def test_consent_history_needs_a_token(client):
    assert client.get('/api/me/consent').status_code == 401
    assert client.post('/api/me/consent', json={'version': 'dpdp-v1'}).status_code == 401


def test_consent_history_only_shows_your_own(client, user, auth, sql):
    _email_a, _token_a = user(email='a@example.com')
    _email_b, token_b = user(email='b@example.com')

    response = client.get('/api/me/consent', headers=auth(token_b))

    assert response.status_code == 200
    user_b = sql('SELECT id FROM users WHERE email = %s', ('b@example.com',))[0][0]
    assert all(row['user_id'] == user_b for row in response.get_json())


# --------------------------------------------------------------------------
# Data export
# --------------------------------------------------------------------------

def test_export_returns_the_callers_data(client, user, auth, booking_payload):
    email, token = user()
    headers = auth(token)
    client.post('/api/bookings', json=booking_payload(), headers=headers)

    response = client.get('/api/me/export', headers=headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body['user']['email'] == email
    assert len(body['bookings']) == 1
    assert 'consents' in body
    assert body['consents'][0]['text_version'] == 'dpdp-v1'
    assert 'exported_at' in body


def test_export_does_not_leak_another_users_data(client, user, auth, booking_payload):
    _email_a, token_a = user(email='a@example.com')
    _email_b, token_b = user(email='b@example.com')
    client.post('/api/bookings', json=booking_payload(), headers=auth(token_a))

    response = client.get('/api/me/export', headers=auth(token_b))

    assert response.status_code == 200
    body = response.get_json()
    assert body['user']['email'] == 'b@example.com'
    assert body['bookings'] == []


def test_export_needs_a_token(client):
    assert client.get('/api/me/export').status_code == 401


# --------------------------------------------------------------------------
# Account deletion
# --------------------------------------------------------------------------

def test_delete_account_removes_the_account_and_its_data(client, user, auth,
                                                          booking_payload, sql):
    email, token = user()
    headers = auth(token)
    client.post('/api/bookings', json=booking_payload(), headers=headers)

    response = client.delete('/api/me', headers=headers)

    assert response.status_code == 200
    assert sql('SELECT count(*) FROM users WHERE email = %s', (email,))[0][0] == 0
    assert sql('SELECT count(*) FROM bookings')[0][0] == 0
    assert sql('SELECT count(*) FROM consents')[0][0] == 0


def test_delete_account_leaves_other_accounts_alone(client, user, auth, sql):
    user(email='a@example.com')
    _email_b, token_b = user(email='b@example.com')

    client.delete('/api/me', headers=auth(token_b))

    emails = [row[0] for row in sql('SELECT email FROM users')]
    assert emails == ['a@example.com']


def test_a_deleted_account_cannot_use_its_token_any_more(client, user, auth):
    _email, token = user()
    headers = auth(token)

    client.delete('/api/me', headers=headers)

    assert client.get('/api/me', headers=headers).status_code == 404
    assert client.get('/api/me/export', headers=headers).status_code == 404


def test_delete_account_needs_a_token(client):
    assert client.delete('/api/me').status_code == 401
