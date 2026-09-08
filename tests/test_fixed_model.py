"""Fixed-expense separation and input-contract tests for the Twin model (design 2-1, 3-x)."""
import copy
import pytest
from fdt import Twin, FDTError
from fdt.ingest import normalize
from fdt.model import validate_snapshot
from conftest import FILES

GROUPS = ('주거', '공과금', '통신', '보험·사회보험', '세금', '구독·멤버십')


def test_label_columns_do_not_protect_or_group(row, snapshot):
    labelled = dict(row, is_fixed='TRUE', is_recurring='TRUE')
    plain = {k: v for k, v in row.items() if k not in ('is_fixed', 'is_recurring', 'spend_pattern')}
    a = Twin([normalize(labelled)], '2026-09-06', snapshot)
    b = Twin([normalize(plain)], '2026-09-06', snapshot)
    assert a.content_digest == b.content_digest
    assert a.model['components'][0]['protected'] is False and a.model['components'][0]['pending'] is False


def test_fixed_expense_component_contract(row, snapshot):
    row.update(category='주거·통신', subcategory='월세', account_id='A', card_id='', transaction_type='WITHDRAW', payment_method='ACCOUNT')
    t = Twin([normalize(row)], '2026-09-06', snapshot)
    f = t.model['components'][0]
    assert f['kind'] == 'fixed_expense' and f['fixed_group'] == '주거'
    assert f['protected'] and not f['budgeted'] and f['envelope'] is None
    items = [w for w in t.model['audit']['warnings'] if w['code'] == 'FIXED_UNSCHEDULED'][0]['details']['items']
    assert items == [{'raw_subcategory': '월세', 'fixed_group': '주거', 'count': 1, 'total_krw': 100,
                      'last_date': '2026-09-06', 'last_amount_krw': 100, 'transaction_ids': ['T1']}]
    assert t.inspect()['behavior']['fixed_groups_observed_krw']['주거'] == 100


def test_demo_fixed_rules_and_unscheduled():
    t = Twin.from_csv(FILES[2])
    fixed = [r for r in t.model['rules'] if r['kind'] == 'fixed_expense']
    assert fixed and all(r['fixed_group'] in GROUPS for r in fixed)
    unscheduled = [w for w in t.model['audit']['warnings'] if w['code'] == 'FIXED_UNSCHEDULED']
    assert [i['raw_subcategory'] for i in unscheduled[0]['details']['items']] == ['자동차세']
    assert 'impulse_labeled_count' not in t.model['behavior']


def test_no_subset_rules_for_frequent_merchants():
    t = Twin.from_csv(FILES[0])
    grocery = [r for r in t.model['rules'] if r['merchant_id'] == 'M2-13']
    assert len(grocery) == 1 and grocery[0]['frequency'] == 'INTERVAL' and len(grocery[0]['evidence']) == 11
    assert not any(r['merchant_id'] in ('M2-10', 'M2-12', 'M2-05') for r in t.model['rules'])


def test_amount_outlier_does_not_break_a_retainer():
    t = Twin.from_csv(FILES[2])
    income = [r for r in t.model['rules'] if r['kind'] == 'income']
    assert any(r['amount_samples'] == [531850] * 3 for r in income)
    assert not any(2901000 in r['amount_samples'] for r in income)


def test_replaces_transaction_ids_removes_unscheduled_from_pool(row, snapshot):
    row.update(category='세금·공과', subcategory='자동차세', account_id='A', card_id='', transaction_type='WITHDRAW', payment_method='ACCOUNT')
    base = Twin([normalize(row)], '2026-09-06', snapshot)
    assert base.model['daily'].sum() == 100
    snap = copy.deepcopy(snapshot)
    snap['schedules'] = [{'rule_id': 'manual-tax', 'kind': 'fixed_expense', 'fixed_group': '세금', 'amount_krw': 100,
                          'account_id': 'A', 'frequency': 'ONCE', 'next_date': '2027-06-01', 'replaces_transaction_ids': ['T1']}]
    t = Twin([normalize(row)], '2026-09-06', snap)
    assert t.model['daily'].sum() == 0
    assert any(r['rule_id'] == 'manual-tax' and r['fixed_group'] == '세금' for r in t.model['rules'])
    assert not [w for w in t.model['audit']['warnings'] if w['code'] == 'FIXED_UNSCHEDULED']
    bad = copy.deepcopy(snap); bad['schedules'][0]['fixed_group'] = '주거'
    with pytest.raises(FDTError) as e:
        Twin([normalize(row)], '2026-09-06', bad)
    assert e.value.code == 'REPLACEMENT_CHANNEL_MISMATCH'
    bad = copy.deepcopy(snap); bad['schedules'][0]['replaces_transaction_ids'] = ['nope']
    with pytest.raises(FDTError) as e:
        Twin([normalize(row)], '2026-09-06', bad)
    assert e.value.code == 'UNKNOWN_TRANSACTION_REPLACEMENT'


def test_snapshot_fixed_schedule_validation(snapshot):
    s = copy.deepcopy(snapshot)
    s['schedules'] = [{'rule_id': 'x', 'kind': 'fixed_expense', 'amount_krw': 1, 'account_id': 'A', 'frequency': 'ONCE', 'next_date': '2027-01-01'}]
    with pytest.raises(FDTError) as e:
        validate_snapshot(s)
    assert e.value.code == 'SCHEDULE_FIXED_GROUP_REQUIRED'
    s['schedules'][0].update(fixed_group='주거', envelope='기타')
    with pytest.raises(FDTError) as e:
        validate_snapshot(s)
    assert e.value.code == 'SCHEDULE_ENVELOPE_FORBIDDEN'
    s['schedules'][0] = dict(rule_id='y', kind='expense', envelope='기타', fixed_group='주거', amount_krw=1,
                             account_id='A', frequency='ONCE', next_date='2027-01-01')
    with pytest.raises(FDTError) as e:
        validate_snapshot(s)
    assert e.value.code == 'SCHEDULE_FIXED_GROUP_FORBIDDEN'


def test_pending_expense_kept_in_daily_but_not_budgeted(row, snapshot):
    row['confirm_status'] = 'PENDING'
    t = Twin([normalize(row)], '2026-09-06', snapshot)
    f = t.model['components'][0]
    assert f['kind'] == 'expense' and f['pending'] and f['envelope'] is None and not f['budgeted']
    assert t.model['daily'].sum() == 100 and t.model['behavior']['pending_consumption_krw'] == 100


def test_merchant_classifications_only_confirmed(row, snapshot):
    confirmed = dict(row, confirm_status='CONFIRMED')
    auto = dict(row, transaction_id='T2', confirm_status='AUTO', transaction_time='10:00')
    t = Twin([normalize(confirmed), normalize(auto)], '2026-09-06', snapshot)
    assert t.inspect()['merchant_classifications'] == [
        {'merchant_id': 'M', 'subcategory': '점심', 'envelope': '외식', 'fixed_group': None, 'confirmed_count': 1, 'last_date': '2026-09-06'}]
