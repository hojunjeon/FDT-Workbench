from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
import numpy as np
from .errors import FDTError
from .mapping import ENVELOPES, FIXED_GROUPS
from .model import Twin, rule_dates
from .util import probability, quantiles


@dataclass
class RandomBundle:
    variable: np.ndarray
    scheduled: dict[str, list[tuple[int, np.ndarray]]]
    dates: list[date]
    seed: int
    paths: int
    fallback_days: int


@dataclass
class Simulation:
    dates: list[date]
    resource: np.ndarray                    # paths x (days+1), change from origin
    consumption: np.ndarray                 # paths x days, purchase-time spend
    by_envelope: np.ndarray                 # paths x days x 7
    budget_by_envelope: np.ndarray
    cash: np.ndarray | None                  # paths x (days+1) x accounts
    cash_total: np.ndarray | None
    payable: np.ndarray | None               # paths x (days+1), all cards
    free: np.ndarray | None                  # cash - unpaid card liabilities
    account_ids: list[str]
    calendar: list[dict]
    fixed: np.ndarray                    # paths x days, fixed expense only
    fixed_by_group: np.ndarray           # paths x days x 6
    pending: np.ndarray                  # paths x days, pending expense only
    scenario: dict

    @property
    def total_short(self) -> np.ndarray | None:
        return np.any(self.cash_total<0,axis=1) if self.cash_total is not None else None

    @property
    def any_account_short(self) -> np.ndarray | None:
        return np.any(self.cash<0,axis=(1,2)) if self.cash is not None else None

    def daily_rows(self, as_of: str) -> list[dict]:
        cumulative=np.cumsum(self.consumption,axis=1)
        cumulative_fixed=np.cumsum(self.fixed,axis=1)
        cumulative_pending=np.cumsum(self.pending,axis=1)
        rows=[]
        for i,d in enumerate([date.fromisoformat(as_of)]+self.dates):
            row={'date':d.isoformat()}
            row.update({'resource_change_'+k+'_krw':v for k,v in quantiles(self.resource[:,i]).items()})
            for label, a in [('cash_balance',self.cash_total),('card_payable',self.payable),('unencumbered_liquid',self.free)]:
                qs=quantiles(a[:,i]) if a is not None else {k:None for k in ('p10','p50','p90')}
                row.update({label+'_'+k+'_krw':v for k,v in qs.items()})
            row['cumulative_expense_p50_krw']=int(round(float(np.median(cumulative[:,i-1])))) if i else 0
            row['cumulative_fixed_p50_krw']=int(round(float(np.median(cumulative_fixed[:,i-1])))) if i else 0
            row['cumulative_pending_p50_krw']=int(round(float(np.median(cumulative_pending[:,i-1])))) if i else 0
            row['p_total_cash_below_zero']=probability(self.cash_total[:,i]<0) if self.cash_total is not None else None
            row['p_any_account_below_zero']=probability(np.any(self.cash[:,i,:]<0,axis=1)) if self.cash is not None else None
            rows.append(row)
        return rows


def generate_bundle(twin: Twin, horizon: int, paths: int, seed: int) -> RandomBundle:
    model=twin.model
    if paths*horizon>400000 or paths*horizon*len(model['components'])>6000000:
        raise FDTError('SIMULATION_LIMIT','요청 크기가 메모리 보호 한도를 초과합니다.')
    rng=np.random.Generator(np.random.PCG64(seed))
    start=date.fromisoformat(twin.as_of)+timedelta(days=1)
    future=[start+timedelta(days=i) for i in range(horizon)]
    history=model['daily']; hist_dates=model['dates']
    sampled=np.zeros((paths,horizon,history.shape[1]),dtype=np.int64)
    fallback=0
    for offset in range(0,horizon,7):
        width=min(7,horizon-offset)
        possible=[i for i,d in enumerate(hist_dates) if d.weekday()==future[offset].weekday() and i+7<=len(hist_dates)]
        if possible:
            chosen=rng.choice(possible,size=paths)
            for j in range(width): sampled[:,offset+j,:]=history[chosen+j,:]
        else:
            for j in range(width):
                candidates=[i for i,d in enumerate(hist_dates) if d.weekday()==future[offset+j].weekday()]
                if not candidates: candidates=list(range(len(hist_dates)))
                sampled[:,offset+j,:]=history[rng.choice(candidates,size=paths),:]
                fallback+=1
    scheduled={}
    for r in model['rules']:
        occurrences=[]
        for d in rule_dates(r,start,future[-1]):
            occurrences.append(((d-start).days,rng.choice(r['amount_samples'],size=paths).astype(np.int64)))
        scheduled[r['rule_id']]=occurrences
    return RandomBundle(sampled,scheduled,future,seed,paths,fallback)


