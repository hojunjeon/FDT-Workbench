"""Loopback-only web API and static frontend for the unmodified numeric engine."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import time
from typing import Any
import uuid

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from fdt.errors import FDTError
from fdt.ingest import load_csv, REQUIRED
from fdt.mapping import ENVELOPES
from fdt.model import Twin, validate_snapshot
from fdt.store import TwinStore
from fdt.util import read_json, validate, validator, write_json
from .jobs import JobManager

PROJECT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / 'static'
MAX_BODY = 9 * 1024 * 1024
MAX_CSV = 8 * 1024 * 1024
ID = re.compile(r'^[0-9a-f]{32}$')
LOG = logging.getLogger(__name__)
DEMOS = [
    {'id': '001', 'name': '이서준', 'detail': '31세 · 직장인 · 1인 가구'},
    {'id': '002', 'name': '김하늘', 'detail': '24세 · 대학생 · 카페 아르바이트'},
    {'id': '003', 'name': '박정민', 'detail': '42세 · 프리랜서 · 자녀 1명'},
    {'id': '004', 'name': '정미숙', 'detail': '61세 · 은퇴 교사 · 연금 소득'},
]


def failure(code: str, message: str, status: int = 400, details: dict | None = None) -> JSONResponse:
    return JSONResponse(FDTError(code, message, details).as_dict(), status_code=status)


class LocalGuard:
    """Bound request size, block hostile Host/Origin, require same-session write token."""
    def __init__(self, app, token: str, allowed_hosts: set[str]):
        self.app, self.token, self.allowed_hosts = app, token, allowed_hosts

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        headers = {k.decode('latin1').lower(): v.decode('latin1') for k, v in scope['headers']}
        host = headers.get('host', '')
        if host not in self.allowed_hosts:
            await failure('HOST_BLOCKED', '로컬 호스트에서만 사용할 수 있습니다.', 403)(scope, receive, send)
            return
        origin = headers.get('origin')
        if origin and origin != 'http://' + host:
            await failure('ORIGIN_BLOCKED', '다른 사이트에서 보낸 요청을 차단했습니다.', 403)(scope, receive, send)
            return
        if scope['method'] not in ('GET', 'HEAD', 'OPTIONS'):
            if not secrets.compare_digest(headers.get('x-workbench-token', ''), self.token):
                await failure('SESSION_TOKEN_REQUIRED', '페이지를 새로고침한 뒤 다시 시도하세요.', 403)(scope, receive, send)
                return
            try:
                length = int(headers.get('content-length', '0'))
            except ValueError:
                length = MAX_BODY + 1
            if length < 0 or length > MAX_BODY:
                await failure('UPLOAD_TOO_LARGE', '전체 업로드는 8 MB 이하로 제한합니다.', 413)(scope, receive, send)
                return
            chunks, total = [], 0
            while True:
                event = await receive()
                if event['type'] == 'http.disconnect':
                    return
                chunk = event.get('body', b'')
                total += len(chunk)
                if total > MAX_BODY:
                    await failure('UPLOAD_TOO_LARGE', '요청 크기 제한을 초과했습니다.', 413)(scope, receive, send)
                    return
                chunks.append(chunk)
                if not event.get('more_body', False):
                    break
            consumed = False
            async def replay():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {'type': 'http.request', 'body': b''.join(chunks), 'more_body': False}
                return await receive()
            receive_request = replay
        else:
            receive_request = receive
        async def secure_send(event):
            if event['type'] == 'http.response.start':
                event.setdefault('headers', []).extend([
                    (b'x-content-type-options', b'nosniff'),
                    (b'referrer-policy', b'no-referrer'),
                    (b'cache-control', b'no-store'),
                    (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")])
            await send(event)
        await self.app(scope, receive_request, secure_send)


def create_app(data_dir: Path | None = None, port: int = 8765, *, test_hosts: set[str] | None = None) -> FastAPI:
    root = (data_dir or PROJECT / 'workbench_data').resolve()
    token = secrets.token_urlsafe(32)
    allowed = {'127.0.0.1:' + str(port), 'localhost:' + str(port)} | (test_hosts or set())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        root.mkdir(parents=True, exist_ok=True)
        (root / 'uploads').mkdir(exist_ok=True)
        # Staging files are deliberately short-lived; never inferred as current balances.
        for folder in (root / 'uploads').iterdir():
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
        app.state.manager = JobManager(root)
        async def reap():
            while True:
                app.state.manager.poll()
                for path in (root / 'uploads').iterdir():
                    if path.is_dir() and time.time() - path.stat().st_mtime > 86400:
                        shutil.rmtree(path, ignore_errors=True)
                await asyncio.sleep(0.15)
        task = asyncio.create_task(reap())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            app.state.manager.close()
            shutil.rmtree(root / 'uploads', ignore_errors=True)
            LOG.info('All FDT workers stopped. Temporary uploads/results removed.')

    app = FastAPI(title='KeyFin FDT Workbench', version='0.2.0', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(LocalGuard, token=token, allowed_hosts=allowed)
    app.state.data_dir = root

    @app.exception_handler(FDTError)
    async def engine_error(request, exc):
        status = 404 if exc.code.endswith('NOT_FOUND') else 409 if exc.code in ('ENGINE_BUSY', 'SERVER_STOPPING', 'TWIN_IN_USE') else 422
        return JSONResponse(exc.as_dict(), status_code=status)

    @app.exception_handler(RequestValidationError)
    async def request_error(request, exc):
        return failure('INVALID_REQUEST', '요청 형식이 올바르지 않습니다.', 422,
                       {'errors': [{'path': list(e['loc']), 'message': e['msg']} for e in exc.errors()]})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return failure('HTTP_' + str(exc.status_code), str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        LOG.exception('Web API error', exc_info=exc)
        return failure('SERVER_ERROR', '서버 오류입니다. 실행 창의 로그를 확인하세요.', 500)

    def checked_path(kind: str, id_: str) -> Path:
        if not ID.fullmatch(id_):
            raise FDTError(kind.upper() + '_NOT_FOUND', '항목을 찾을 수 없습니다.')
        path = root / kind / id_
        if not path.is_dir():
            raise FDTError(kind.upper() + '_NOT_FOUND', '항목을 찾을 수 없습니다. 다시 업로드하거나 Twin을 선택하세요.')
        return path

    def summary_meta(id_: str) -> dict:
        return read_json(checked_path('twins', id_) / 'meta.json')

    @app.get('/api/health')
    async def health():
        return {'status': 'ok', 'version': '0.2.0', 'engine_version': '0.1.0', 'pid': os.getpid()}

    @app.get('/api/config')
    async def config():
        return {'version': '0.2.0', 'token': token, 'envelopes': ENVELOPES, 'demos': DEMOS,
                'templates': {m: read_json(PROJECT / 'examples' / 'requests' / (m + '.json')) for m in ('forecast', 'what_if', 'goal', 'risk', 'optimize')},
                'request_schema': validator('request').schema, 'snapshot_schema': validator('snapshot').schema,
                'required_csv_columns': sorted(REQUIRED), 'limits': {'upload_mb': 8, 'csv_files': 4, 'rows': 10000, 'concurrent_jobs': 1, 'timeout_seconds': 300}}

    @app.get('/api/demo/{id_}/{kind}')
    async def demo(id_: str, kind: str):
        if id_ not in {d['id'] for d in DEMOS} or kind not in ('csv', 'snapshot'):
            raise FDTError('DEMO_NOT_FOUND', '데모 파일을 찾을 수 없습니다.')
        path = PROJECT / ('data/demo/consumer_' + id_ + '.csv' if kind == 'csv' else 'examples/snapshot_' + id_ + '.json')
        return FileResponse(path, media_type='text/csv; charset=utf-8' if kind == 'csv' else 'application/json', filename=path.name)

    @app.post('/api/uploads')
    async def upload(files: list[UploadFile] = File(...)):
        if not 1 <= len(files) <= 4:
            for file in files:
                await file.close()
            raise FDTError('FILE_LIMIT', '같은 사용자 CSV를 1~4개 선택하세요.')
        if len(list((root / 'uploads').iterdir())) >= 20:
            raise FDTError('STAGING_LIMIT', '임시 업로드가 20개입니다. 페이지의 임시 파일 정리 버튼을 사용하세요.')
        directory = root / 'uploads' / uuid.uuid4().hex
        directory.mkdir()
        total = 0
        try:
            paths = []
            filenames = []
            for i, file in enumerate(files):
                name = (file.filename or '').replace('\\', '/').split('/')[-1]
                if not name.lower().endswith('.csv'):
                    raise FDTError('CSV_ONLY', 'CSV 파일만 업로드할 수 있습니다.')
                data = await file.read(MAX_CSV + 1)
                total += len(data)
                if total > MAX_CSV:
                    raise FDTError('UPLOAD_TOO_LARGE', 'CSV 합계는 8 MB 이하여야 합니다.')
                # Server-generated names prevent filesystem paths supplied by clients.
                path = directory / f'input_{i+1:02}.csv'
                path.write_bytes(data)
                paths.append(path)
                filenames.append(name[:160])
            txs, audit = load_csv(paths)
            profile = {'user_id': txs[0].user_id, 'rows': len(txs), 'input_rows': audit['input_rows'],
                       'duplicates_ignored': audit['duplicates_ignored'], 'from_date': txs[0].date, 'as_of': txs[-1].date,
                       'account_ids': sorted({t.account_id for t in txs if t.account_id} | {t.to_account_id for t in txs if t.to_account_id}),
                       'card_ids': sorted({t.card_id for t in txs if t.card_id}), 'filenames': filenames}
            write_json(directory / 'profile.json', profile)
            return {'upload_id': directory.name, 'profile': profile}
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        finally:
            for file in files:
                await file.close()

    @app.delete('/api/uploads')
    async def clear_uploads():
        app.state.manager.poll()
        if any(j['status'] == 'running' for j in app.state.manager.jobs.values()):
            raise FDTError('ENGINE_BUSY', '작업 종료 후 임시 파일을 정리하세요.')
        for folder in (root / 'uploads').iterdir():
            if folder.is_dir():
                shutil.rmtree(folder)
        return {'status': 'ok'}

    @app.get('/api/twins')
    async def twins():
        entries = []
        for directory in (root / 'twins').iterdir():
            if directory.is_dir() and ID.fullmatch(directory.name):
                try:
                    entries.append(read_json(directory / 'meta.json'))
                except FDTError:
                    LOG.warning('Skipping invalid Twin metadata: %s', directory.name)
        return {'twins': sorted(entries, key=lambda m: m['created_at'], reverse=True)}

    @app.post('/api/twins', status_code=202)
    async def build(body: dict[str, Any]):
        if set(body) - {'upload_id', 'name', 'snapshot', 'as_of'}:
            raise FDTError('INVALID_BUILD_FIELD', '지원하지 않는 생성 입력 필드입니다.')
        name = body.get('name', '내 금융 Twin')
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise FDTError('INVALID_NAME', 'Twin 이름을 1~80자로 입력하세요.')
        upload_id = body.get('upload_id', '')
        if not isinstance(upload_id, str):
            raise FDTError('UPLOADS_NOT_FOUND', 'CSV를 먼저 업로드하세요.')
        directory = checked_path('uploads', upload_id)
        profile = read_json(directory / 'profile.json')
        snapshot = body.get('snapshot')
        validate_snapshot(snapshot)
        as_of = body.get('as_of') or profile['as_of']
        try:
            date.fromisoformat(as_of)
        except (ValueError, TypeError):
            raise FDTError('INVALID_AS_OF', '기준일을 YYYY-MM-DD 형식으로 입력하세요.')
        return app.state.manager.submit('build', {'files': [str(p) for p in sorted(directory.glob('*.csv'))],
                                                 'name': name.strip(), 'snapshot': snapshot, 'as_of': as_of})

    @app.get('/api/twins/{id_}')
    async def inspect(id_: str):
        directory = checked_path('twins', id_)
        return {'meta': summary_meta(id_), 'summary': TwinStore(directory / 'twin.sqlite').load().inspect()}

    @app.delete('/api/twins/{id_}')
    async def delete_twin(id_: str):
        directory = checked_path('twins', id_)
        app.state.manager.poll()
        if any(j['status'] == 'running' and j['payload'].get('record_id') == id_ for j in app.state.manager.jobs.values()):
            raise FDTError('TWIN_IN_USE', '이 Twin의 계산을 먼저 종료하세요.')
        shutil.rmtree(directory)
        return {'status': 'deleted', 'id': id_}

    @app.post('/api/validate/request')
    async def validate_request(request_body: dict[str, Any]):
        validate('request', request_body)
        return {'status': 'valid'}

    @app.post('/api/twins/{id_}/runs', status_code=202)
    async def run(id_: str, request_body: dict[str, Any]):
        directory = checked_path('twins', id_)
        validate('request', request_body)
        if request_body['mode'] == 'optimize':
            opt = request_body.get('optimization', {})
            count = len(opt.get('reduction_grid', [0, .1, .2])) ** len(opt.get('envelopes', ['외식', '취미·여가', '쇼핑']))
            if count > 128:
                raise FDTError('OPTIMIZATION_LIMIT', '감축률 조합은 128개 이하로 설정하세요.', {'candidates': count})
        return app.state.manager.submit('run', {'database': str(directory / 'twin.sqlite'), 'request': request_body, 'record_id': id_})

    @app.get('/api/activity')
    async def activity():
        app.state.manager.poll()
        running = next((j for j in app.state.manager.jobs.values() if j['status'] == 'running'), None)
        if running is None:
            return {'job': None}
        data = app.state.manager.public(running)
        data['request'] = running['payload'].get('request')
        data['record_id'] = running['payload'].get('record_id')
        data['name'] = running['payload'].get('name')
        return {'job': data}

    @app.get('/api/jobs/{id_}')
    async def job(id_: str):
        return app.state.manager.get(id_)

    @app.post('/api/jobs/{id_}/cancel')
    async def cancel(id_: str):
        return app.state.manager.cancel(id_)

    @app.get('/api/jobs/{id_}/result')
    async def result(id_: str):
        job = app.state.manager.get(id_)
        if job['status'] != 'succeeded':
            raise FDTError('RESULT_NOT_READY', '완료된 작업만 결과를 조회할 수 있습니다.')
        return FileResponse(root / 'jobs' / id_ / 'result.json', media_type='application/json')

    @app.get('/')
    async def index():
        return FileResponse(STATIC / 'index.html', media_type='text/html')

    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app
