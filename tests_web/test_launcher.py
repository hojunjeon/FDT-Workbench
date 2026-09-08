"""Real-process lifecycle tests. No mock server or synthetic shutdown callback."""
from __future__ import annotations
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import httpx
import psutil
import pytest

from launcher import listen_socket
from tests_web.conftest import ROOT


def free_port():
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close();return port


def start(tmp_path: Path, port=None, directory=None):
    port=port or free_port();directory=directory or tmp_path/'runtime'
    log=(tmp_path/f'server-{port}.log').open('w')
    process=subprocess.Popen([sys.executable,str(ROOT/'launcher.py'),'--no-install','--no-browser','--port',str(port),'--data-dir',str(directory)],
                             cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=(os.name!='nt'))
    url=f'http://127.0.0.1:{port}'
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        if process.poll() is not None:
            log.close();raise AssertionError((tmp_path/f'server-{port}.log').read_text())
        try:
            if httpx.get(url+'/api/health',timeout=.3).status_code==200:
                return process,url,port,log
        except httpx.HTTPError:
            pass
        time.sleep(.05)
    process.kill();process.wait();log.close();raise AssertionError('server readiness timeout')


def stop(process, log):
    if process.poll() is None:
        process.send_signal(signal.SIGINT if os.name!='nt' else signal.CTRL_C_EVENT)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill();process.wait();raise
    log.close()


def alive(pid):
    try:
        return psutil.Process(pid).status()!=psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.mark.skipif(os.name=='nt', reason='Windows console Ctrl+C must be checked from an interactive BAT window.')
def test_ctrl_c_idle_and_same_port_restart(tmp_path):
    p,url,port,log=start(tmp_path)
    try:
        assert httpx.get(url+'/').status_code==200
        p.send_signal(signal.SIGINT)
        assert p.wait(timeout=8)==0
        # Bind reuse rather than only "process exited" proves the serving socket is gone.
        new,url2,_,log2=start(tmp_path,port=port)
        try:
            assert httpx.get(url2+'/api/health').json()['pid']==new.pid
        finally:
            stop(new,log2)
    finally:
        stop(p,log)


@pytest.mark.skipif(os.name=='nt', reason='Windows console event delivery is an interactive acceptance test.')
def test_ctrl_c_during_numeric_job_stops_entire_process_tree(tmp_path):
    p,url,port,log=start(tmp_path)
    try:
        with httpx.Client(base_url=url,timeout=10) as client:
            client.headers['X-Workbench-Token']=client.get('/api/config').json()['token']
            raw=(ROOT/'data/demo/consumer_001.csv').read_bytes()
            upload=client.post('/api/uploads',files={'files':('input.csv',raw,'text/csv')}).json()
            pending=client.post('/api/twins',json={'upload_id':upload['upload_id'],'name':'Shutdown test'}).json()
            assert pending['status']=='running'
            children=psutil.Process(p.pid).children(recursive=True)
            assert children, 'spawn worker was not created'
            # Send SIGINT to the console-equivalent group, not just to the Python parent.
            os.killpg(p.pid,signal.SIGINT)
            assert p.wait(timeout=8)==0
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and any(alive(c.pid) for c in children):
                time.sleep(.1)
            assert not any(alive(c.pid) for c in children)
            assert not list((tmp_path/'runtime'/'jobs').iterdir())
            assert not (tmp_path/'runtime'/'uploads').exists()
            assert not list((tmp_path/'runtime'/'twins').iterdir())
    finally:
        stop(p,log)


@pytest.mark.skipif(os.name=='nt', reason='Noninteractive Windows terminal lifecycle not available.')
def test_duplicate_data_directory_rejected_without_killing_first_server(tmp_path):
    p,url,port,log=start(tmp_path)
    try:
        other=subprocess.run([sys.executable,str(ROOT/'launcher.py'),'--no-install','--no-browser','--port',str(free_port()),'--data-dir',str(tmp_path/'runtime')],cwd=ROOT,capture_output=True,text=True,timeout=10)
        assert other.returncode==1
        assert '이미 실행 중' in other.stdout
        assert httpx.get(url+'/api/health').json()['pid']==p.pid
    finally:
        stop(p,log)


def test_port_is_not_stolen():
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
    port=listener.getsockname()[1]
    try:
        with pytest.raises(RuntimeError,match='포트'):
            listen_socket(port)
    finally:
        listener.close()


def test_batch_is_foreground_and_location_safe():
    content=(ROOT/'START.bat').read_bytes()
    assert b'\r\n' in content
    text=content.decode('ascii')
    assert 'cd /d "%~dp0"' in text
    assert '".venv\\Scripts\\python.exe" launcher.py %*' in text
    assert 'taskkill' not in text.lower()
    assert 'start /b' not in text.lower()
    assert 'powershell' not in text.lower()
    assert 'activate' not in text.lower()


def test_launcher_rejects_invalid_port():
    p=subprocess.run([sys.executable,str(ROOT/'launcher.py'),'--no-install','--no-browser','--port','-1'],cwd=ROOT,capture_output=True,text=True,timeout=10)
    assert p.returncode==1
    assert 'Port must be' in p.stdout


@pytest.mark.skipif(os.name=='nt', reason='Requires a real Unix signal process group; Windows acceptance is documented separately.')
def test_ctrl_c_while_mode_running_preserves_committed_twin(tmp_path):
    import json
    p,url,port,log=start(tmp_path)
    try:
        with httpx.Client(base_url=url,timeout=15) as client:
            client.headers['X-Workbench-Token']=client.get('/api/config').json()['token']
            upload=client.post('/api/uploads',files={'files':('input.csv',(ROOT/'data/demo/consumer_001.csv').read_bytes(),'text/csv')}).json()
            body={'upload_id':upload['upload_id'],'name':'Persistent QA', 'snapshot':json.loads((ROOT/'examples/snapshot_001.json').read_text())}
            job=client.post('/api/twins',json=body).json()
            deadline=time.monotonic()+20
            while job['status']=='running' and time.monotonic()<deadline:
                time.sleep(.1);job=client.get('/api/jobs/'+job['id']).json()
            assert job['status']=='succeeded',job
            record=job['twin']['id']
            request=json.loads((ROOT/'examples/requests/optimize.json').read_text())
            request['paths']=100
            run=client.post('/api/twins/'+record+'/runs',json=request).json()
            assert run['status']=='running'
            children=psutil.Process(p.pid).children(recursive=True)
            os.killpg(p.pid,signal.SIGINT)
            assert p.wait(timeout=8)==0
            time.sleep(.2)
            assert not any(alive(c.pid) for c in children)
        new,url2,_,log2=start(tmp_path,port=port)
        try:
            assert httpx.get(url2+'/api/twins/'+record).status_code==200
        finally:
            stop(new,log2)
    finally:
        stop(p,log)
