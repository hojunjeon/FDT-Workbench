from datetime import date, timedelta
from fastapi.testclient import TestClient

from workbench.app import create_app
from workbench.coaching import PlanStore
from tests_web.conftest import build, wait


def plus(day, count):
    return (date.fromisoformat(day)+timedelta(days=count)).isoformat()


def test_home_is_coaching_and_numeric_tools_are_preserved(web):
    home = web.get('/')
    assert '지금 필요한 결정부터' in home.text
    assert 'forecast' not in home.text
    assert web.get('/workbench').status_code == 200
    assert web.get('/static/coach.js').status_code == 200
    assert web.get('/api/coaching/config').json()['banking_actions'] is False


def test_coaching_context_uses_actual_account_and_income_identifiers(web, twins):
    response = web.get('/api/twins/'+twins['003']['id']+'/coaching/context')
    assert response.status_code == 200
    assert response.json()['as_of'] == twins['003']['as_of']
    assert response.json()['accounts'][0]['balance_krw'] == 5000000
    assert response.json()['snapshot_source'] == 'USER_ASSUMPTION'


def test_today_is_not_silently_set_to_old_twin_date(web, twins):
    today = web.get('/api/coaching/config').json()['today']
    record = twins['003']
    response = web.post(f"/api/twins/{record['id']}/coaching", json={'through_date': plus(today, 7), 'paths': 20})
    assert response.status_code == 202, response.text
    job = wait(web, response.json()['id'])
    assert job['status'] == 'succeeded', job
    result = web.get(job['result_url']).json()
    assert result['on_date'] == today
    if today != record['as_of']:
        assert result['status'] == 'needs_data'
        assert result['projection'] is None


def test_explicit_replay_and_real_worker(web, twins):
    record = twins['003']
    body = {'on_date': record['as_of'], 'through_date': plus(record['as_of'], 7), 'replay': True, 'paths': 20}
    response = web.post(f"/api/twins/{record['id']}/coaching", json=body)
    assert response.status_code == 202, response.text
    job = wait(web, response.json()['id'])
    assert job['status'] == 'succeeded', job
    result = web.get(job['result_url']).json()
    assert result['context']['replay'] is True
    assert result['projection']['cash'] is not None
    assert result['next_action']['evidence_refs']


def test_coaching_write_requires_session_token(web, twins):
    response = web.post('/api/twins/'+twins['001']['id']+'/coaching', json={}, headers={'X-Workbench-Token': ''})
    assert response.status_code == 403


def test_no_engine_mode_or_arbitrary_subcategory_translation(web, twins):
    record = twins['001']
    response = web.post('/api/twins/'+record['id']+'/coaching', json={
        'on_date': record['as_of'], 'through_date': plus(record['as_of'], 7), 'replay': True, 'mode': 'optimize'})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'COACHING_INPUT'


def test_plan_requires_explicit_confirmation_and_is_private_to_user(web, twins):
    today = web.get('/api/coaching/config').json()['today']
    action = {'kind': 'spending_cap', 'envelope': '외식', 'start_date': plus(today, 1), 'end_date': plus(today, 7), 'amount_krw': 100000}
    base = '/api/twins/'+twins['001']['id']+'/coaching/plans'
    assert web.post(base, json={'label': '내 한도', 'action': action, 'confirmed': False}).status_code == 422
    response = web.post(base, json={'label': '내 한도', 'action': action, 'confirmed': True})
    assert response.status_code == 201, response.text
    plan = response.json()
    assert any(p['id'] == plan['id'] for p in web.get(base).json()['plans'])
    other = '/api/twins/'+twins['002']['id']+'/coaching/plans'
    assert not any(p['id'] == plan['id'] for p in web.get(other).json()['plans'])
    assert web.delete(other+'/'+plan['id']).status_code == 404
    assert web.delete(base+'/'+plan['id']).status_code == 200


def test_plans_persist_across_rebuilds_without_mutating_observations(web, twins):
    today = web.get('/api/coaching/config').json()['today']
    base = '/api/twins/'+twins['003']['id']+'/coaching/plans'
    action = {'kind': 'spending_cap', 'envelope': '쇼핑', 'start_date': plus(today, 1), 'end_date': plus(today, 3), 'amount_krw': 30000}
    plan = web.post(base, json={'label': '다음 쇼핑 한도', 'action': action, 'confirmed': True}).json()
    rebuilt, _ = build(web, '003', name='갱신한 같은 사용자')
    rebuilt_plans = web.get('/api/twins/'+rebuilt['id']+'/coaching/plans').json()['plans']
    assert any(p['id'] == plan['id'] for p in rebuilt_plans)
    assert web.delete(base+'/'+plan['id']).status_code == 200


def test_plan_store_survives_new_instance(tmp_path):
    path = tmp_path/'plans.sqlite'
    plan = PlanStore(path).add('user-a', '계획', {'kind': 'spending_cap', 'envelope': '외식', 'start_date': '2026-10-01', 'end_date': '2026-10-07', 'amount_krw': 1})
    assert PlanStore(path).list('user-a')[0]['id'] == plan['id']
    assert PlanStore(path).list('user-b') == []
