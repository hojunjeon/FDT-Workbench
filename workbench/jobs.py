"""Share the existing single-worker lifecycle between coaching and numeric jobs."""
from __future__ import annotations

import logging
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import signal
import time
import uuid

from fdt.errors import FDTError
from fdt.util import write_json
from ._numeric_jobs import JobManager as NumericJobManager, worker

LOG = logging.getLogger(__name__)


def coaching_worker(payload: dict, directory: str) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal.SIG_IGN)
    root = Path(directory)
    try:
        from fdt.coaching import Coach
        from fdt.store import TwinStore
        result = Coach(TwinStore(payload['database']).load()).review(payload['request'], payload.get('commitments', []))
        write_json(root / 'result.tmp', result)
        os.replace(root / 'result.tmp', root / 'result.json')
        packet = {'status': 'succeeded'}
    except FDTError as exc:
        packet = {'status': 'failed', 'error': exc.as_dict()['error']}
    except Exception:
        LOG.exception('Coaching worker failed')
        packet = {'status': 'failed', 'error': {'code': 'WORKER_ERROR',
                  'message': '코칭 계산이 실패했습니다. 실행 창의 로그를 확인하세요.', 'details': {}}}
    write_json(root / 'completion.tmp', packet)
    os.replace(root / 'completion.tmp', root / 'completion.json')


class JobManager(NumericJobManager):
    def submit(self, operation: str, payload: dict) -> dict:
        if operation != 'coach':
            return super().submit(operation, payload)
        self.poll()
        if self.closed:
            raise FDTError('SERVER_STOPPING', '서버를 종료하고 있습니다.')
        if any(j['status'] == 'running' for j in self.jobs.values()):
            raise FDTError('ENGINE_BUSY', '진행 중인 점검을 완료하거나 취소한 뒤 다시 실행하세요.')
        while len(self.jobs) >= 30:
            oldest = next(iter(self.jobs))
            shutil.rmtree(self.root / 'jobs' / oldest, ignore_errors=True)
            del self.jobs[oldest]
        id_ = uuid.uuid4().hex
        directory = self.root / 'jobs' / id_
        directory.mkdir()
        process = mp.get_context('spawn').Process(target=coaching_worker, args=(payload, str(directory)), name='FDT-coach')
        job = {'id': id_, 'operation': operation, 'status': 'running', 'started': time.monotonic(),
               'elapsed_seconds': 0., 'payload': payload, 'process': process}
        try:
            process.start()
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        self.jobs[id_] = job
        return self.public(job)
