from pathlib import Path
import copy
import json
import time
import pytest
from fastapi.testclient import TestClient
from fdt.engine import Engine
from fdt.store import TwinStore
from fdt.util import read_json, validate
from workbench.app import create_app, MAX_BODY
from tests_web.conftest import ROOT, wait, upload, build


def test_health_and_static(web):
    assert web.get('/api/health').json()['status'] == 'ok'
    page = web.get('/')
    assert page.status_code == 200 and 'KeyFin' in page.text
    assert "script-src 'self'" in page.headers['content-security-policy']
    assert web.get('/static/app.js').status_code == 200
    assert web.get('/static/charts.js').status_code == 200
    assert web.get('/static/app.css').status_code == 200
    assert web.get('/docs').status_code == 404


def test_config_uses_engine_contract(web):
    data = web.get('/api/config').json()
    assert len(data['templates']) == 5 and len(data['envelopes']) == 7
    for template in data['templates'].values():
        validate('request', template)
    assert data['limits']['concurrent_jobs'] == 1


@pytest.mark.parametrize('id_', ['001', '002', '003', '004'])
def test_demo_files(web, id_):
    assert web.get(f'/api/demo/{id_}/csv').status_code == 200
    response = web.get(f'/api/demo/{id_}/snapshot')
    validate('snapshot', response.json())
    assert response.json()['source'] == 'USER_ASSUMPTION'


@pytest.mark.parametrize('url', ['/api/demo/999/csv','/api/demo/001/other','/api/twins/not-a-safe-id','/api/jobs/no-job'])
def test_not_found(web, url):
    response = web.get(url)
    assert response.status_code == 404
    assert response.json()['status'] == 'error'


@pytest.mark.parametrize('id_', ['001', '002', '003', '004'])
@pytest.mark.parametrize('mode', ['forecast', 'what_if', 'goal', 'risk', 'optimize'])
def test_four_personas_five_modes_match_direct_engine(web, twins, id_, mode):
    record = twins[id_]
    request = {**read_json(ROOT / 'examples/requests' / (mode + '.json')), 'paths': 20, 'horizon_days': 14}
    response = web.post('/api/twins/' + record['id'] + '/runs', json=request)
    assert response.status_code == 202, response.text
    job = wait(web, response.json()['id'])
    assert job['status'] == 'succeeded', job
    result = web.get(job['result_url']).json()
    validate('result', result)
    assert result['mode'] == mode
    assert result['model']['calibrated'] is False
    stored = TwinStore(web.app.state.data_dir / 'twins' / record['id'] / 'twin.sqlite').load()
    assert result == Engine(stored).run(request)


def test_history_only_never_fabricates_cash(web):
    record, summary = build(web, with_snapshot=False, name='History only')
    assert summary['state']['managed_cash_krw'] is None
    assert record['absolute_cash_ready'] is False
    for mode in ('goal', 'optimize'):
        request = {**read_json(ROOT / 'examples/requests' / (mode + '.json')), 'paths': 20, 'horizon_days': 7}
        job = wait(web, web.post('/api/twins/' + record['id'] + '/runs', json=request).json()['id'])
        result = web.get(job['result_url']).json()
        assert result['status'] == 'insufficient_data'
        assert result['metrics']['terminal_cash_p50_krw']['value'] is None


@pytest.mark.parametrize('payload', [
    {'mode': 'unknown'}, {'mode': 'forecast', 'horizon_days': 0},
    {'mode': 'forecast', 'paths': 2001}, {'mode': 'what_if'}, {'mode': 'goal'},
    {'mode': 'forecast', 'surprise': True}, {'mode': 'risk', 'goal': {'target_krw': 1}},
    {'mode': 'what_if', 'scenario': {'expense_reductions': {'외식': 1.1}}},
    {'mode': 'optimize', 'optimization': {'envelopes': ['외식','쇼핑','기타','교통비'], 'reduction_grid': [0,.1,.2,.3]}}
])
def test_invalid_requests_are_structured(web, twins, payload):
    response = web.post('/api/twins/' + twins['001']['id'] + '/runs', json=payload)
    assert response.status_code == 422, response.text
    assert response.json()['error']['code']


def test_upload_deduplicates(web):
    raw = (ROOT/'data/demo/consumer_001.csv').read_bytes()
    response = web.post('/api/uploads', files=[('files', ('a.csv',raw,'text/csv')),('files',('b.csv',raw,'text/csv'))])
    data = response.json()
    assert data['profile']['duplicates_ignored'] == data['profile']['rows']
    assert data['profile']['input_rows'] == 2 * data['profile']['rows']


def test_mixed_persona_csv_rejected(web):
    response = web.post('/api/uploads', files=[('files',('a.csv',(ROOT/'data/demo/consumer_001.csv').read_bytes(),'text/csv')),('files',('b.csv',(ROOT/'data/demo/consumer_002.csv').read_bytes(),'text/csv'))])
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'MIXED_OR_EMPTY_USER'


@pytest.mark.parametrize('name,content', [('a.txt',b'hello'),('a.csv',b'a,b\n1,2'),('bad.csv',b'\xff\xfe'),('empty.csv',b'')])
def test_bad_csv(web,name,content):
    response = web.post('/api/uploads',files={'files':(name,content,'text/csv')})
    assert response.status_code == 422
    assert response.json()['status'] == 'error'


def test_file_count_limit(web):
    raw=(ROOT/'data/demo/consumer_001.csv').read_bytes()
    response=web.post('/api/uploads',files=[('files',(f'{i}.csv',raw,'text/csv')) for i in range(5)])
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'FILE_LIMIT'


