from __future__ import annotations
import csv
import io
import re
from dataclasses import asdict, dataclass
from datetime import date, time
from pathlib import Path
from typing import Any, Iterable
from .errors import FDTError
from .mapping import FIXED_GROUPS, classify, fixed_group
from .util import digest, warning

REQUIRED = {'user_id','transaction_id','source','transaction_type','transaction_date','transaction_time',
            'category','subcategory','merchant','merchant_id','amount_krw','account_id','card_id',
            'confirm_status','status'}
OPTIONAL = {'exclude_tag':'NONE','direction':None,'payment_method':None,'to_account_id':None}
IGNORED = ('is_fixed','is_recurring','spend_pattern','classify_source')
ENUMS = {
    'source': {'SEED','LIVE'}, 'direction': {'INCOME','EXPENSE','TRANSFER'},
    'transaction_type': {'CARD','WITHDRAW','DEPOSIT','TRANSFER_IN','TRANSFER_OUT','TRANSFER','CARD_BILL','CARD_SETTLEMENT'},
    'payment_method': {'CARD','ACCOUNT'}, 'confirm_status': {'AUTO','CONFIRMED','PENDING'},
    'exclude_tag': {'NONE','INTERNAL_TRANSFER','SELF_TRANSFER','DUTCH','EMERGENCY','CARRYOVER'},
    'status': {'NORMAL','CANCELED'}, 'spend_pattern': {'ROUTINE','PLANNED','FIXED','IMPULSE','N/A'}
}

@dataclass(frozen=True)
class Transaction:
    id: str
    user_id: str
    date: str
    time: str
    source: str
    kind: str
    amount_krw: int
    account_id: str | None
    card_id: str | None
    to_account_id: str | None
    merchant_id: str
    envelope: str | None
    subcategory: str | None
    fixed_group: str | None
    pending: bool
    raw_category: str
    raw_subcategory: str
    is_fixed: bool
    is_recurring: bool
    spend_pattern: str
    confirm_status: str
    exclude_tag: str
    active: bool
    mapping_fallback: bool
    budget_amount_krw: int
    origin: dict
    raw: dict

    def as_dict(self) -> dict:
        return asdict(self)


def normalize(row: dict[str, Any], origin: dict | None = None) -> Transaction:
    missing = REQUIRED - set(row)
    if missing:
        raise FDTError('MISSING_COLUMNS', '필수 CSV 열이 없습니다.', {'columns': sorted(missing)})
    r = {k: str(v).strip() if v is not None else '' for k,v in row.items()}
    for key, default in OPTIONAL.items():
        if key not in r or r[key] == '':
            r[key] = default
    for key, values in ENUMS.items():
        if key in IGNORED or key not in r or r[key] is None:
            continue
        if r[key] not in values:
            raise FDTError('INVALID_ENUM', f'{key}: {r[key]}')
    for key in ('transaction_id','user_id'):
        if not r[key] or len(r[key]) > 200:
            raise FDTError('INVALID_ID', key)
    if len(r['merchant_id']) > 200:
        raise FDTError('INVALID_ID', 'merchant_id')
    if not re.fullmatch(r'\d+', r['amount_krw']) or int(r['amount_krw']) > 10**12:
        raise FDTError('INVALID_AMOUNT', 'amount_krw는 0~10^12 정수여야 합니다.')
    try:
        d = date.fromisoformat(r['transaction_date'])
        ts = r['transaction_time'].split(':')
        if len(ts) not in (2,3):
            raise ValueError('time')
        t = time(*map(int, ts))
    except (ValueError, TypeError) as e:
        raise FDTError('INVALID_DATETIME', f'{r["transaction_date"]} {r["transaction_time"]}') from e
    account, card = r['account_id'] or None, r['card_id'] or None
    if not card and not account:
        raise FDTError('MISSING_CHANNEL', 'card_id 또는 account_id가 필요합니다.')
    channel = 'CARD' if card else 'ACCOUNT'
    if r['payment_method'] and r['payment_method'] != channel:
        raise FDTError('INCONSISTENT_RECORD', 'payment_method와 카드/계좌 식별자가 일치하지 않습니다.')
    if r['transaction_type'] == 'CARD' and not card:
        raise FDTError('MISSING_CHANNEL', 'CARD 거래는 card_id가 필요합니다.')
    if card and not r['merchant_id']:
        raise FDTError('INVALID_ID', 'merchant_id')

    transaction_type = r['transaction_type']
    derived_direction = _derived_direction(transaction_type)
    direction = r['direction'] or derived_direction
    if r['direction'] and direction not in _allowed_directions(transaction_type):
        raise FDTError('INCONSISTENT_RECORD', 'direction과 transaction_type이 일치하지 않습니다.')

    kind = 'expense'
    if transaction_type in ('CARD_BILL','CARD_SETTLEMENT'):
        kind = 'card_settlement'
    elif r['subcategory'] == 'ATM 출금':
        kind = 'cash_withdrawal'
    elif r['subcategory'] == '대출 상환':
        kind = 'debt_service'
    elif (r['exclude_tag'] in ('INTERNAL_TRANSFER','SELF_TRANSFER') or direction == 'TRANSFER'
            or (transaction_type in ('TRANSFER','TRANSFER_OUT') and r['category'] == '저축·투자')):
        # Own-account moves are known only from the user's tag (or an explicit TRANSFER direction /
        # savings category).  A TRANSFER_OUT to a third party (축의금, 회비, 모임 정산) stays an expense.
        kind = 'savings_out' if r['category'] == '저축·투자' else 'internal_transfer'
    elif transaction_type in ('DEPOSIT','TRANSFER_IN') and direction == 'INCOME':
        kind = 'reimbursement' if r['subcategory'] == '모임 정산' else 'income'

    raw_fixed_group = fixed_group(r['subcategory'])
    pending = kind == 'expense' and r['confirm_status'] == 'PENDING'
    if kind == 'expense' and not pending and raw_fixed_group:
        kind, env, sub, mapping_source = 'fixed_expense', None, None, 'fixed_group'
    elif kind == 'expense' and pending:
        env, sub, mapping_source = None, None, 'pending'
    elif kind == 'expense':
        env, sub, mapping_source = classify(r['category'], r['subcategory'])
    else:
        env, sub, mapping_source = None, None, 'not_applicable'
    amount = int(r['amount_krw'])
    active = r['status'] == 'NORMAL'
    fallback = mapping_source == 'category_fallback'
    budget = amount if active and kind == 'expense' and not pending and r['exclude_tag'] == 'NONE' else 0
    return Transaction(r['transaction_id'], r['user_id'], d.isoformat(), t.isoformat(), r['source'], kind, amount,
        account, card, r.get('to_account_id') or None, r['merchant_id'], env, sub, raw_fixed_group if kind == 'fixed_expense' else None,
        pending, r['category'], r['subcategory'], False, False, 'N/A', r['confirm_status'], r['exclude_tag'], active,
        fallback, budget, origin or {}, r)


