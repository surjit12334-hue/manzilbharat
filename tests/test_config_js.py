"""The browser config must never ship the placeholder Google client id.

Google answers "OAuth client was not found" (401 invalid_client) when a page
sends the literal ``YOUR_GOOGLE_CLIENT_ID...``. The deployed site did exactly
that because config.js was checked in with the placeholder while the real id
lived in GOOGLE_CLIENT_ID. /config.js is now generated from that variable, so
the id the browser uses and the id the server verifies cannot drift apart.
"""


def test_config_js_uses_the_configured_client_id(client, monkeypatch):
    import backend.app as app_module

    monkeypatch.setattr(
        app_module, 'GOOGLE_CLIENT_ID', '123-test.apps.googleusercontent.com'
    )

    response = client.get('/config.js')
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == 'application/javascript'
    assert '123-test.apps.googleusercontent.com' in body
    assert 'YOUR_GOOGLE_CLIENT_ID' not in body
    assert response.headers['Cache-Control'] == 'no-store'


def test_config_js_falls_back_to_the_file_when_unset(client, monkeypatch):
    import backend.app as app_module

    monkeypatch.setattr(app_module, 'GOOGLE_CLIENT_ID', None)

    response = client.get('/config.js')

    assert response.status_code == 200
    # The checked-in file carries the real (public) client id, never the
    # placeholder, so even the unconfigured fallback cannot cause
    # invalid_client.
    assert 'YOUR_GOOGLE_CLIENT_ID' not in response.get_data(as_text=True)
