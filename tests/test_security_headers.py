"""Security headers (Phase 5 step 3).

Every response the app sends carries the baseline headers, the CSP names the
origins the pages really use, and Strict-Transport-Security only shows up on a
request that came in over HTTPS (sending it on a plain http:// page would pin
a developer to a scheme the local server does not speak).
"""


def test_pages_carry_the_baseline_headers(client):
    response = client.get('/index.html')

    assert response.status_code == 200
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['X-Frame-Options'] == 'SAMEORIGIN'
    assert response.headers['Referrer-Policy'] == 'strict-origin-when-cross-origin'
    assert response.headers['Permissions-Policy']
    assert 'Content-Security-Policy' in response.headers


def test_api_responses_carry_the_headers_too(client):
    response = client.get('/api/health')

    assert response.status_code == 200
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert 'Content-Security-Policy' in response.headers


def test_the_csp_locks_the_dangerous_things_down(client):
    csp = client.get('/index.html').headers['Content-Security-Policy']

    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "form-action 'self'" in csp


def test_the_csp_allows_the_origins_the_pages_use(client):
    """A CSP that forgets unpkg.com would break the map, and one that forgets
    accounts.google.com would break Google Sign-In."""
    csp = client.get('/map.html').headers['Content-Security-Policy']

    assert 'https://unpkg.com' in csp
    assert 'https://tile.openstreetmap.org' in csp
    assert 'https://accounts.google.com' in csp
    assert 'https://fonts.googleapis.com' in csp
    assert 'https://cdnjs.cloudflare.com' in csp


def test_the_csp_allows_the_razorpay_checkout(client):
    """Phase 5 step 2 loads checkout.js and frames the hosted checkout; the
    API host is there because the script talks to it from the page."""
    csp = client.get('/mytrips.html').headers['Content-Security-Policy']
    directives = {
        part.strip().split(' ', 1)[0]: part
        for part in csp.split(';')
        if part.strip()
    }

    assert 'https://checkout.razorpay.com' in directives['script-src']
    assert 'https://checkout.razorpay.com' in directives['frame-src']
    assert 'https://api.razorpay.com' in directives['connect-src']


def test_hsts_is_sent_over_https(client):
    response = client.get('/index.html', headers={'X-Forwarded-Proto': 'https'})

    assert 'max-age=31536000' in response.headers['Strict-Transport-Security']


def test_hsts_is_not_sent_over_plain_http(client):
    """A local http:// page must not be pinned to https."""
    response = client.get('/index.html')

    assert 'Strict-Transport-Security' not in response.headers


def test_api_responses_are_still_not_cached(client):
    response = client.get('/api/health')

    assert response.headers['Cache-Control'] == 'no-store'


def test_static_assets_keep_their_cache_policy(client):
    """The headers must not have broken the asset caching."""
    image = client.get('/icon.png')
    script = client.get('/auth.js')

    assert image.headers['Cache-Control'] == 'public, max-age=31536000, immutable'
    assert script.headers['Cache-Control'] == 'public, max-age=604800'