def _derived_direction(transaction_type: str) -> str:
    # Without a direction column a TRANSFER_OUT is an outflow to someone; only the bare
    # TRANSFER type (or the user's INTERNAL_TRANSFER tag) marks an own-account move.
    return 'INCOME' if transaction_type in ('DEPOSIT','TRANSFER_IN') else (
        'TRANSFER' if transaction_type == 'TRANSFER' else 'EXPENSE')


def _allowed_directions(transaction_type: str) -> set[str]:
    if transaction_type == 'DEPOSIT':
        return {'INCOME'}
    if transaction_type in ('TRANSFER','TRANSFER_OUT','TRANSFER_IN'):
        return {'INCOME','EXPENSE','TRANSFER'} if transaction_type == 'TRANSFER_IN' else {'EXPENSE','TRANSFER'}
    return {'EXPENSE'}


def transaction_signature(t: Transaction) -> str:
    # Provenance and nonfinancial persona metadata cannot alter transaction identity.
    relevant = {k:v for k,v in t.as_dict().items() if k not in ('origin','raw')}
    return digest(relevant)


def record_signature(t: Transaction) -> str:
    return digest({
        'id': t.id, 'user_id': t.user_id, 'date': t.date, 'time': t.time,
        'source': t.source, 'amount_krw': t.amount_krw, 'account_id': t.account_id,
        'card_id': t.card_id, 'to_account_id': t.to_account_id,
        'merchant_id': t.merchant_id, 'transaction_type': t.raw.get('transaction_type'),
        'active': t.active,
    })


def deduplicate(transactions: Iterable[Transaction]) -> tuple[list[Transaction], int]:
    by_id: dict[str, Transaction] = {}
    count = 0
    for t in transactions:
        if t.id in by_id:
            if transaction_signature(t) != transaction_signature(by_id[t.id]):
                raise FDTError('TRANSACTION_CONFLICT', f'동일 ID의 내용이 다릅니다: {t.id}')
            count += 1
        else:
            by_id[t.id] = t
    users = {t.user_id for t in by_id.values()}
    if len(users) != 1:
        raise FDTError('MIXED_OR_EMPTY_USER', '하나의 Twin에는 정확히 한 사용자의 거래가 필요합니다.')
    if sum(t.amount_krw for t in by_id.values()) > 9_000_000_000_000_000:
        raise FDTError('MONEY_RANGE_LIMIT','관측 금액 합계가 안전한 JSON 정수 범위를 초과했습니다.')
    if len(by_id) > 10000:
        raise FDTError('DATA_LIMIT', '최대 10,000 거래입니다.')
    return sorted(by_id.values(), key=lambda t:(t.date,t.time,t.id)), count