def _scaled(values: np.ndarray, factor: float) -> np.ndarray:
    return np.rint(values*factor).astype(np.int64)


def simulate(twin: Twin, bundle: RandomBundle, scenario: dict | None = None) -> Simulation:
    s = scenario or {}
    rules = twin.model['rules']
    components = twin.model['components']
    known = {r['rule_id'] for r in rules}
    cancel_ids = set(s.get('cancel_rule_ids', []))
    invalid = cancel_ids - known
    if invalid:
        raise FDTError('UNKNOWN_RULE', '취소 대상 rule_id가 없습니다.', {'rule_ids': sorted(invalid)})

    overrides = s.get('fixed_overrides', []) or []
    override_ids = [x['rule_id'] for x in overrides]
    if len(override_ids) != len(set(override_ids)):
        raise FDTError('DUPLICATE_OVERRIDE', 'fixed_overrides에 같은 rule_id가 중복됩니다.')
    if set(override_ids) & cancel_ids:
        raise FDTError('OVERRIDE_CANCEL_CONFLICT', '취소한 규칙은 금액을 교체할 수 없습니다.')
    override_by_id = {x['rule_id']: x['amount_krw'] for x in overrides}
    for rule_id, amount in override_by_id.items():
        if rule_id not in known:
            raise FDTError('UNKNOWN_RULE', '교체 대상 rule_id가 없습니다.', {'rule_id': rule_id})
        rule = next(r for r in rules if r['rule_id'] == rule_id)
        f = components[rule['component']]
        if (f.get('kind') or rule.get('kind')) != 'fixed_expense':
            raise FDTError('OVERRIDE_NOT_FIXED', '고정지출 규칙만 금액을 교체할 수 있습니다.', {'rule_id': rule_id})
        if amount < 0 or amount > 10**12:
            raise FDTError('MONEY_RANGE_LIMIT', '교체 금액이 허용 범위를 벗어났습니다.', {'rule_id': rule_id})

    for e in s.get('cash_events', []):
        if not bundle.dates[0].isoformat() <= e['date'] <= bundle.dates[-1].isoformat():
            raise FDTError('SCENARIO_DATE_RANGE', 'cash_events는 예측 기간 안에 있어야 합니다.')
        if e.get('fixed_group') and e['direction'] != 'EXPENSE':
            raise FDTError('SCENARIO_FIXED_GROUP_INCOME', '고정지출 그룹은 지출 현금 이벤트에만 지정할 수 있습니다.')
        if e.get('fixed_group') and e['fixed_group'] not in FIXED_GROUPS:
            raise FDTError('SCENARIO_FIXED_GROUP', '알 수 없는 고정지출 그룹입니다.')

    flows = bundle.variable.copy()
    p, h, _ = flows.shape
    calendar = []
    for j, f in enumerate(components):
        kind = f.get('kind')
        envelope = f.get('envelope')
        protected = f.get('protected', False)
        multiplier = 1 - s.get('expense_reductions', {}).get(envelope, 0) if kind == 'expense' and not protected else 1
        flows[:, :, j] = _scaled(flows[:, :, j], multiplier)

    for rule in rules:
        rule_id = rule['rule_id']
        if rule_id in cancel_ids:
            continue
        f = components[rule['component']]
        kind = f.get('kind') or rule.get('kind')
        fixed_group = f.get('fixed_group') or rule.get('fixed_group')
        factor = s.get('income_multiplier', 1) if kind == 'income' else (
            s.get('fixed_multiplier', 1) if kind == 'fixed_expense' else s.get('expense_multiplier', 1) if kind == 'expense' else 1)
        for i, sampled_values in bundle.scheduled.get(rule_id, []):
            values = sampled_values
            if rule_id in override_by_id:
                values = np.full_like(sampled_values, override_by_id[rule_id])
            flows[:, i, rule['component']] += values
            transformed = _scaled(values, factor)
            calendar.append({'date': bundle.dates[i].isoformat(), 'event_type': 'RECURRING_' + kind.upper(),
                'reference_id': rule_id, 'account_id': f.get('account_id'), 'card_id': f.get('card_id'),
                'fixed_group': fixed_group if kind == 'fixed_expense' else None,
                'expected_amount_krw': int(round(float(np.mean(transformed)))), 'source': rule['source'],
                'timing_basis': 'purchase_date' if f.get('card_id') else 'estimated_cash_date'})

    for j, f in enumerate(components):
        kind = f.get('kind')
        factor = s.get('income_multiplier', 1) if kind == 'income' else (
            s.get('fixed_multiplier', 1) if kind == 'fixed_expense' else s.get('expense_multiplier', 1) if kind == 'expense' else 1)
        flows[:, :, j] = _scaled(flows[:, :, j], factor)

    opening_exposure = sum(abs(a['balance_krw']) for a in (twin.snapshot or {}).get('accounts', []))
    opening_exposure += sum(c.get('opening_payable_krw', 0) for c in (twin.snapshot or {}).get('cards', []))
    opening_exposure += sum(b['amount_krw'] for b in (twin.snapshot or {}).get('known_bills', []))
    opening_exposure += sum(e['amount_krw'] for e in s.get('cash_events', []))
    gross_bound = float(flows.sum(axis=(1, 2), dtype=np.float64).max()) + opening_exposure
    if gross_bound > 9_000_000_000_000_000:
        raise FDTError('MONEY_RANGE_LIMIT', '예측 누적금액이 안전한 JSON/int64 범위를 초과했습니다.')

    resource_delta = np.zeros((p, h), dtype=np.int64)
    spending = np.zeros((p, h), dtype=np.int64)
    fixed = np.zeros((p, h), dtype=np.int64)
    fixed_by_group = np.zeros((p, h, len(FIXED_GROUPS)), dtype=np.int64)
    pending = np.zeros((p, h), dtype=np.int64)
    by_env = np.zeros((p, h, len(ENVELOPES)), dtype=np.int64)
    budgets = np.zeros_like(by_env)
    income_total = np.zeros((p, h), dtype=np.int64)
    debt = np.zeros((p, h), dtype=np.int64)
    withdrawals = np.zeros((p, h), dtype=np.int64)
    savings = np.zeros((p, h), dtype=np.int64)
    ready = not twin.cash_requirements()
    snapshot = twin.snapshot or {}
    ids = [a['account_id'] for a in snapshot.get('accounts', [])]
    account_lookup = {a: i for i, a in enumerate(ids)}
    cards = {c['card_id']: c for c in snapshot.get('cards', [])}
    daily_cash = np.zeros((p, h, len(ids)), dtype=np.int64) if ready else None
    payable_delta = np.zeros((p, h), dtype=np.int64) if ready else None
    bill_calendar: dict[tuple[str, str], np.ndarray] = {}

    for j, f in enumerate(components):
        values = flows[:, :, j]
        kind = f.get('kind')
        if kind in ('income', 'reimbursement'):
            resource_delta += values
            income_total += values
        elif kind not in ('internal_transfer', 'card_settlement'):
            resource_delta -= values
        if kind == 'expense':
            spending += values
            if f.get('pending', False):
                pending += values
            elif f.get('envelope') in ENVELOPES:
                k = ENVELOPES.index(f['envelope'])
                by_env[:, :, k] += values
                if f.get('budgeted', False):
                    budgets[:, :, k] += values
        elif kind == 'fixed_expense':
            fixed += values
            group = f.get('fixed_group')
            if group in FIXED_GROUPS:
                fixed_by_group[:, :, FIXED_GROUPS.index(group)] += values
        elif kind == 'debt_service':
            debt += values
        elif kind == 'cash_withdrawal':
            withdrawals += values
        elif kind == 'savings_out':
            savings += values
        if not ready:
            continue
        if f.get('card_id'):
            policy = cards[f['card_id']]
            ai = account_lookup[policy['settlement_account_id']]
            if kind not in ('expense', 'fixed_expense'):
                raise FDTError('UNSUPPORTED_CARD_FLOW', kind)
            if policy['kind'] == 'DEBIT':
                daily_cash[:, :, ai] -= values
            else:
                payable_delta += values
                for i, d in enumerate(bundle.dates):
                    due = d + timedelta(days=7 - d.weekday() + policy['payment_delay_days'])
                    key = (f['card_id'], due.isoformat())
                    bill_calendar.setdefault(key, np.zeros(p, dtype=np.int64))
                    bill_calendar[key] += values[:, i]
                    offset = (due - bundle.dates[0]).days
                    if 0 <= offset < h:
                        daily_cash[:, offset, ai] -= values[:, i]
                        payable_delta[:, offset] -= values[:, i]
        else:
            ai = account_lookup[f['account_id']]
            if kind in ('income', 'reimbursement'):
                daily_cash[:, :, ai] += values
            elif kind == 'internal_transfer':
                daily_cash[:, :, ai] -= values
                daily_cash[:, :, account_lookup[f['to_account_id']]] += values
            else:
                daily_cash[:, :, ai] -= values

    if ready:
        for bill in snapshot.get('known_bills', []):
            key = (bill['card_id'], bill['due_date'])
            bill_calendar.setdefault(key, np.zeros(p, dtype=np.int64))
            bill_calendar[key] += bill['amount_krw']
            offset = (date.fromisoformat(bill['due_date']) - bundle.dates[0]).days
            if 0 <= offset < h:
                ai = account_lookup[cards[bill['card_id']]['settlement_account_id']]
                daily_cash[:, offset, ai] -= bill['amount_krw']
                payable_delta[:, offset] -= bill['amount_krw']
        for (card_id, due), vals in sorted(bill_calendar.items()):
            if np.any(vals):
                calendar.append({'date': due, 'event_type': 'CARD_BILL', 'reference_id': card_id + '@' + due,
                    'account_id': cards[card_id]['settlement_account_id'], 'card_id': card_id, 'fixed_group': None,
                    'expected_amount_krw': int(round(float(np.mean(vals)))),
                    'source': 'KNOWN_PLUS_SIMULATED', 'timing_basis': 'weekly_issue_plus_explicit_delay'})

    observed_ids = {t.account_id for t in twin.transactions if t.account_id} | set(ids)
    for e in s.get('cash_events', []):
        offset = (date.fromisoformat(e['date']) - bundle.dates[0]).days
        sign = 1 if e['direction'] == 'INCOME' else -1
        amount = e['amount_krw']
        resource_delta[:, offset] += sign * amount
        if sign > 0:
            income_total[:, offset] += amount
        elif e.get('fixed_group'):
            fixed[:, offset] += amount
            fixed_by_group[:, offset, FIXED_GROUPS.index(e['fixed_group'])] += amount
        else:
            spending[:, offset] += amount
            by_env[:, offset, -1] += amount
            budgets[:, offset, -1] += amount
        if e['account_id'] not in observed_ids or (ready and e['account_id'] not in account_lookup):
            raise FDTError('SCENARIO_ACCOUNT', 'cash_event의 계좌를 확인할 수 없습니다.')
        if ready:
            daily_cash[:, offset, account_lookup[e['account_id']]] += sign * amount
        calendar.append({'date': e['date'],
            'event_type': 'SCENARIO_FIXED_EXPENSE' if e.get('fixed_group') else 'SCENARIO_' + e['direction'],
            'reference_id': 'cash_event', 'account_id': e['account_id'], 'card_id': None,
            'fixed_group': e.get('fixed_group'), 'expected_amount_krw': amount,
            'source': 'USER_ASSUMPTION', 'timing_basis': 'explicit_date'})

    if not np.array_equal(fixed, fixed_by_group.sum(axis=2)):
        raise FDTError('FLOW_INVARIANT', '고정지출 그룹 합계와 고정지출 합계가 다릅니다.')
    expected_resource = income_total - spending - fixed - debt - withdrawals - savings
    if not np.array_equal(expected_resource, resource_delta):
        diff = expected_resource - resource_delta
        raise FDTError('FLOW_INVARIANT', '지출 항목과 resource_delta가 일치하지 않습니다.',
                       {'max_difference_krw': int(np.max(np.abs(diff)))})

    cash = total = payable = free = None
    if ready:
        opening = np.array([a['balance_krw'] for a in snapshot['accounts']], dtype=np.int64)
        cash = np.concatenate([np.broadcast_to(opening, (p, 1, len(ids))), np.cumsum(daily_cash, axis=1) + opening], axis=1)
        total = cash.sum(axis=2)
        opening_payable = sum(c.get('opening_payable_krw', 0) for c in cards.values())
        payable = np.concatenate([np.full((p, 1), opening_payable, dtype=np.int64), np.cumsum(payable_delta, axis=1) + opening_payable], axis=1)
        if np.any(payable < 0):
            raise FDTError('PAYABLE_INVARIANT', '카드 미결제액이 음수가 되었습니다.')
        free = total - payable
    for entry in calendar:
        entry['within_forecast_horizon'] = entry['date'] <= bundle.dates[-1].isoformat()
        if entry['event_type'] == 'CARD_BILL':
            entry['amount_coverage'] = 'known_bills_plus_purchases_through_forecast_end_only'
    resource = np.concatenate([np.zeros((p, 1), dtype=np.int64), np.cumsum(resource_delta, axis=1)], axis=1)
    if ready and not np.array_equal(free - free[:, [0]], resource):
        raise FDTError('BALANCE_INVARIANT', '자금 여력과 계좌/카드 전이가 일치하지 않습니다.')
    return Simulation(bundle.dates, resource, spending, by_env, budgets, cash, total, payable, free, ids,
                      sorted(calendar, key=lambda c: (c['date'], c['event_type'], c['reference_id'])),
                      fixed, fixed_by_group, pending, s)
