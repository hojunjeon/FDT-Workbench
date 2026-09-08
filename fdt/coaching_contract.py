"""Life-first coaching inputs. No free-text-to-engine guesses or silent defaults."""
from __future__ import annotations

from datetime import date
from .errors import FDTError
from .mapping import ENVELOPES

MAX_DAYS = 90
MAX_CHANGES = 6


def fail(message: str, **details) -> None:
    raise FDTError('COACHING_INPUT', message, details)


def iso(value, field: str) -> date:
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError()
        return date.fromisoformat(value)
    except ValueError:
        fail(f'{field}는 YYYY-MM-DD 날짜여야 합니다.', field=field)


def money(value, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 10**12:
        fail(f'{field}는 0 이상 1조 원 이하의 정수여야 합니다.', field=field)
    return value


def fields(obj, allowed: set[str], required: set[str]) -> None:
    if not isinstance(obj, dict):
        fail('객체 형식의 입력이 필요합니다.')
    if set(obj) - allowed:
        fail('지원하지 않는 필드입니다. 다른 의미의 입력으로 대체하지 않습니다.', fields=sorted(set(obj)-allowed))
    if required - set(obj):
        fail('판단에 필요한 입력이 빠졌습니다.', required=sorted(required-set(obj)))


def validate_change(change: dict) -> dict:
    common = {'kind', 'label'}
    kinds = {
        'expense': {'date', 'amount_krw', 'envelope', 'account_id', 'card_id', 'payment_date', 'reserve_now'},
        'set_aside': {'amount_krw', 'account_id'},
        'income_delay': {'rule_id', 'original_date', 'new_date'},
        'spending_cap': {'envelope', 'start_date', 'end_date', 'amount_krw'},
    }
    if not isinstance(change, dict) or not isinstance(change.get('kind'), str) or change['kind'] not in kinds:
        fail('지원하는 행동은 지출, 자금 보관, 특정 입금 지연, 기간별 추가 지출 한도입니다.')
    kind = change['kind']
    required = {
        'expense': {'date', 'amount_krw'},
        'set_aside': {'amount_krw', 'account_id'},
        'income_delay': {'rule_id', 'original_date', 'new_date'},
        'spending_cap': {'envelope', 'start_date', 'end_date', 'amount_krw'},
    }[kind]
    fields(change, common | kinds[kind], {'kind'} | required)
    if 'label' in change and (not isinstance(change['label'], str) or not 1 <= len(change['label'].strip()) <= 120):
        fail('행동 이름은 1~120자여야 합니다.')
    for key in ('date', 'payment_date', 'original_date', 'new_date', 'start_date', 'end_date'):
        if key in change:
            iso(change[key], key)
    for key in ('account_id', 'card_id', 'rule_id'):
        if key in change and (not isinstance(change[key], str) or not 1 <= len(change[key]) <= 200):
            fail(f'{key}를 확인하세요.')
    if 'amount_krw' in change:
        money(change['amount_krw'], 'amount_krw')
    if 'envelope' in change and (not isinstance(change['envelope'], str) or change['envelope'] not in ENVELOPES):
        fail('승인된 예산 항목을 선택하세요. 하위분류를 임의 비율로 바꾸지 않습니다.')
    if kind == 'expense':
        if bool(change.get('account_id')) == bool(change.get('card_id')):
            fail('지출할 계좌 또는 카드 중 하나를 지정하세요.')
        if change.get('card_id') and 'payment_date' not in change:
            fail('새 카드 지출의 실제 결제일을 입력하세요. 구매일을 결제일로 간주하지 않습니다.')
        if change.get('account_id') and 'payment_date' in change:
            fail('계좌 지출은 date 당일 출금입니다. payment_date는 카드 지출에만 사용합니다.')
        if 'payment_date' in change and change['payment_date'] < change['date']:
            fail('카드 결제일은 구매일보다 빠를 수 없습니다.')
        if type(change.get('reserve_now', False)) is not bool:
            fail('reserve_now는 참/거짓이어야 합니다.')
        if change.get('reserve_now') and change.get('card_id'):
            fail('카드 미결제액과 보관액의 중복 차감을 막기 위해 카드 지출은 별도 보관을 지원하지 않습니다.')
    if kind == 'income_delay' and change['new_date'] <= change['original_date']:
        fail('변경 입금일은 기존 입금일보다 뒤여야 합니다.')
    if kind == 'spending_cap' and change['end_date'] < change['start_date']:
        fail('한도 종료일은 시작일보다 빠를 수 없습니다.')
    return change


def validate_review(request: dict) -> dict:
    fields(request, {'on_date', 'through_date', 'replay', 'protected_cash_krw', 'changes', 'paths', 'seed'}, {'on_date', 'through_date'})
    start, end = iso(request['on_date'], 'on_date'), iso(request['through_date'], 'through_date')
    if not 1 <= (end-start).days <= MAX_DAYS:
        fail('생활 코칭은 다음 1~90일을 점검합니다. 장기 목표는 가까운 점검 구간부터 나누어 주세요.')
    if type(request.get('replay', False)) is not bool:
        fail('replay는 참/거짓이어야 합니다.')
    if 'protected_cash_krw' in request:
        money(request['protected_cash_krw'], 'protected_cash_krw')
    for key, default, lo, hi in [('paths', 400, 20, 2000), ('seed', 42, 0, 2**32-1)]:
        value = request.get(key, default)
        if type(value) is not int or not lo <= value <= hi:
            fail(f'{key} 범위를 확인하세요.')
    changes = request.get('changes', [])
    if not isinstance(changes, list) or len(changes) > MAX_CHANGES:
        fail(f'한 번에 최대 {MAX_CHANGES}개 행동을 비교할 수 있습니다.')
    for change in changes:
        validate_change(change)
        for key in ('date', 'original_date', 'start_date', 'end_date'):
            if key in change and not start <= iso(change[key], key) <= end:
                fail(f'{key}는 점검 기간 안에 있어야 합니다.')
        if change['kind'] == 'spending_cap' and change['start_date'] <= request['on_date']:
            fail('일 마감 자료 이후의 추가 한도입니다. 시작일은 자료 기준일 다음 날 이후로 지정하세요.')
    caps = [c['envelope'] for c in changes if c['kind'] == 'spending_cap']
    if len(caps) != len(set(caps)):
        fail('같은 예산 항목에 한도를 중복 적용할 수 없습니다.')
    delayed = [(c['rule_id'], c['original_date']) for c in changes if c['kind'] == 'income_delay']
    if len(delayed) != len(set(delayed)):
        fail('같은 입금 건을 두 번 지연할 수 없습니다.')
    return request
