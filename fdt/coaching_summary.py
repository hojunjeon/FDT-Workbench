"""Pathwise budget, account and cash-flow summaries for coaching."""
from __future__ import annotations

import numpy as np
from .mapping import ENVELOPES


def q(values) -> dict:
    return {f'p{p}_krw': int(round(float(np.percentile(values, p)))) for p in (10, 50, 90)}


def frequency(mask) -> dict:
    return {'count': int(np.count_nonzero(mask)), 'paths': int(len(mask)),
            'fraction': float(np.mean(mask)), 'basis': 'conditional_model_paths_not_real_world_probability'}


def summarize(twin, projection, reserve: int | None, observed_budgets: list[dict]) -> dict:
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
        'period_earmark_breach': frequency(np.any(current_usable < 0, axis=(1, 2))),
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
            'period_earmark_breach': frequency(np.any(current_usable[:, :, j] < 0, axis=1)),
            'lowest_after_earmark': q(current_usable[:, :, j].min(axis=1)),
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
