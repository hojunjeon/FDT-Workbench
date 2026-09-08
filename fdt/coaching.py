"""Observation -> one useful next action -> user commitment -> later check-in.

No LLM, banking action, hidden percentage, automatic budget approval or causal
claim is involved. Raw numeric engine modes remain a separate research surface.
"""
from __future__ import annotations

from datetime import date, timedelta
from .coaching_contract import validate_review
from .coaching_projection import frequency, project, q, summarize
from .simulation import generate_bundle
from .util import digest

from .coaching_advice import _action, advise


def observed_budgets(twin) -> list[dict]:
    rows = []
    for envelope, limit in (twin.snapshot or {}).get('budgets', {}).items():
        used = sum(t.budget_amount_krw for t in twin.transactions
                   if t.active and t.kind == 'expense' and not t.pending and t.envelope == envelope
                   and t.date[:7] == twin.as_of[:7] and t.date <= twin.as_of)
        rows.append({'envelope': envelope, 'budget_krw': limit, 'observed_used_krw': used,
                     'observed_remaining_krw': limit-used, 'as_of': twin.as_of,
                     'basis': 'approved_snapshot_budget_minus_observed_budgeted_spending'})
    return rows


def review_commitments(twin, commitments: list[dict]) -> list[dict]:
    """Measure an explicit future-period cap against later observed transactions."""
    rows = []
    first = min(t.date for t in twin.transactions)
    for plan in commitments:
        if plan.get('status', 'active') != 'active':
            continue
        action = plan['action']
        if action['kind'] != 'spending_cap':
            continue
        start, end = action['start_date'], action['end_date']
        used = sum(t.budget_amount_krw for t in twin.transactions if t.active and t.kind == 'expense'
                   and not t.pending and t.envelope == action['envelope'] and start <= t.date <= min(end, twin.as_of))
        pending = sum(t.amount_krw for t in twin.transactions if t.active and t.kind == 'expense'
                      and t.pending and start <= t.date <= min(end, twin.as_of))
        complete = first <= start and twin.as_of >= end and not pending
        if first > start or pending:
            state = 'needs_data'
        elif twin.as_of < start:
            state = 'not_started'
        elif used > action['amount_krw']:
            state = 'over_cap'
        elif complete:
            state = 'within_cap'
        else:
            state = 'in_progress'
        rows.append({'id': plan['id'], 'label': plan['label'], 'action': action, 'state': state,
                     'observed_spending_krw': used, 'remaining_krw': action['amount_krw']-used,
                     'observed_through': twin.as_of, 'coverage_complete': complete,
                     'note': '관측된 지출과 정한 한도를 비교합니다. 절약의 인과 효과나 실제 이체 완료를 뜻하지 않습니다.'})
    return rows


