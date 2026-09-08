"""Date- and action-aware projections on the existing numeric model.

This adapter deliberately does not translate everyday decisions into five engine
modes. Spending, unpaid cards and earmarked money remain separate ledgers.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
import numpy as np

from .coaching_contract import fail
from .mapping import ENVELOPES
from .simulation import simulate


def q(values) -> dict:
    return {f'p{p}_krw': int(round(float(np.percentile(values, p)))) for p in (10, 50, 90)}


def frequency(mask) -> dict:
    return {'count': int(np.count_nonzero(mask)), 'paths': int(len(mask)),
            'fraction': float(np.mean(mask)), 'basis': 'conditional_model_paths_not_real_world_probability'}


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
    bundle = copy.deepcopy(original) if changes else original
    notes = []
    components = twin.model['components']
    rules = {r['rule_id']: r for r in twin.model['rules']}
    future = [d.isoformat() for d in bundle.dates]
    all_dates = [twin.as_of] + future
    accounts = [a['account_id'] for a in (twin.snapshot or {}).get('accounts', [])]
    cards = {c['card_id']: c for c in (twin.snapshot or {}).get('cards', [])}

    for c in changes:
        if c['kind'] != 'income_delay':
            continue
        rule = rules.get(c['rule_id'])
        if rule is None or components[rule['component']].get('kind') != 'income':
            fail('지연할 수입 규칙을 찾을 수 없습니다.', rule_id=c['rule_id'])
        occurrences = bundle.scheduled[c['rule_id']]
        matches = [(i, vals) for i, vals in occurrences if future[i] == c['original_date']]
        if len(matches) != 1:
            fail('그 날짜의 입금 한 건을 확인할 수 없습니다. 금액 감소로 대체하지 않습니다.')
        old_index, values = matches[0]
        shifted = [(i, vals) for i, vals in occurrences if i != old_index]
        if c['new_date'] in future:
            shifted.append((future.index(c['new_date']), values.copy()))
        else:
            notes.append({'code': 'INCOME_AFTER_WINDOW', 'severity': 'user',
                          'detail': f"{c['new_date']} 입금은 점검 기간 밖입니다. 소득 소멸이 아니라 수령 시점 이동입니다."})
        bundle.scheduled[c['rule_id']] = sorted(shifted, key=lambda item: item[0])

    for c in changes:
        if c['kind'] != 'spending_cap':
            continue
        days = [i for i, d in enumerate(future) if c['start_date'] <= d <= c['end_date']]
        columns = [j for j, f in enumerate(components) if f.get('kind') == 'expense'
                   and f.get('envelope') == c['envelope'] and not f.get('protected') and not f.get('pending')
                   and f.get('budgeted')]
        # Confirmed/estimated scheduled consumption is not automatically cancelled.
        committed = np.zeros(bundle.paths, dtype=np.int64)
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
    return Projection(all_dates, cash, payable, locked, usage, spending, fixed,
                      sorted(calendar, key=lambda row: (row['date'], row['event_type'])),
                      accounts, totals, extra_direct, notes)


def summarize(twin, projection: Projection, reserve: int | None, observed_budgets: list[dict]) -> dict:
    pr = projection
    end = pr.dates[-1]
    result = {'through_date': end, 'budget_cutoff_date': None, 'budgets': [], 'cash': None,
              'upcoming': [r for r in pr.calendar if r['date'] <= end], 'warnings': pr.notes}
    for b in observed_budgets:
        indexes = [i for i, d in enumerate(pr.dates) if d[:7] == twin.as_of[:7]]
        used = b['observed_used_krw'] + pr.usage[:, indexes, ENVELOPES.index(b['envelope'])].sum(axis=1)
        remaining = b['budget_krw']-used
        result['budgets'].append({**b, 'remaining_at_cutoff': q(remaining), 'over_budget': frequency(remaining < 0),
                                  'coverage_end': pr.dates[indexes[-1]]})
        result['budget_cutoff_date'] = pr.dates[indexes[-1]]
    if end[:7] != twin.as_of[:7]:
        result['warnings'] = result['warnings'] + [{'code': 'NEXT_MONTH_BUDGET_UNCONFIRMED', 'severity': 'user',
            'detail': '다음 달 예산을 이번 달 예산과 같다고 가정하지 않습니다. 봉투 잔여는 이번 달까지만 표시합니다.'}]
    if pr.cash is None:
        return result
    cash = pr.cash.sum(axis=2)
    free = pr.free - pr.locked.sum(axis=2) - (reserve or 0)
    any_short = np.any(pr.cash < 0, axis=(1, 2))
    current_usable = pr.cash - pr.locked
    result['cash'] = {
        'period_account_shortfall': frequency(any_short),
        'terminal_account_shortfall': frequency(np.any(pr.cash[:, -1, :] < 0, axis=1)),
        'period_protected_cash_breach': frequency(np.any(free < 0, axis=1)) if reserve is not None else None,
        'terminal_balance': q(cash[:, -1]), 'lowest_balance': q(cash.min(axis=1)),
        'terminal_unencumbered': q(pr.free[:, -1]),
        'lowest_after_protection': q(free.min(axis=1)) if reserve is not None else None,
        'accounts': []}
    for j, account in enumerate(pr.accounts):
        headroom = np.minimum(current_usable[:, :, j].min(axis=1), free.min(axis=1))
        result['cash']['accounts'].append({'account_id': account,
            'period_shortfall': frequency(np.any(pr.cash[:, :, j] < 0, axis=1)),
            'lowest_balance': q(pr.cash[:, :, j].min(axis=1)),
            'additional_one_off_room': q(headroom) if reserve is not None else None,
            'room_basis': 'same_account_and_total_unencumbered_minimum_after_existing_spending_not_a_budget'})
    totals = pr.totals
    by_kind = {}
    direct_spend = pr.extra_direct
    direct_fixed = 0
    cards = {c['card_id']: c for c in (twin.snapshot or {}).get('cards', [])}
    for j, f in enumerate(twin.model['components']):
        value = float(totals[:, j].mean())
        by_kind[f['kind']] = by_kind.get(f['kind'], 0.) + value
        credit = f.get('card_id') and cards[f['card_id']]['kind'] == 'CREDIT'
        if not credit and f['kind'] == 'expense':
            direct_spend += value
        if not credit and f['kind'] == 'fixed_expense':
            direct_fixed += value
    opening = sum(a['balance_krw'] for a in twin.snapshot['accounts'])
    terminal = float(cash[:, -1].mean())
    inflow = by_kind.get('income', 0) + by_kind.get('reimbursement', 0)
    other_out = sum(by_kind.get(k, 0) for k in ('debt_service', 'savings_out', 'cash_withdrawal'))
    # The simulator's cash/free invariant identifies actual card settlement cash,
    # rather than counting both purchases and card payments as cash outflow.
    card_settlement = opening + inflow - direct_spend - direct_fixed - other_out - terminal
    bridge = {'opening_cash_krw': opening,
              'income_krw': round(by_kind.get('income', 0)), 'reimbursement_krw': round(by_kind.get('reimbursement', 0)),
              'direct_spending_krw': round(direct_spend), 'direct_fixed_krw': round(direct_fixed),
              'debt_service_krw': round(by_kind.get('debt_service', 0)), 'savings_out_krw': round(by_kind.get('savings_out', 0)),
              'cash_withdrawal_krw': round(by_kind.get('cash_withdrawal', 0)), 'card_settlement_krw': round(card_settlement),
              'terminal_cash_krw': round(terminal), 'basis': 'path_means_not_sum_of_medians'}
    reconstructed = opening + bridge['income_krw'] + bridge['reimbursement_krw'] - sum(bridge[k] for k in (
        'direct_spending_krw', 'direct_fixed_krw', 'debt_service_krw', 'savings_out_krw', 'cash_withdrawal_krw', 'card_settlement_krw'))
    bridge['rounding_adjustment_krw'] = bridge['terminal_cash_krw'] - reconstructed
    result['cash']['explanation'] = bridge
    return result