def test_upload_path_is_not_trusted(web):
    raw=(ROOT/'data/demo/consumer_001.csv').read_bytes()
    response=web.post('/api/uploads',files={'files':('../../escape.csv',raw,'text/csv')})
    assert response.status_code == 200
    root=web.app.state.data_dir
    assert not (root.parent/'escape.csv').exists()
    assert list((root/'uploads'/response.json()['upload_id']).glob('input_*.csv'))


def test_invalid_snapshot_does_not_start_job(web):
    data=upload(web)
    response=web.post('/api/twins',json={'upload_id':data['upload_id'],'snapshot':{'as_of':'2026-09-03','accounts':[{'account_id':'a','balance_krw':None}]}})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'SCHEMA_VALIDATION'


def test_build_failure_commits_nothing(web):
    data=upload(web)
    before=len(web.get('/api/twins').json()['twins'])
    body={'upload_id':data['upload_id'],'snapshot':read_json(ROOT/'examples/snapshot_001.json'),'as_of':'2026-08-01'}
    response=web.post('/api/twins',json=body)
    job=wait(web,response.json()['id'])
    assert job['status']=='failed'
    assert job['error']['code']=='FUTURE_SNAPSHOT'
    assert len(web.get('/api/twins').json()['twins'])==before


def test_cancel_and_busy(web,twins):
    id_=twins['001']['id']
    body={**read_json(ROOT/'examples/requests/optimize.json'),'horizon_days':90,'paths':100}
    first=web.post(f'/api/twins/{id_}/runs',json=body).json()
    second=web.post(f'/api/twins/{id_}/runs',json={'mode':'forecast'})
    assert second.status_code==409
    assert web.delete('/api/twins/'+id_).status_code==409
    cancelled=web.post('/api/jobs/'+first['id']+'/cancel').json()
    assert cancelled['status']=='cancelled'
    assert web.get('/api/jobs/'+first['id']+'/result').status_code==422
    assert web.get('/api/twins/'+id_).status_code==200


def test_explicit_delete(web):
    record,_=build(web,name='Delete me')
    response=web.delete('/api/twins/'+record['id'])
    assert response.status_code==200
    assert web.get('/api/twins/'+record['id']).status_code==404


def test_tokens_host_and_origin(web):
    response=web.post('/api/twins',json={},headers={'X-Workbench-Token':''})
    assert response.status_code==403
    response=web.post('/api/twins',json={},headers={'Origin':'https://other.example'})
    assert response.status_code==403
    response=web.get('/api/config',headers={'Host':'evil.example'})
    assert response.status_code==403
    assert web.get('/api/config',headers={'Origin':'null'}).status_code==403
    assert web.get('/api/config',headers={'Origin':'http://testserver'}).status_code==200


def test_oversized_body(web):
    response=web.post('/api/twins',content=b'x'*(MAX_BODY+1),headers={'Content-Type':'application/json'})
    assert response.status_code==413


def test_malformed_json(web):
    response=web.post('/api/twins',content='{"broken":',headers={'Content-Type':'application/json'})
    assert response.status_code==422
    assert response.json()['error']['code']=='INVALID_REQUEST'


def test_clear_uploads_does_not_delete_twins(web,twins):
    count=len(web.get('/api/twins').json()['twins'])
    assert web.delete('/api/uploads').status_code==200
    assert len(web.get('/api/twins').json()['twins'])==count
    assert not list((web.app.state.data_dir/'uploads').iterdir())


def test_restart_retains_twins_removes_temporary_files(tmp_path):
    app=create_app(tmp_path,test_hosts={'testserver'})
    with TestClient(app) as client:
        client.headers['X-Workbench-Token']=client.get('/api/config').json()['token']
        record,_=build(client)
        assert (tmp_path/'uploads').is_dir()
    assert not (tmp_path/'uploads').exists()
    assert not list((tmp_path/'jobs').iterdir())
    app2=create_app(tmp_path,test_hosts={'testserver'})
    with TestClient(app2) as client2:
        assert client2.get('/api/twins/'+record['id']).status_code==200
        assert client2.get('/api/twins').json()['twins'][0]['id']==record['id']


def test_raw_to_form_validation_endpoint(web):
    assert web.post('/api/validate/request',json={'mode':'forecast'}).json()['status']=='valid'
    invalid={'mode':'optimize','optimization':{'reduction_grid':'broken'}}
    response=web.post('/api/validate/request',json=invalid)
    assert response.status_code==422
    assert response.json()['error']['code']=='SCHEMA_VALIDATION'


def test_unicode_build_name_persists_as_data_not_html(web):
    name='<img src=x onerror=alert(1)> 사용자 이름'
    record,summary=build(web,name=name)
    response=web.get('/api/twins/'+record['id']).json()
    assert response['meta']['name']==name
    assert response['summary']['twin_id']==summary['twin_id']
    assert web.delete('/api/twins/'+record['id']).status_code==200


def test_active_job_can_be_rediscovered_after_page_reload(web, twins):
    id_=twins['001']['id']
    payload={**read_json(ROOT/'examples/requests/optimize.json'),'horizon_days':90,'paths':100}
    response=web.post('/api/twins/'+id_+'/runs',json=payload)
    job_id=response.json()['id']
    active=web.get('/api/activity').json()['job']
    assert active['id']==job_id
    assert active['record_id']==id_ and active['request']==payload
    web.post('/api/jobs/'+job_id+'/cancel')
    assert web.get('/api/activity').json()['job'] is None
