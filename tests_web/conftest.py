from pathlib import Path
import time
import pytest
from fastapi.testclient import TestClient
from workbench.app import create_app
from fdt.util import read_json

ROOT = Path(__file__).resolve().parents[1]


def wait(client, job_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get('/api/jobs/' + job_id)
        assert response.status_code == 200, response.text
        job = response.json()
        if job['status'] != 'running':
            return job
        time.sleep(0.03)
    raise AssertionError('Job timeout: ' + job_id)


def upload(client, id_='001'):
    path = ROOT / 'data/demo' / ('consumer_' + id_ + '.csv')
    response = client.post('/api/uploads', files={'files': (path.name, path.read_bytes(), 'text/csv')})
    assert response.status_code == 200, response.text
    return response.json()


def build(client, id_='001', with_snapshot=True, name='QA Twin'):
    data = upload(client, id_)
    body = {'upload_id': data['upload_id'], 'name': name}
    if with_snapshot:
        body['snapshot'] = read_json(ROOT / 'examples' / ('snapshot_' + id_ + '.json'))
    response = client.post('/api/twins', json=body)
    assert response.status_code == 202, response.text
    job = wait(client, response.json()['id'])
    assert job['status'] == 'succeeded', job
    return job['twin'], client.get(job['result_url']).json()


@pytest.fixture(scope='module')
def web(tmp_path_factory):
    root = tmp_path_factory.mktemp('web-suite')
    app = create_app(root, test_hosts={'testserver'})
    with TestClient(app) as client:
        client.headers['X-Workbench-Token'] = client.get('/api/config').json()['token']
        yield client


@pytest.fixture(scope='module')
def twins(web):
    return {id_: build(web, id_)[0] for id_ in ('001', '002', '003', '004')}
