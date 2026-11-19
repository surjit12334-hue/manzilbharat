"""GET /api/places - the curated demo-city data behind the trip page.

Two things matter here: the five demo files actually satisfy the promise the
roadmap made (5-8 hotels, 5 things to do, 3 eateries, real named places), and
the endpoint can never be talked into reading a file outside data/.
"""
import json
import os

import pytest

import backend.app as app_module

DEMO_CITIES = ('jaipur', 'goa', 'delhi', 'manali', 'varanasi')
HOTEL_CATEGORIES = {'hostel', 'budget', 'mid-range', 'luxury'}


def city_file(city):
    with open(os.path.join(app_module.PLACES_DIR, city + '.json'), encoding='utf-8') as handle:
        return json.load(handle)


@pytest.mark.parametrize('city', DEMO_CITIES)
def test_every_demo_city_ships_a_usable_list(city):
    data = city_file(city)

    assert data['city'] == city
    assert data['label']
    assert data['tagline']
    # The roadmap's own numbers: 5-8 hotels, 5 things to do, 3 eateries.
    assert 5 <= len(data['hotels']) <= 8
    assert len(data['things_to_do']) >= 5
    assert len(data['eateries']) >= 3


@pytest.mark.parametrize('city', DEMO_CITIES)
def test_hotel_entries_are_complete_and_priced(city):
    for hotel in city_file(city)['hotels']:
        assert hotel['name'].strip()
        assert hotel['area'].strip()
        assert hotel['note'].strip()
        assert hotel['category'] in HOTEL_CATEGORIES
        # A rupee range, not a bare number - it is an indicative band.
        assert hotel['price_per_night'].startswith('₹')
        assert '-' in hotel['price_per_night']


@pytest.mark.parametrize('city', DEMO_CITIES)
def test_things_and_eateries_are_complete(city):
    data = city_file(city)

    for place in data['things_to_do']:
        assert place['name'].strip()
        assert place['area'].strip()
        assert place['duration'].strip()
        assert place['note'].strip()

    for eatery in data['eateries']:
        assert eatery['name'].strip()
        assert eatery['area'].strip()
        assert eatery['must_try'].strip()
        assert eatery['price_for_two'].startswith('₹')
        assert eatery['note'].strip()


def test_the_price_sheet_and_the_places_files_cover_the_same_cities():
    with open(app_module.CITY_COSTS_PATH, encoding='utf-8') as handle:
        priced = set(json.load(handle)['cities'])

    assert priced == set(DEMO_CITIES)


def test_the_endpoint_returns_the_places(client):
    body = client.get('/api/places?city=jaipur').get_json()

    assert body['city'] == 'jaipur'
    assert body['label'] == 'Jaipur, Rajasthan'
    assert body['source'] == 'curated'
    assert len(body['hotels']) == 7
    assert len(body['things_to_do']) == 6
    assert len(body['eateries']) == 4
    assert body['hotels'][0]['name']


@pytest.mark.parametrize('typed', ['Goa', 'GOA', ' goa '])
def test_the_city_lookup_is_case_and_space_insensitive(client, typed):
    body = client.get('/api/places', query_string={'city': typed}).get_json()

    assert body['city'] == 'goa'
    assert body['label'] == 'Goa'


def test_an_unknown_city_is_an_honest_404_with_the_cities_that_exist(client):
    response = client.get('/api/places?city=shillong')
    body = response.get_json()

    assert response.status_code == 404
    assert body['city'] == 'shillong'
    assert sorted(body['available']) == sorted(DEMO_CITIES)


def test_a_missing_city_is_a_400(client):
    response = client.get('/api/places')

    assert response.status_code == 400
    assert 'city' in response.get_json()['error'].lower()


@pytest.mark.parametrize('hostile', [
    '../../.env',
    '..%2f..%2f.env',
    'jaipur/../../.env',
    'city_costs',
    '/etc/passwd',
    'jaipur.json',
    'J A I P U R',
    '.env',
    'jaipur!',
])
def test_the_slug_can_never_reach_outside_the_data_directory(client, hostile):
    response = client.get('/api/places', query_string={'city': hostile})

    assert response.status_code in (400, 404), hostile
    assert response.get_json()['city'] == hostile
    # Never a 200: none of these is a demo city file.
    assert response.status_code != 200


def test_a_broken_city_file_is_a_404_not_a_500(client, monkeypatch, tmp_path):
    broken = tmp_path / 'nowhere.json'
    broken.write_text('{ not json', encoding='utf-8')
    monkeypatch.setattr(app_module, 'PLACES_DIR', str(tmp_path))

    response = client.get('/api/places?city=nowhere')

    assert response.status_code == 404


def test_an_unreadable_city_file_is_a_404_not_a_500(client, monkeypatch, tmp_path):
    stray = tmp_path / 'stray.json'
    stray.write_text('"just a string"', encoding='utf-8')
    monkeypatch.setattr(app_module, 'PLACES_DIR', str(tmp_path))

    response = client.get('/api/places?city=stray')

    assert response.status_code == 404


def test_editing_a_city_file_is_picked_up_without_a_restart(client, monkeypatch, tmp_path):
    path = tmp_path / 'jaipur.json'
    path.write_text(json.dumps({'city': 'jaipur', 'label': 'First', 'hotels': [],
                                'things_to_do': [], 'eateries': []}), encoding='utf-8')
    monkeypatch.setattr(app_module, 'PLACES_DIR', str(tmp_path))
    assert client.get('/api/places?city=jaipur').get_json()['label'] == 'First'

    # Rewrite with a new mtime; the mtime cache must notice.
    path.write_text(json.dumps({'city': 'jaipur', 'label': 'Second', 'hotels': [],
                                'things_to_do': [], 'eateries': []}), encoding='utf-8')
    os.utime(path, (os.path.getmtime(path) + 5, os.path.getmtime(path) + 5))

    assert client.get('/api/places?city=jaipur').get_json()['label'] == 'Second'