class Coach:
    def __init__(self, twin):
        self.twin = twin

    def review(self, request: dict, commitments: list[dict] | None = None) -> dict:
        validate_review(request)
        twin = self.twin
        reserve = request.get('protected_cash_krw', (twin.snapshot or {}).get('reserve_krw'))
        history_days = twin.model['behavior']['observation_days']
        source = (twin.snapshot or {}).get('source', 'UNKNOWN')
        missing = twin.cash_requirements()
        observed = observed_budgets(twin)
        result = {
            'schema_version': 'coaching/1.0', 'twin_id': twin.twin_id, 'revision': twin.revision,
            'as_of': twin.as_of, 'on_date': request['on_date'], 'through_date': request['through_date'],
            'request_digest': digest(request), 'input_digest': twin.content_digest, 'status': 'ready',
            'context': {'replay': request.get('replay', False), 'snapshot_source': source,
                        'protected_cash_krw': reserve,
                        'reserve_source': 'USER_REQUEST' if 'protected_cash_krw' in request else 'SNAPSHOT' if reserve is not None else 'MISSING',
                        'historical_days': history_days, 'absolute_cash_ready': not missing,
                        'simulation_policy': {'paths': request.get('paths', 400), 'seed': request.get('seed', 42),
                                              'source': 'REPRODUCIBILITY_DEFAULT_UNLESS_SUPPLIED'},
                        'calibrated': False},
            'observed_budgets': observed, 'projection': None, 'comparison': None,
            'follow_up': review_commitments(twin, commitments or []), 'next_action': None,
            'warnings': [
                {'code': 'CONDITIONAL_MODEL', 'severity': 'user',
                 'detail': '경로 수와 분위수는 입력한 자료·가정 아래의 계산입니다. 실제 확률, 최악의 경우, 안전 보장이 아닙니다.'},
                {'code': 'DAY_CLOSE_MODEL', 'severity': 'info',
                 'detail': '일 마감 자료를 사용합니다. 당일 입출금 순서와 영업일, 실제 카드 약관은 별도 확인이 필요합니다.'}],
            'next_check_in': {'date': min((date.fromisoformat(request['on_date'])+timedelta(days=7)).isoformat(), request['through_date']),
                              'trigger': '새 거래·잔액이 들어오거나 계획이 바뀌면 다시 점검', 'scheduled': False},
            'executed': False,
        }
        if source == 'USER_ASSUMPTION':
            result['warnings'].append({'code': 'ASSUMED_SNAPSHOT', 'severity': 'user',
                'detail': '잔액·카드 청구·예산에 사용자 또는 데모 가정이 포함되어 있습니다. 실제 계좌 확인 결과가 아닙니다.'})
        if any(c['kind'] == 'CREDIT' for c in (twin.snapshot or {}).get('cards', [])):
            result['warnings'].append({'code': 'EXISTING_CARD_SCHEDULE_APPROXIMATION', 'severity': 'user',
                'detail': '기존 소비의 미래 카드 청구는 단순화된 정산 일정 모델입니다. 실제 청구서와 결제일을 확인해야 합니다. 새로 입력한 카드 지출에는 지정한 출금일을 사용합니다.'})
        if request.get('replay'):
            result['warnings'].append({'code': 'HISTORICAL_REPLAY', 'severity': 'user',
                'detail': f"{request['on_date']} 자료 기준의 검토입니다. 오늘의 사용 가능 금액으로 안내하지 않습니다."})
        if request['on_date'] != twin.as_of:
            result['status'] = 'needs_data'
            result['next_action'] = _action('refresh_data', '최신 거래와 잔액부터 맞춰 주세요',
                f"자료는 {twin.as_of}까지입니다. {request['on_date']}에 쓸 돈을 계산하기 전에 그 사이의 거래·잔액·청구 정보를 갱신해야 합니다.",
                ['as_of', 'on_date'], required_inputs=['updated_transactions', 'dated_balances_and_card_bills'])
            return result
        horizon = (date.fromisoformat(request['through_date'])-date.fromisoformat(twin.as_of)).days
        if horizon > history_days or history_days < 60:
            result['warnings'].append({'code': 'LIMITED_HISTORY', 'severity': 'user',
                'detail': f'{history_days}일 이력으로 {horizon}일을 점검합니다. 짧은 이력의 반복 가정이며 기간이 길수록 재점검이 필요합니다.'})
        pending = twin.model.get('audit', {}).get('pending_consumption_krw', 0)
        historical_expense = twin.model.get('audit', {}).get('kind_totals_krw', {}).get('expense', 0)
        incomplete_budget = min(t.date for t in twin.transactions) > twin.as_of[:8]+'01'
        uncertain_budget = incomplete_budget or (historical_expense > 0 and pending/historical_expense > .05)
        if uncertain_budget:
            result['warnings'].append({'code': 'BUDGET_INPUT_INCOMPLETE', 'severity': 'user',
                'detail': '월초 이력 또는 지출 분류가 충분하지 않아 잔여 예산만으로 새 지출을 권하지 않습니다.'})
        # Saved plans are reminders, never silently injected into a new forecast.
        bundle = generate_bundle(twin, horizon, request.get('paths', 400), request.get('seed', 42))
        baseline = project(twin, bundle, [])
        baseline_summary = summarize(twin, baseline, reserve, observed)
        result['projection'] = baseline_summary
        if missing:
            result['status'] = 'needs_data'
            result['next_action'] = _action('complete_cash_state', '결제 가능 여부를 판단할 자료가 부족합니다',
                '소비 기록과 잔액은 다릅니다. 계좌 잔액과 미결제 카드 청구를 확인해 주세요. 관측된 예산 사용액은 아래에서 볼 수 있습니다.',
                ['context.absolute_cash_ready'], required_inputs=missing)
            return result
        changes = request.get('changes', [])
        if changes:
            branch = project(twin, bundle, changes)
            branch_summary = summarize(twin, branch, reserve, observed)
            effect = {'spending_change': q(branch.spending.sum(axis=1)-baseline.spending.sum(axis=1))}
            if branch.cash is not None:
                effect['terminal_cash_change'] = q(branch.cash[:, -1, :].sum(axis=1)-baseline.cash[:, -1, :].sum(axis=1))
                effect['terminal_unencumbered_change'] = q(branch.free[:, -1]-baseline.free[:, -1])
                effect['terminal_after_earmark_change'] = q((branch.free[:, -1]-branch.locked[:, -1, :].sum(axis=1))-
                                                          (baseline.free[:, -1]-baseline.locked[:, -1, :].sum(axis=1)))
            result['comparison'] = {'changes': changes, 'baseline': baseline_summary, 'planned': branch_summary,
                                    'effect': effect, 'basis': 'paired_same_random_paths', 'executed': False}
            result['projection'] = branch_summary
        return advise(result, request, twin, reserve, uncertain_budget)
