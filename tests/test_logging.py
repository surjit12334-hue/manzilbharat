"""Logging and audit trail (Phase 5 step 4).

The rules under test:

* the app writes an audit line for the things that matter - sign-ups,
  sign-ins (successful and failed), admin logins, the consent records, the
  data exports, the account deletions and the admin actions;
* the log never contains a password, a password hash, a session token or a
  reset token. That is the whole point of an audit log;
* a crash is logged with the request that caused it, and the visitor gets a
  plain message instead of a stack trace.
"""

import logging

import pytest


class _LogLines:
    """The log messages written during the test, read on demand.

    A plain string would be a snapshot taken before the request, so the
    fixture hands out this little reader instead.
    """

    def __init__(self, caplog):
        self._caplog = caplog

    def text(self):
        return '\n'.join(record.getMessage() for record in self._caplog.records)

    def clear(self):
        """Forget what was logged so far - used to look at one action only."""
        self._caplog.clear()

    def __contains__(self, needle):
        return needle in self.text()

    def __str__(self):
        return self.text()


@pytest.fixture()
def log_lines(caplog):
    """Every log message written during the test."""
    caplog.set_level(logging.INFO)
    return _LogLines(caplog)


# --------------------------------------------------------------------------
# The audit trail
# --------------------------------------------------------------------------

def test_a_signup_is_logged(client, register, log_lines):
    register(email='audit@example.com')

    assert 'signup' in log_lines
    assert 'audit@example.com' in log_lines
    assert 'dpdp-v1' in log_lines


def test_a_refused_signup_is_logged(client, register, log_lines):
    register(email='')

    assert 'signup refused' in log_lines


def test_a_successful_login_is_logged(client, register, log_lines):
    register(email='audit@example.com')
    log_lines.clear()

    client.post('/api/login',
                json={'email': 'audit@example.com', 'password': 'Str0ng!Pass1'})

    assert 'login' in log_lines
    assert 'audit@example.com' in log_lines


def test_a_failed_login_is_logged(client, register, log_lines):
    register(email='audit@example.com')
    log_lines.clear()

    client.post('/api/login',
                json={'email': 'audit@example.com', 'password': 'wrong-password'})

    assert 'login failed' in log_lines


def test_an_admin_login_is_logged(client, admin_credentials, log_lines):
    email, password = admin_credentials

    client.post('/api/admin/login', json={'email': email, 'password': password})

    assert 'admin login' in log_lines


def test_a_failed_admin_login_is_logged(client, admin_credentials, log_lines):
    email, _password = admin_credentials

    client.post('/api/admin/login', json={'email': email, 'password': 'nope'})

    assert 'admin login failed' in log_lines


def test_the_consent_is_logged(client, user, auth, log_lines):
    _email, token = user()
    log_lines.clear()

    client.post('/api/me/consent', json={'version': 'dpdp-v1'}, headers=auth(token))

    assert 'consent' in log_lines


def test_a_data_export_is_logged(client, user, auth, log_lines):
    _email, token = user()
    log_lines.clear()

    client.get('/api/me/export', headers=auth(token))

    assert 'exported their data' in log_lines


def test_an_account_deletion_is_logged(client, user, auth, log_lines):
    _email, token = user()
    log_lines.clear()

    client.delete('/api/me', headers=auth(token))

    assert 'deleted their account' in log_lines


def test_an_admin_action_is_logged(client, user, admin_headers, log_lines):
    _email, _token = user()
    log_lines.clear()

    client.delete('/api/users/1', headers=admin_headers())

    assert 'admin: deleted user 1' in log_lines


# --------------------------------------------------------------------------
# What must NEVER be in the log
# --------------------------------------------------------------------------

def test_the_password_never_reaches_the_log(client, register, log_lines):
    register(password='Unmistakable!Pass42')

    assert 'Unmistakable!Pass42' not in log_lines


def test_the_password_hash_never_reaches_the_log(client, register, log_lines, sql):
    register(password='Unmistakable!Pass42')

    password_hash = sql('SELECT password FROM users')[0][0]
    assert password_hash not in log_lines


def test_the_session_token_never_reaches_the_log(client, register, log_lines):
    response = register()

    assert response.get_json()['token'] not in log_lines


def test_the_login_token_never_reaches_the_log(client, register, log_lines):
    register()
    log_lines.clear()

    response = client.post('/api/login',
                            json={'email': 'test.user@example.com',
                                  'password': 'Str0ng!Pass1'})

    assert response.get_json()['token'] not in log_lines


def test_the_reset_token_never_reaches_the_log(client, register, log_lines):
    """The reset link is a one-time key to the account - it must stay out of
    the log file."""
    register()

    client.post('/api/forgot-password', json={'email': 'test.user@example.com'})

    assert 'reset-password.html?token=' not in log_lines


# --------------------------------------------------------------------------
# Crashes
# --------------------------------------------------------------------------

def test_a_crash_is_logged_with_the_request_that_caused_it(client, monkeypatch,
                                                           caplog):
    import backend.app as app_module

    caplog.set_level(logging.INFO)

    def boom(*args, **kwargs):
        raise RuntimeError('the disk caught fire')

    monkeypatch.setattr(app_module, 'send_from_directory', boom)
    # Without this, Flask re-raises the exception instead of turning it into
    # a 500 response, so the error handler never runs.
    monkeypatch.setitem(app_module.app.config, 'PROPAGATE_EXCEPTIONS', False)

    response = client.get('/')

    assert response.status_code == 500
    messages = '\n'.join(record.getMessage() for record in caplog.records)
    assert '500 on GET /' in messages


def test_a_crash_does_not_show_the_traceback_to_the_visitor(client, monkeypatch):
    import backend.app as app_module

    def boom(*args, **kwargs):
        raise RuntimeError('the disk caught fire')

    monkeypatch.setattr(app_module, 'send_from_directory', boom)
    monkeypatch.setitem(app_module.app.config, 'PROPAGATE_EXCEPTIONS', False)

    response = client.get('/')

    assert response.status_code == 500
    body = response.get_data(as_text=True)
    assert 'the disk caught fire' not in body
    assert 'Traceback' not in body
    assert 'Something went wrong' in body


def test_a_crash_on_an_api_call_answers_json(flask_app):
    """On an API path the handler answers JSON, not an HTML page."""
    from backend.app import handle_internal_error

    with flask_app.test_request_context('/api/bookings', method='POST'):
        body, status = handle_internal_error(RuntimeError('boom'))

    assert status == 500
    assert body.get_json()['error']
    assert 'boom' not in body.get_data(as_text=True)
