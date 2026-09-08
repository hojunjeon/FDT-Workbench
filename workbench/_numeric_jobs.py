"""Bounded, cancellable jobs. Only the parent commits completed Twin builds."""
from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import signal
import time
import uuid

from fdt.errors import FDTError
from fdt.util import read_json, write_json

LOG = logging.getLogger(__name__)


def worker(operation: str, payload: dict, directory: str) -> None:
    # On Windows the console broadcasts Ctrl+C to the process group. The server
    # owns cancellation, so the numeric worker must not race the parent cleanup.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal.SIG_IGN)
    root = Path(directory)
    try:
        from fdt.engine import Engine
        from fdt.model import Twin
        from fdt.store import TwinStore
        if operation == 'build':
            twin = Twin.from_csv(payload['files'], snapshot=payload.get('snapshot'), as_of=payload.get('as_of'))
            TwinStore(root / 'built.sqlite').create(twin)
            result = twin.inspect()
        else:
            result = Engine(TwinStore(payload['database']).load()).run(payload['request'])
        write_json(root / 'result.tmp', result)
        os.replace(root / 'result.tmp', root / 'result.json')
        packet = {'status': 'succeeded'}
    except FDTError as exc:
        packet = {'status': 'failed', **exc.as_dict()}
        packet['status'] = 'failed'
    except Exception:
        LOG.exception('Numeric worker failed')
        packet = {'status': 'failed', 'error': {'code': 'WORKER_ERROR', 'message': '계산 작업이 실패했습니다. 실행 창의 로그를 확인하세요.', 'details': {}}}
    write_json(root / 'completion.tmp', packet)
    os.replace(root / 'completion.tmp', root / 'completion.json')


class JobManager:
    def __init__(self, root: Path, timeout_seconds: float = 300):
        self.root = root
        self.timeout_seconds = timeout_seconds
        self.jobs: dict[str, dict] = {}
        self.closed = False
        (root / 'jobs').mkdir(parents=True, exist_ok=True)
        (root / 'twins').mkdir(parents=True, exist_ok=True)
        # Results are session-local. Persistent Twins are separate and untouched.
        for path in (root / 'jobs').iterdir():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)

    def submit(self, operation: str, payload: dict) -> dict:
        self.poll()
        if self.closed:
            raise FDTError('SERVER_STOPPING', '서버를 종료하고 있습니다.')
        if any(j['status'] == 'running' for j in self.jobs.values()):
            raise FDTError('ENGINE_BUSY', '이미 실행 중인 작업이 있습니다. 완료를 기다리거나 취소하세요.')
        while len(self.jobs) >= 30:
            oldest = next(iter(self.jobs))
            shutil.rmtree(self.root / 'jobs' / oldest, ignore_errors=True)
            del self.jobs[oldest]
        id_ = uuid.uuid4().hex
        directory = self.root / 'jobs' / id_
        directory.mkdir()
        process = mp.get_context('spawn').Process(target=worker, args=(operation, payload, str(directory)), name='FDT-' + operation)
        job = {'id': id_, 'operation': operation, 'status': 'running', 'started': time.monotonic(),
               'elapsed_seconds': 0.0, 'payload': payload, 'process': process}
        try:
            process.start()
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        self.jobs[id_] = job
        return self.public(job)

    def poll(self) -> None:
        for job in list(self.jobs.values()):
            if job['status'] != 'running':
                continue
            job['elapsed_seconds'] = round(time.monotonic() - job['started'], 2)
            proc = job['process']
            if proc.is_alive():
                if job['elapsed_seconds'] > self.timeout_seconds:
                    self._stop(job)
                    job['status'] = 'failed'
                    job['error'] = {'code': 'JOB_TIMEOUT', 'message': '작업 제한 시간(300초)을 초과했습니다. 기간·경로 수·후보 수를 줄이세요.', 'details': {}}
                continue
            proc.join(timeout=0.1)
            directory = self.root / 'jobs' / job['id']
            try:
                if not (directory / 'completion.json').is_file():
                    raise FDTError('WORKER_EXITED', '계산 프로세스가 결과 없이 종료되었습니다.', {'exitcode': proc.exitcode})
                packet = read_json(directory / 'completion.json')
                if packet['status'] != 'succeeded':
                    job['status'] = 'failed'
                    job['error'] = packet['error']
                    continue
                if job['operation'] == 'build':
                    result = read_json(directory / 'result.json')
                    record_id = uuid.uuid4().hex  # UI handle; engine twin_id is preserved inside the result.
                    target = self.root / 'twins' / record_id
                    temp = directory / 'commit'
                    temp.mkdir()
                    os.replace(directory / 'built.sqlite', temp / 'twin.sqlite')
                    meta = {'id': record_id, 'name': job['payload']['name'], 'twin_id': result['twin_id'],
                            'user_id': result['user_id'], 'as_of': result['as_of'], 'revision': result['revision'],
                            'transaction_count': result['audit']['rows'],
                            'absolute_cash_ready': result['state']['absolute_cash_ready'],
                            'source': result['state']['source'], 'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
                    write_json(temp / 'meta.json', meta)
                    os.replace(temp, target)
                    job['twin'] = meta
                job['status'] = 'succeeded'
                job['result_url'] = '/api/jobs/' + job['id'] + '/result'
            except FDTError as exc:
                job['status'] = 'failed'
                job['error'] = exc.as_dict()['error']
            except Exception:
                LOG.exception('Job result commit failed')
                job['status'] = 'failed'
                job['error'] = {'code': 'COMMIT_ERROR', 'message': '결과를 저장하지 못했습니다. 디스크 권한·여유 공간을 확인하세요.', 'details': {}}
            finally:
                proc.close()
                job['process'] = None

    def public(self, job: dict) -> dict:
        return {k: v for k, v in job.items() if k not in ('payload', 'process', 'started')}

    def get(self, id_: str) -> dict:
        self.poll()
        if id_ not in self.jobs:
            raise FDTError('JOB_NOT_FOUND', '작업이 없거나 서버 재시작으로 작업 결과가 만료되었습니다.')
        return self.public(self.jobs[id_])

    def _stop(self, job: dict) -> None:
        proc = job.get('process')
        if proc is not None:
            if proc.is_alive():
                proc.terminate()
            proc.join(timeout=2)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2)
            proc.close()
            job['process'] = None
        job['elapsed_seconds'] = round(time.monotonic() - job['started'], 2)

    def cancel(self, id_: str) -> dict:
        self.get(id_)
        job = self.jobs[id_]
        if job['status'] == 'running':
            self._stop(job)
            job['status'] = 'cancelled'
        return self.public(job)

    def close(self) -> None:
        self.closed = True
        for job in self.jobs.values():
            if job['status'] == 'running':
                self._stop(job)
                job['status'] = 'cancelled'
        # No ephemeral request/response files survive normal shutdown.
        for directory in (self.root / 'jobs').iterdir():
            if directory.is_dir():
                shutil.rmtree(directory, ignore_errors=True)
