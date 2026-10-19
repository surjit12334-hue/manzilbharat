"""The password reset flow: happy path, expiry, single use.

No mail is sent - conftest disables the provider - so the token is minted with
the app's own create_reset_token() instead of reading it out of an inbox.
"""
from datetime import datetime, timedelta

from backend.app import create_reset_token

EMAIL = 'reset.me@example.com'
NEW_PASSWORD = 'N3w-Password!'


def test_reset_changes_the_password_and_the_token_cannot_be_reused(
        client, register, password):
    register(email=EMAIL)
    token = create_reset_token(EMAIL)

    response = client.post('/api/reset-password',
                           json={'token': token, 'new_password': NEW_PASSWORD})
    assert response.status_code == 200

    # The old password no longer works, the new one does.
    assert client.post('/api/login',
                       json={'email': EMAIL, 'password': password}).status_code == 401
    assert client.post('/api/login',
                       json={'email': EMAIL, 'password': NEW_PASSWORD}).status_code == 200

    # Redeemed in the same transaction as the password change, so a replay is
    # refused even on another worker.
    again = client.post('/api/reset-password',
                        json={'token': token, 'new_password': 'Other-Pass1!'})
    assert again.status_code == 400
    assert client.post('/api/login',
                       json={'email': EMAIL, 'password': NEW_PASSWORD}).status_code == 200


def test_expired_token_is_refused(client, register, sql):
    register(email=EMAIL)
    token = create_reset_token(EMAIL)

    # The token itself is still signed and unexpired, so this exercises the
    # server-side row check specifically.
    sql('UPDATE reset_tokens SET exp = %s', (datetime.utcnow() - timedelta(minutes=1),))

    response = client.post('/api/reset-password',
                           json={'token': token, 'new_password': NEW_PASSWORD})

    assert response.status_code == 400
    assert 'expired' in response.get_json()['error'].lower()


def test_unknown_or_missing_token_is_refused(client):
    for token in ('garbage.token', '', 'eyJhIjoxfQ.deadbeef'):
        response = client.post('/api/reset-password',
                               json={'token': token, 'new_password': NEW_PASSWORD})
        assert response.status_code == 400, token


def test_reset_enforces_the_password_policy(client, register):
    register(email=EMAIL)
    token = create_reset_token(EMAIL)

    response = client.post('/api/reset-password',
                           json={'token': token, 'new_password': 'weak'})

    assert response.status_code == 400
    assert '8 characters' in response.get_json()['error']


def test_reset_for_a_deleted_account_is_404(client, register, sql):
    register(email=EMAIL)
    token = create_reset_token(EMAIL)
    sql('DELETE FROM users')

    response = client.post('/api/reset-password',
                           json={'token': token, 'new_password': NEW_PASSWORD})

    assert response.status_code == 404


def test_forgot_password_is_honest_when_no_mail_provider_is_configured(client, register):
    """conftest switches email off. An explicit 500 beats claiming a link was
    sent when nothing could have been."""
    register(email=EMAIL)

    response = client.post('/api/forgot-password', json={'email': EMAIL})

    assert response.status_code == 500
    assert 'not configured' in response.get_json()['error']
