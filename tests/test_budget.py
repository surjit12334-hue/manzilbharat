"""The budget estimate that rides along with an itinerary.

The numbers are derived from the plan the visitor actually received, so most
of this file is about arithmetic that must always hold (the heads add up to
the total, integers everywhere) and about the fallback path for cities that
are not in data/city_costs.json. No network, no Gemini.
"""
import json

import pytest

import backend.app as app_module

VALID = {
    'kind': 'itinerary',
    'destination': 'Jaipur',
    'days': 3,
    'budget': 'mid-range',
    'interests': 'forts',
}


class FakeResponse:
    def __init__(self, payload_text):
        self.status_code = 200
        self.text = payload_text

    def json(self):
        return {'candidates': [{'content': {'parts': [{'text': self.text}]}}]}


def fake_gemini(monkeypatch, text):
    monkeypatch.setattr(app_module, 'GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(app_module.requests, 'post', lambda *a, **k: FakeResponse(text))


def plan_for(destination, days=3, budget='mid-range'):
    return {'destination': destination, 'days': days, 'budget': budget, 'interests': 'forts'}


def days_for(costs):
    return [
        {'day': index, 'morning': 'm', 'afternoon': 'a', 'evening': 'e', 'cost': cost}
        for index, cost in enumerate(costs, start=1)
    ]


def test_the_table_ships_the_five_demo_cities_and_a_default():
    with open(app_module.CITY_COSTS_PATH, encoding='utf-8') as handle:
        table = json.load(handle)

    assert set(table['cities']) == {'jaipur', 'goa', 'delhi', 'manali', 'varanasi'}
    assert 'default' in table
    for name, city in {**table['cities'], 'default': table['default']}.items():
        assert set(city['per_day']) == set(app_module.BUDGET_LEVELS), name
        assert set(city['split']) == set(app_module.BUDGET_HEADS), name
        # A split that drifts away from 1.0 would invent or lose money.
        assert abs(sum(city['split'].values()) - 1.0) < 1e-9, name
        assert all(rate > 0 for rate in city['per_day'].values()), name


def test_the_breakdown_always_adds_up_to_the_total():
    # Awkward totals: the rounding remainder has to land somewhere.
    for costs in ([333], [1000, 1000, 1000], [999, 1], [7, 11, 13, 17]):
        estimate = app_module._budget_estimate(plan_for('Jaipur', len(costs)), days_for(costs))

        assert estimate['trip_total'] == sum(costs)
        assert sum(estimate['breakdown'].values()) == estimate['trip_total']
        assert set(estimate['breakdown']) == set(app_module.BUDGET_HEADS)
        assert all(isinstance(value, int) for value in estimate['breakdown'].values())
        assert estimate['per_day'] == sum(costs) // len(costs)


def test_the_split_follows_the_city_table():
    days = days_for([1000, 1000, 1000, 1000])

    estimate = app_module._budget_estimate(plan_for('Goa', 4), days)

    assert estimate['matched'] is True
    assert estimate['city'] == 'Goa'
    # Goa's hand-written split is stay .45 / transport .20 / food .22 / activities .13
    assert estimate['breakdown'] == {
        'stay': 1800, 'transport': 800, 'food': 880, 'activities': 520,
    }


def test_cities_outside_the_table_use_the_fallback_and_say_so():
    estimate = app_module._budget_estimate(plan_for('Shillong'), days_for([1000, 1000, 1000]))

    assert estimate['matched'] is False
    assert estimate['city'] == 'Shillong'
    assert estimate['trip_total'] == 3000
    assert sum(estimate['breakdown'].values()) == 3000


@pytest.mark.parametrize('typed, expected', [
    ('budget', 'budget'),
    ('mid-range', 'mid-range'),
    ('luxury', 'luxury'),
    ('LUXURY', 'luxury'),
    ('  Mid-Range ', 'mid-range'),
    ('cheap and cheerful', 'budget'),
    ('upscale', 'luxury'),
    ('premium', 'luxury'),
    ('something else', 'mid-range'),
    ('', 'mid-range'),
    (None, 'mid-range'),
    ('luxurious', 'luxury'),
])
def test_the_budget_wording_is_normalised(typed, expected):
    assert app_module._budget_level(typed) == expected


def test_a_city_is_matched_case_insensitively():
    estimate = app_module._budget_estimate(plan_for('  jAiPuR '), days_for([100, 100]))

    assert estimate['matched'] is True
    assert estimate['city'] == 'Jaipur, Rajasthan'


def test_a_broken_table_degrades_instead_of_failing(monkeypatch, tmp_path):
    broken = tmp_path / 'city_costs.json'
    broken.write_text('{"cities": "not a dict"}', encoding='utf-8')
    monkeypatch.setattr(app_module, 'CITY_COSTS_PATH', str(broken))
    monkeypatch.setattr(app_module, '_city_costs', lambda: None)

    estimate = app_module._budget_estimate(plan_for('Jaipur'), days_for([500, 500]))

    assert estimate['trip_total'] == 1000
    assert sum(estimate['breakdown'].values()) == 1000
    assert estimate['baseline_per_day'] == 0


def test_a_missing_table_degrades_instead_of_failing(monkeypatch):
    monkeypatch.setattr(app_module, 'CITY_COSTS_PATH', '/nonexistent/city_costs.json')

    estimate = app_module._budget_estimate(plan_for('Jaipur'), days_for([500, 500]))

    assert estimate['trip_total'] == 1000
    assert estimate['matched'] is False
    assert sum(estimate['breakdown'].values()) == 1000


def test_a_hand_edited_split_that_does_not_add_up_is_ignored(monkeypatch, tmp_path):
    # Weights sum to 0.5: taking them at face value would lose half the money.
    silly = tmp_path / 'city_costs.json'
    silly.write_text(json.dumps({
        'default': {'per_day': {'mid-range': 1000},
                    'split': {'stay': 0.25, 'transport': 0.1, 'food': 0.1,
                              'activities': 0.05}},
        'cities': {},
    }), encoding='utf-8')
    monkeypatch.setattr(app_module, 'CITY_COSTS_PATH', str(silly))

    estimate = app_module._budget_estimate(plan_for('Jaipur'), days_for([1000]))

    assert sum(estimate['breakdown'].values()) == 1000
    assert estimate['breakdown']['stay'] == 250  # even 25% each


def test_the_endpoint_returns_the_budget_for_a_fresh_plan(client, monkeypatch, sql):
    fake_gemini(monkeypatch, json.dumps({'days': days_for([2000, 3000])}))

    body = client.post('/api/ai-guide', json={**VALID, 'days': 2}).get_json()

    assert body['cached'] is False
    assert body['budget']['trip_total'] == 5000
    assert body['budget']['per_day'] == 2500
    assert sum(body['budget']['breakdown'].values()) == 5000


def test_the_budget_is_recomputed_for_a_cached_plan(client, monkeypatch):
    fake_gemini(monkeypatch, json.dumps({'days': days_for([2000, 3000])}))
    assert client.post('/api/ai-guide', json={**VALID, 'days': 2}).status_code == 200

    monkeypatch.setattr(app_module, 'GEMINI_API_KEY', '')
    body = client.post('/api/ai-guide', json={**VALID, 'days': 2}).get_json()

    assert body['cached'] is True
    # Same plan, same numbers - the estimate is not part of the cached blob.
    assert body['budget']['trip_total'] == 5000
    assert sum(body['budget']['breakdown'].values()) == 5000


def test_the_level_in_the_request_changes_the_baseline_not_the_total(client, monkeypatch):
    fake_gemini(monkeypatch, json.dumps({'days': days_for([1000, 1000])}))

    budget = client.post('/api/ai-guide', json={**VALID, 'days': 2, 'budget': 'budget'}).get_json()
    luxury = client.post('/api/ai-guide', json={**VALID, 'days': 2, 'budget': 'luxury'}).get_json()

    assert budget['budget']['level'] == 'budget'
    assert luxury['budget']['level'] == 'luxury'
    assert budget['budget']['baseline_per_day'] < luxury['budget']['baseline_per_day']
    # The visitor's own day costs drive the total either way.
    assert budget['budget']['trip_total'] == luxury['budget']['trip_total'] == 2000