def load_csv(paths: list[str | Path]) -> tuple[list[Transaction], dict]:
    txs, sources, ignored_columns = [], [], set()
    for path in sorted(map(Path, paths), key=str):
        try:
            raw = path.read_bytes()
            reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''))
        except (OSError, UnicodeError) as e:
            raise FDTError('CSV_READ_ERROR', str(e)) from e
        if reader.fieldnames is None or not REQUIRED.issubset(reader.fieldnames):
            raise FDTError('MISSING_COLUMNS', str(path.name), {'required': sorted(REQUIRED)})
        ignored_columns.update(set(reader.fieldnames) & set(IGNORED))
        source_id = __import__('hashlib').sha256(raw).hexdigest()
        n = 0
        for n, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise FDTError('CSV_SHAPE', f'{path.name}:{n} 열 수 오류')
            try:
                txs.append(normalize(row, {'file':path.name,'row':n,'sha256':source_id}))
            except FDTError as e:
                e.details.update({'file':path.name,'row':n})
                raise
        sources.append({'file':path.name,'sha256':source_id,'rows':max(0,n-1)})
    result, dup = deduplicate(txs)
    return result, {'sources':sources,'duplicates_ignored':dup,'input_rows':len(txs),
                    'ignored_columns':sorted(ignored_columns)}


def audit(transactions: list[Transaction]) -> dict:
    active = [t for t in transactions if t.active]
    kinds = {k:sum(t.amount_krw for t in active if t.kind==k) for k in sorted({t.kind for t in active})}
    pending = [t for t in active if t.kind=='expense' and t.pending]
    consumption = [t for t in active if t.kind=='expense' and not t.pending]
    fixed = [t for t in active if t.kind=='fixed_expense']
    pending_amount = sum(t.amount_krw for t in pending)
    env = {k:sum(t.amount_krw for t in consumption if t.envelope==k) for k in sorted({t.envelope for t in consumption if t.envelope})}
    raw_categories = {k:sum(t.amount_krw for t in consumption if t.raw_category==k) for k in sorted({t.raw_category for t in consumption})}
    raw_subcategories = {k:sum(t.amount_krw for t in consumption if t.raw_subcategory==k) for k in sorted({t.raw_subcategory for t in consumption})}
    fixed_totals = {group:sum(t.amount_krw for t in fixed if t.fixed_group==group) for group in FIXED_GROUPS}
    fixed_raw_subcategories = {k:sum(t.amount_krw for t in fixed if t.raw_subcategory==k)
                               for k in sorted({t.raw_subcategory for t in fixed})}
    fallback_pairs = {}
    for t in consumption:
        if t.mapping_fallback:
            key = f'{t.raw_category} > {t.raw_subcategory}'
            fallback_pairs[key] = fallback_pairs.get(key, 0) + 1
    exact_count = len(consumption) - sum(t.mapping_fallback for t in consumption)
    mapping_quality = {
        'expense_rows': len(consumption),
        'exact_rows': exact_count,
        'fallback_rows': len(consumption) - exact_count,
        'exact_ratio': (exact_count / len(consumption)) if consumption else 1.0,
        'fallback_pairs': fallback_pairs,
    }
    warnings = []
    if pending:
        warnings.append(warning('PENDING_CLASSIFICATION', f'잠정 분류 {len(pending)}건, {pending_amount}원입니다.',
                                count=len(pending), krw=pending_amount))
    for flag, predicate, message in [
        ('MAPPING_FALLBACK',lambda t:t.mapping_fallback,'일부 세부분류는 원본 상위 카테고리 기준으로 fallback 매핑했습니다.'),
        ('CASH_WITHDRAWAL_UNOBSERVED',lambda t:t.kind=='cash_withdrawal','ATM 인출은 소비가 아니며 이후 현금 소비는 미관측입니다.'),
        ('DEBT_SPLIT_UNKNOWN',lambda t:t.kind=='debt_service','원리금 납부를 원금/이자로 분해할 수 없습니다.'),
        ('REIMBURSEMENT_UNMATCHED',lambda t:t.kind=='reimbursement','정산 입금에 원거래 링크가 없어 소비 봉투를 임의 상계하지 않습니다.')]:
        cnt = sum(predicate(t) for t in active)
        if cnt: warnings.append(warning(flag,message,count=cnt))
    assert sum(env.values()) == kinds.get('expense',0) - pending_amount
    return {'rows':len(transactions),'active_rows':len(active),'kind_totals_krw':kinds,
            'envelope_totals_krw':env,'raw_category_totals_krw':raw_categories,
            'raw_subcategory_totals_krw':raw_subcategories,'mapping_quality':mapping_quality,
            'fixed_totals_krw':fixed_totals,'fixed_raw_subcategory_totals_krw':fixed_raw_subcategories,
            'pending_consumption_krw':pending_amount,'pending_rows':len(pending),
            'reconciliation_difference_krw':sum(env.values())-(kinds.get('expense',0)-pending_amount),
            'warnings':warnings}
