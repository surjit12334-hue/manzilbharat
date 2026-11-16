"""Clean page URLs: /aboutus is the address of aboutus.html.

Every page has ONE canonical address without the .html extension. The old
form 301-redirects to it, so bookmarks, emailed reset links and search results
keep working. These tests prove both halves, plus that the change did not open
a hole in the static-file guard.
"""
import pytest

PAGES = [
    'aboutus', 'admin', 'blog', 'blog-post', 'careers', 'contact',
    'cookiepolicy', 'createaccount', 'helpcenter', 'index', 'login', 'map',
    'mytrips', 'privacypolicy', 'refundpolicy', 'reset-password',
    'terms-and-conditions', 'trip',
]


@pytest.mark.parametrize('name', PAGES)
def test_every_page_has_a_clean_address(client, name):
    url = '/' if name == 'index' else '/' + name
    response = client.get(url)

    assert response.status_code == 200, url
    assert response.mimetype == 'text/html', url


def test_the_old_html_address_redirects_permanently(client):
    response = client.get('/aboutus.html')

    assert response.status_code == 301
    assert response.headers['Location'].endswith('/aboutus')


def test_the_homepage_redirects_to_the_root(client):
    response = client.get('/index.html')

    assert response.status_code == 301
    assert response.headers['Location'].endswith('/')


def test_a_query_string_survives_the_redirect(client):
    response = client.get('/blog-post.html?slug=goa')

    assert response.status_code == 301
    assert response.headers['Location'].endswith('/blog-post?slug=goa')


def test_the_clean_address_carries_the_query_string(client):
    response = client.get('/blog-post?slug=goa')

    assert response.status_code == 200
    # The article itself is rendered by the page from the local JSON, so the
    # server only has to serve the page that knows where to look.
    assert b'blog_posts.json' in response.data


def test_unknown_pages_are_still_404s(client):
    for path in ('/no-such-page', '/no-such-page.html', '/backend/app',
                 '/backend/app.py', '/schema', '/.env'):
        assert client.get(path).status_code == 404, path


def test_static_assets_are_not_shadowed_by_the_page_route(client):
    """The clean-page rule matches /config.js too; it must fall through."""
    config = client.get('/config.js')
    assert config.status_code == 200
    assert b'API_BASE' in config.data
    assert client.get('/icon.png').status_code == 200
    assert client.get('/og-image.jpg').status_code == 200


def test_a_redirect_is_not_counted_as_a_page_view(client, sql):
    client.get('/aboutus.html')

    assert sql('SELECT count(*) FROM page_views')[0][0] == 0


def test_the_clean_address_is_counted_once(client, sql):
    client.get('/aboutus')

    rows = sql('SELECT path, count FROM page_views')
    assert rows == [('/aboutus', 1)]
