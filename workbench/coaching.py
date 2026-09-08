"""Coaching routes and explicit, local-only user commitments."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import uuid

from fdt.coaching_contract import fields, validate_change, validate_review
from fdt.errors import FDTError
from fdt.mapping import ENVELOPES
from fdt.util import read_json

ID = re.compile(r'^[0-9a-f]{32}$')


def today_seoul() -> str:
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


class PlanStore:
    """Separate from Twin observations; a commitment is not a bank transaction."""
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, twin_id TEXT NOT NULL, label TEXT NOT NULL, action TEXT NOT NULL, created_at TEXT NOT NULL)')
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self, twin_id: str) -> list[dict]:
        with self.connect() as db:
            return [{**dict(row), 'action': json.loads(row['action']), 'status': 'active'}
                    for row in db.execute('SELECT * FROM plans WHERE twin_id=? ORDER BY created_at,id', (twin_id,))]

    def add(self, twin_id: str, label: str, action: dict) -> dict:
        plan = {'id': uuid.uuid4().hex, 'twin_id': twin_id, 'label': label, 'action': action,
                'created_at': datetime.now(timezone.utc).isoformat(), 'status': 'active'}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM plans WHERE twin_id=?', (twin_id,)).fetchone()[0] >= 30:
                raise FDTError('PLAN_LIMIT', '저장된 계획은 사용자당 최대 30개입니다. 끝난 계획을 정리하세요.')
            db.execute('INSERT INTO plans VALUES (?,?,?,?,?)',
                       (plan['id'], twin_id, label, json.dumps(action, ensure_ascii=False), plan['created_at']))
        return plan

    def delete(self, twin_id: str, id_: str) -> None:
        with self.connect() as db:
            if not db.execute('DELETE FROM plans WHERE twin_id=? AND id=?', (twin_id, id_)).rowcount:
                raise FDTError('PLAN_NOT_FOUND', '계획을 찾을 수 없습니다.')


def install_coaching(app) -> None:
    root = app.state.data_dir
    store = PlanStore(root / 'coaching.sqlite')

    def record(id_: str):
        directory = root / 'twins' / id_
        if not ID.fullmatch(id_) or not directory.is_dir():
            raise FDTError('TWIN_NOT_FOUND', '자료를 선택하거나 먼저 갱신해 주세요.')
        return directory, read_json(directory / 'meta.json')

    @app.get('/api/coaching/config')
    async def config():
        return {'schema_version': 'coaching/1.0', 'today': today_seoul(), 'timezone': 'Asia/Seoul',
                'envelopes': ENVELOPES, 'max_days': 90, 'max_changes': 6,
                'purpose': '오늘의 돈 문제 확인, 행동 비교, 약속한 지출 한도의 다음 점검',
                'banking_actions': False, 'free_text_interpretation': False,
                'numeric_workbench_url': '/workbench'}

    @app.get('/api/twins/{id_}/coaching/context')
    async def context(id_: str):
        from fdt.model import rule_dates
        from fdt.store import TwinStore
        directory, _ = record(id_)
        twin = TwinStore(directory / 'twin.sqlite').load()
        snapshot = twin.snapshot or {}
        start = date.fromisoformat(twin.as_of)+timedelta(days=1)
        income = []
        for rule in twin.model['rules']:
            if twin.model['components'][rule['component']].get('kind') == 'income':
                for due in rule_dates(rule, start, start+timedelta(days=89)):
                    income.append({'rule_id': rule['rule_id'], 'date': due.isoformat(),
                                   'amount_krw': rule['expected_amount_krw'], 'source': rule['source']})
        return {'as_of': twin.as_of, 'snapshot_source': snapshot.get('source', 'UNKNOWN'),
                'accounts': snapshot.get('accounts', []), 'cards': snapshot.get('cards', []),
                'protected_cash_krw': snapshot.get('reserve_krw'),
                'income_occurrences': sorted(income, key=lambda row: (row['date'], row['rule_id']))}

    @app.post('/api/twins/{id_}/coaching', status_code=202)
    async def review(id_: str, body: dict):
        directory, meta = record(id_)
        request = dict(body)
        request.setdefault('on_date', today_seoul())
        if request['on_date'] != today_seoul() and not request.get('replay', False):
            raise FDTError('REPLAY_REQUIRED', '과거 기준일 검토는 replay를 명시해야 합니다. 오늘의 결과와 섞지 않습니다.')
        validate_review(request)
        return app.state.manager.submit('coach', {'database': str(directory / 'twin.sqlite'), 'record_id': id_,
            'request': request, 'commitments': store.list(meta['twin_id'])})

    @app.get('/api/twins/{id_}/coaching/plans')
    async def plans(id_: str):
        _, meta = record(id_)
        return {'plans': store.list(meta['twin_id'])}

    @app.post('/api/twins/{id_}/coaching/plans', status_code=201)
    async def save_plan(id_: str, body: dict):
        _, meta = record(id_)
        fields(body, {'label', 'action', 'confirmed'}, {'label', 'action', 'confirmed'})
        if body['confirmed'] is not True:
            raise FDTError('PLAN_CONFIRMATION_REQUIRED', '계획 저장은 사용자가 명시적으로 선택해야 합니다.')
        if not isinstance(body['label'], str) or not 1 <= len(body['label'].strip()) <= 120:
            raise FDTError('INVALID_PLAN_LABEL', '계획 이름은 1~120자여야 합니다.')
        action = validate_change(body['action'])
        if action['kind'] != 'spending_cap':
            raise FDTError('PLAN_TYPE_UNSUPPORTED', '현재 추적 가능한 계획은 기간별 추가 지출 한도입니다. 이체·구매 완료로 기록하지 않습니다.')
        if action['start_date'] <= today_seoul():
            raise FDTError('PLAN_START_DATE', '새 계획은 내일 이후부터 시작합니다. 이미 쓴 돈을 새 한도에 섞지 않습니다.')
        return store.add(meta['twin_id'], body['label'].strip(), action)

    @app.delete('/api/twins/{id_}/coaching/plans/{plan_id}')
    async def delete_plan(id_: str, plan_id: str):
        _, meta = record(id_)
        store.delete(meta['twin_id'], plan_id)
        return {'status': 'deleted', 'id': plan_id}
