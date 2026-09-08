"""Choose one next action without inventing a spending decision."""
from __future__ import annotations

DISCRETIONTIONARY = {'외식', '취미·여가', '쇼핑'}


def _action(kind: str, title: str, detail: str, evidence: list[str], **extra) -> dict:
    return {'kind': kind, 'title': title, 'detail': detail, 'evidence_refs': evidence, **extra}


def advise(result, request, twin, reserve, uncertain_budget):
    active = result['projection']
    result['warnings'].extend(active['warnings'])
    cash = active['cash']
    affected = [a for a in cash['accounts'] if a['period_shortfall']['count']]
    if affected:
        worst = max(affected, key=lambda a: a['period_shortfall']['count'])
        samples = worst['period_shortfall']
        result['next_action'] = _action('prepare_payment_account', '먼저 결제 계좌의 잔액을 확인하세요',
            f"{request['through_date']}까지 {samples['paths']}개 경로 중 {samples['count']}개에서 {worst['account_id']} 잔액 부족이 나타났습니다. 합산 잔액만으로 결제가 된다고 판단하지 않습니다.",
            ['projection.cash.accounts', 'projection.upcoming'], account_id=worst['account_id'])
    elif cash['period_earmark_breach']['count']:
        blocked = next(a for a in cash['accounts'] if a['period_earmark_breach']['count'])
        result['next_action'] = _action('adjust_earmark_account', '보관할 계좌와 금액을 다시 확인하세요',
            f"{blocked['account_id']}에서 예정된 결제와 보관액을 함께 유지하지 못하는 경로가 있습니다. 다른 계좌의 잔액이 이 계좌로 자동 이동하지 않습니다.",
            ['projection.cash.accounts', 'comparison.changes'], account_id=blocked['account_id'])
    elif any(w['code'] == 'PAYMENT_AFTER_WINDOW' for w in active['warnings']):
        result['next_action'] = _action('extend_to_payment_date', '카드 결제일까지 확인 범위를 늘려 주세요',
            '미결제액은 가용금액에서 뺐지만 실제 출금일까지의 계좌 잔액은 아직 점검하지 않았습니다.',
            ['comparison.changes', 'projection.warnings'])
    elif any(w['code'] == 'CAP_BELOW_COMMITTED_SPENDING' for w in active['warnings']):
        result['next_action'] = _action('resolve_committed_spending', '예정 지출과 정한 한도가 맞지 않습니다',
            '변동 소비만 줄여서는 한도를 지킬 수 없는 경로가 있습니다. 예정 지출을 확인하거나 한도를 다시 정해 주세요.',
            ['projection.warnings'])
    elif reserve is None:
        result['next_action'] = _action('choose_protected_cash', '건드리지 않을 생활비를 먼저 정해 주세요',
            '최소 보관 금액을 정하지 않은 상태에서는 남는 잔액을 모두 써도 되는 돈으로 표시하지 않습니다.',
            ['context.reserve_source'], required_inputs=['protected_cash_krw'])
    elif cash['lowest_after_protection']['p10_krw'] < 0:
        result['next_action'] = _action('protect_cash', '추가 지출보다 남겨둘 돈부터 확보하세요',
            f"예정된 소비·미결제액·보관액을 반영하면 점검 기간 최소 여유의 하위 10% 지점이 {cash['lowest_after_protection']['p10_krw']:,}원입니다. 구매 날짜나 금액을 바꾸어 비교해 보세요.",
            ['projection.cash.lowest_after_protection', 'context.protected_cash_krw'])
    elif uncertain_budget:
        result['next_action'] = _action('confirm_budget_inputs', '먼저 누락·미분류 지출을 확인하세요',
            '빠진 지출을 0원으로 보고 예산을 늘리거나 새 구매를 권하지 않습니다.', ['warnings', 'observed_budgets'])
    else:
        pressure = [b for b in active['budgets'] if b['envelope'] in DISCRETIONARY and b['remaining_at_cutoff']['p10_krw'] < 0]
        overdue_plan = next((p for p in result['follow_up'] if p['state'] == 'over_cap'), None)
        if overdue_plan:
            result['next_action'] = _action('reset_plan', '초과한 한도는 다음 기간부터 다시 정하세요',
                f"{overdue_plan['label']}: 정한 한도보다 {-overdue_plan['remaining_krw']:,}원 더 관측됐습니다. 필수 생활비를 무리하게 줄이지 말고 실행 가능한 다음 한도를 선택하세요.",
                ['follow_up'], plan_id=overdue_plan['id'])
        elif pressure:
            b = min(pressure, key=lambda item: item['remaining_at_cutoff']['p10_krw'])
            result['next_action'] = _action('choose_spending_cap', f"{b['envelope']}의 다음 지출 한도를 정해 보세요",
                f"{twin.as_of}까지 승인 예산에서 {b['observed_remaining_krw']:,}원이 남았습니다. 예상 초과를 피하려면 예정 지출을 확인한 뒤, 이 범위에서 지킬 수 있는 추가 한도를 직접 선택하세요.",
                ['projection.budgets'], envelope=b['envelope'],
                observed_budget_left_krw=b['observed_remaining_krw'])
        else:
            samples = cash['period_account_shortfall']
            result['next_action'] = _action('keep_and_review', '이번 점검에서는 새로운 감축을 권하지 않습니다',
                f"{samples['paths']}개 경로 중 계좌 부족은 {samples['count']}개였습니다. 안전 보장은 아닙니다. 다가오는 지출을 확인하고 새 거래가 들어오면 다시 점검하세요.",
                ['projection.cash.period_account_shortfall', 'projection.upcoming'])
    return result
