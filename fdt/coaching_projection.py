"""Action-aware cash, credit and earmark ledgers using the original simulator."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import numpy as np

from .coaching_contract import fail
from .coaching_changes import prepare_bundle
from .coaching_summary import q, frequency, summarize
from .errors import FDTError
from .mapping import ENVELOPES
from .simulation import simulate


@dataclass
class Projection:
    dates: list[str]
    cash: np.ndarray | None
    payable: np.ndarray | None
    locked: np.ndarray | None
    usage: np.ndarray
    spending: np.ndarray
    fixed: np.ndarray
    calendar: list[dict]
    accounts: list[str]
    totals: np.ndarray
    extra_direct: int
    notes: list[dict]

    @property
    def free(self):
        return self.cash.sum(axis=2)-self.payable if self.cash is not None else None


def project(twin, original, changes: list[dict]) -> Projection:
    bundle, notes = prepare_bundle(twin, original, changes)
    components = twin.model['components']
    rules = {r['rule_id']: r for r in twin.model['rules']}
    future = [d.isoformat() for d in bundle.dates]
    all_dates = [twin.as_of] + future
    accounts = [a['account_id'] for a in (twin.snapshot or {}).get('accounts', [])]
    cards = {c['card_id']: c for c in (twin.snapshot or {}).get('cards', [])}
    sim = simulate(twin, bundle)
    p, h = bundle.paths, len(future)
    usage = np.concatenate([np.zeros((p, 1, len(ENVELOPES)), dtype=np.int64), sim.budget_by_envelope], axis=1)
    spending = np.concatenate([np.zeros((p, 1), dtype=np.int64), sim.consumption], axis=1)
    fixed = np.concatenate([np.zeros((p, 1), dtype=np.int64), sim.fixed], axis=1)
    cash = sim.cash.copy() if sim.cash is not None else None
    payable = sim.payable.copy() if sim.payable is not None else None
    locked = np.zeros_like(cash) if cash is not None else None
    calendar = copy.deepcopy(sim.calendar)
    extra_direct = 0
    for c in changes:
        kind = c['kind']
        if kind not in ('expense', 'set_aside'):
            continue
        account = c.get('account_id')
        if c.get('card_id'):
            card = cards.get(c['card_id'])
            if not card:
                fail('지출 카드를 확인할 수 없습니다.')
            account = card['settlement_account_id']
            if card['kind'] == 'DEBIT' and c['payment_date'] != c['date']:
                fail('체크카드는 구매일과 출금일이 같아야 합니다.')
        if account not in accounts:
            fail('자금을 보관하거나 지출할 계좌를 확인할 수 없습니다.')
        ai = accounts.index(account)
        amount = c['amount_krw']
        if kind == 'set_aside':
            if locked is not None:
                locked[:, :, ai] += amount
            continue
        index = all_dates.index(c['date'])
        spending[:, index] += amount
        if c.get('envelope'):
            usage[:, index, ENVELOPES.index(c['envelope'])] += amount
        else:
            notes.append({'code': 'EXPENSE_ENVELOPE_UNASSIGNED', 'severity': 'user',
                          'detail': '예정 지출은 현금에 반영했지만 예산 항목이 없어 봉투 잔여에는 배분하지 않았습니다.'})
        if c.get('card_id') and cards[c['card_id']]['kind'] == 'CREDIT':
            if payable is not None:
                payable[:, index:] += amount
            if c['payment_date'] in all_dates:
                due = all_dates.index(c['payment_date'])
                if cash is not None:
                    cash[:, due:, ai] -= amount
                    payable[:, due:] -= amount
            else:
                notes.append({'code': 'PAYMENT_AFTER_WINDOW', 'severity': 'user',
                              'detail': '카드 출금은 점검 기간 밖입니다. 미결제액은 이미 가용금액에서 제외했습니다. 결제 준비 판단에는 결제일까지 점검해야 합니다.'})
            payment_date = c['payment_date']
        else:
            if cash is not None:
                cash[:, index:, ai] -= amount
            extra_direct += amount
            if c.get('reserve_now') and locked is not None:
                locked[:, :index, ai] += amount
            payment_date = c['date']
        calendar.append({'date': payment_date, 'purchase_date': c['date'], 'event_type': 'PLANNED_EXPENSE',
                         'account_id': account, 'card_id': c.get('card_id'), 'expected_amount_krw': amount,
                         'source': 'USER_PLAN', 'reference_id': c.get('label', '예정 지출'),
                         'within_forecast_horizon': payment_date <= all_dates[-1]})
    totals = bundle.variable.sum(axis=1).copy()
    for rid, occurrences in bundle.scheduled.items():
        for _, values in occurrences:
            totals[:, rules[rid]['component']] += values
    if cash is not None:
        # Independent pathwise check of the adapter's extra ledgers. Earmarking
        # changes spendability, never cash, liabilities or economic outflow.
        opening_free = sum(a['balance_krw'] for a in twin.snapshot['accounts']) - sum(
            c.get('opening_payable_krw', 0) for c in cards.values())
        expected_free = np.full(p, opening_free, dtype=np.int64)
        for j, component in enumerate(components):
            if component['kind'] in ('income', 'reimbursement'):
                expected_free += totals[:, j]
            elif component['kind'] not in ('internal_transfer', 'card_settlement'):
                expected_free -= totals[:, j]
        expected_free -= sum(c['amount_krw'] for c in changes if c['kind'] == 'expense')
        if not np.array_equal(cash[:, -1, :].sum(axis=1)-payable[:, -1], expected_free):
            raise FDTError('COACHING_BALANCE_INVARIANT', '행동별 현금·미결제 전이와 자금 흐름이 일치하지 않습니다.')
    return Projection(all_dates, cash, payable, locked, usage, spending, fixed,
                      sorted(calendar, key=lambda row: (row['date'], row['event_type'])),
                      accounts, totals, extra_direct, notes)


