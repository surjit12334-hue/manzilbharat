"""GET /api/transport - curated metro/rail/airport info for the demo cities."""
import json
import os

import backend.app as app_module


def test_transport_json_covers_all_five_demo_cities():
    with open(os.path.join(app_module.PROJECT_ROOT, 'data', 'transport.json'), encoding='utf-8') as f:
        table = json.load(f)
    assert set(table.keys()) == {'jaipur', 'goa', 'delhi', 'manali', 'varanasi'}
    for data in table.values():
        assert data.get('label')
        assert 'metro' in data and 'rail' in data and 'airport' in data
        assert isinstance(data['metro'], list)
        assert isinstance(data['rail'], list)
        assert isinstance(data['airport'], list)
        # Goa and Manali have no metro - but others should have some info
        assert len(data['rail']) >= 1
        assert len(data['airport']) >= 1


def test_transport_endpoint_returns_data(client):
    body = client.get('/api/transport?city=jaipur').get_json()
    assert body['city'] == 'jaipur'
    assert body['source'] == 'curated'
    assert len(body['metro']) >= 1
    assert body['metro'][0]['name']
    assert len(body['rail']) >= 1
    assert len(body['airport']) >= 1


def test_transport_case_insensitive(client):
    body = client.get('/api/transport?city=DELHI').get_json()
    assert body['city'] == 'delhi'


def test_transport_unknown_city_404_with_available_list(client):
    r = client.get('/api/transport?city=shillong')
    b = r.get_json()
    assert r.status_code == 404
    assert sorted(b['available']) == ['delhi', 'goa', 'jaipur', 'manali', 'varanasi']


def test_transport_missing_city_400(client):
    r = client.get('/api/transport')
    assert r.status_code == 400


def test_transport_path_traversal_rejected(client):
    for hostile in ['../../.env', '/etc/passwd', 'jaipur.json']:
        r = client.get('/api/transport', query_string={'city': hostile})
        assert r.status_code in (400, 404)
