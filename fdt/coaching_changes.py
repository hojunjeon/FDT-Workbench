"""Atomic receipt shifts and explicit caps on changeable consumption."""
from __future__ import annotations

import copy
import numpy as np
from .coaching_contract import fail


def prepare_bundle(twin, original, changes: list[dict]):
    bundle = copy.deepcopy(original) if changes else original
    notes = []
    components = twin.model['components']
    rules = {r['rule_id']: r for r in twin.model['rules']}
    future = [d.isoformat() for d in bundle.dates]

    # Resolve all moves against the original ledger before applying any move.
    # Two receipts may share their new date without changing each other's identity.
    delays = {}
    for c in changes:
        if c['kind'] != 'income_delay':
            continue
        rule = rules.get(c['rule_id'])
        if rule is None or components[rule['component']].get('kind') != 'income':
            fail('지연할 수입 규칙을 찾을 수 없습니다.', rule_id=c['rule_id'])
        occurrences = original.scheduled[c['rule_id']]
        matches = [(i, vals) for i, vals in occurrences if future[i] == c['original_date']]
        if len(matches) != 1:
            fail('그 날짜의 입금 한 건을 확인할 수 없습니다. 금액 감소로 대체하지 않습니다.')
        old_index, values = matches[0]
        moves = delays.setdefault(c['rule_id'], {})
        if old_index in moves:
            fail('같은 입금 건을 두 번 지연할 수 없습니다.')
        moves[old_index] = (future.index(c['new_date']), values.copy()) if c['new_date'] in future else None
        if c['new_date'] not in future:
            notes.append({'code': 'INCOME_AFTER_WINDOW', 'severity': 'user',
                          'detail': f"{c['new_date']} 입금은 점검 기간 밖입니다. 소득 소멸이 아니라 수령 시점 이동입니다."})
    for rule_id, moves in delays.items():
        shifted = [(i, vals) for i, vals in original.scheduled[rule_id] if i not in moves]
        shifted.extend(move for move in moves.values() if move is not None)
        bundle.scheduled[rule_id] = sorted(shifted, key=lambda item: item[0])

    for c in changes:
        if c['kind'] != 'spending_cap':
            continue
        days = [i for i, d in enumerate(future) if c['start_date'] <= d <= c['end_date']]
        columns = [j for j, f in enumerate(components) if f.get('kind') == 'expense'
                   and f.get('envelope') == c['envelope'] and not f.get('protected') and not f.get('pending')
                   and f.get('budgeted')]
        # Confirmed/estimated scheduled consumption is not automatically cancelled.
        committed = np.zeros(bundle.paths, dtype=np.int64)
        for j, f in enumerate(components):
            if f.get('kind') == 'expense' and f.get('envelope') == c['envelope'] and f.get('budgeted') and f.get('protected'):
                committed += bundle.variable[:, days, j].sum(axis=1)
        for rid, occurrences in bundle.scheduled.items():
            f = components[rules[rid]['component']]
            if f.get('kind') == 'expense' and f.get('envelope') == c['envelope'] and f.get('budgeted'):
                for i, values in occurrences:
                    if i in days:
                        committed += values
        for event in changes:
            if event['kind'] == 'expense' and event.get('envelope') == c['envelope'] and c['start_date'] <= event['date'] <= c['end_date']:
                committed += event['amount_krw']
        if np.any(committed > c['amount_krw']):
            notes.append({'code': 'CAP_BELOW_COMMITTED_SPENDING', 'severity': 'user', 'envelope': c['envelope'],
                          'detail': '예정 지출만으로 한도를 넘는 경로가 있습니다. 이 한도를 달성한 계획으로 표시하지 않습니다.'})
        remaining = np.maximum(c['amount_krw']-committed, 0)
        # Chronological capping, not an invented reduction percentage. Integer
        # allocation is exact and deterministic even across multiple components.
        for i in days:
            for j in columns:
                values = bundle.variable[:, i, j]
                kept = np.minimum(values, remaining)
                bundle.variable[:, i, j] = kept
                remaining -= kept

    return bundle, notes
