"""Signup: the field rules and the duplicate-email guard.

A validation test also asserts that nothing was written, because "400 but the
row is still there" is exactly the kind of bug a happy-path test misses.
"""
from datetime import date, timedelta


def test_valid_signup_creates_the_user(register, sql, client, auth):
    response = register()

    assert response.status_code == 201
    body = response.get_json()
    assert body['user']['email'] == 'test.user@example.com'
    assert body['user']['name'] == 'Test User'

    rows = sql('SELECT name, email, password FROM users')
    assert len(rows) == 1
    name, email, stored = rows[0]
    assert (name, email) == ('Test User', 'test.user@example.com')
    # bcrypt, never the plain text.
    assert stored.startswith('$2') and len(stored) == 60

    # The token returned by signup is a usable session.
    me = client.get('/api/me', headers=auth(body['token']))
    assert me.status_code == 200
    assert me.get_json()['email'] == 'test.user@example.com'


def test_duplicate_email_is_refused(register, sql):
    assert register().status_code == 201

    response = register()

    assert response.status_code == 409
    assert sql('SELECT count(*) FROM users')[0][0] == 1


def test_email_is_matched_case_insensitively(register, sql):
    """A@b.com and a@b.com must not become two accounts that cannot log in
    to each other."""
    register(email='Test.User@Example.COM')

    response = register(email='test.user@example.com')

    assert response.status_code == 409
    assert sql('SELECT count(*) FROM users')[0][0] == 1


def test_weak_passwords_are_refused(register, sql):
    cases = (
        ('Sh0rt!', 'at least 8'),
        ('alllowercase1!', 'uppercase'),
        ('ALLUPPERCASE!', 'number'),
        ('NoSpecials123', 'special'),
    )
    for bad, needle in cases:
        response = register(password=bad)
        assert response.status_code == 400, bad
        assert needle in response.get_json()['errors']['password'], bad

    assert sql('SELECT count(*) FROM users')[0][0] == 0


def test_bad_pincode_is_refused(register, sql):
    for bad in ('12345', '012345', 'abcdef'):
        response = register(pincode=bad)
        assert response.status_code == 400, bad
        assert 'pincode' in response.get_json()['errors'], bad

    assert sql('SELECT count(*) FROM users')[0][0] == 0


def test_other_fields_are_validated_too(register, sql):
    cases = {
        'name': ('A', 'at least 2'),
        'email': ('not-an-email', 'valid email'),
        'contact': ('12345', 'mobile'),
        'gender': ('Robot', 'gender'),
        'marital_status': ('Complicated', 'marital'),
        'residential_address': ('short', 'at least 10'),
        'dob': ('not-a-date', 'date'),
    }
    for field, (bad, needle) in cases.items():
        response = register(**{field: bad})
        assert response.status_code == 400, field
        errors = response.get_json()['errors']
        assert field in errors, (field, errors)
        assert needle in errors[field].lower(), (field, errors[field])

    assert sql('SELECT count(*) FROM users')[0][0] == 0


def test_age_must_be_between_18_and_120(register):
    minor = date(date.today().year - 10, 1, 1).isoformat()
    future = (date.today() + timedelta(days=1)).isoformat()
    ancient = date(1800, 1, 1).isoformat()

    assert '18' in register(dob=minor).get_json()['errors']['dob']
    assert 'future' in register(dob=future).get_json()['errors']['dob']
    assert 'valid' in register(dob=ancient).get_json()['errors']['dob']
