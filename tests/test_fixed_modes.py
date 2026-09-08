"""Engine output contract for fixed expenses and pending consumption (contract 6)."""
import csv
from fdt import Twin
from fdt.engine import Engine
from fdt.ingest import normalize

GROUPS = ['주거', '공과금', '통신', '보험·사회보험', '세금', '구독·멤버십']


def test_fixed_metrics_and_groups_dataset(row, snapshot):
    rent = dict(row, transaction_id='R1', category='주거·통신', subcategory='월세', account_id='A', card_id='', transaction_type='WITHDRAW', payment_method='ACCOUNT', amount_krw='700')
    t = Twin([normalize(row), normalize(rent)], '2026-09-06', snapshot)
    r = Engine(t).run({'mode': 'forecast', 'paths': 20, 'horizon_days': 3, 'seed': 1})
    m = r['metrics']
    assert m['expected_expense_krw']['basis'] == 'simulation_consumption_only'
    assert m['total_fixed_p50_krw']['basis'] == 'simulation_fixed_only'
    assert m['total_outflow_p50_krw']['value'] >= m['total_fixed_p50_krw']['value'] and m['fixed_share_of_outflow']['unit'] == 'ratio'
    assert [g['group'] for g in r['datasets']['fixed_groups']] == GROUPS
    assert any(v['id'] == 'fixed_groups' for v in r['visualizations'])
    assert [v for v in r['visualizations'] if v['id'] == 'envelopes'][0]['title'].endswith('(고정지출 제외)')
    codes = [w['code'] for w in r['warnings']]
    assert 'FIXED_UNSCHEDULED' in codes and 'IGNORED_LABEL_COLUMNS' not in codes


def test_pending_share_high_downgrades_status(row, snapshot):
    pending = dict(row, transaction_id='P1', confirm_status='PENDING', amount_krw='50')
    r = Engine(Twin([normalize(row), normalize(pending)], '2026-09-06', snapshot)).run({'mode': 'forecast', 'paths': 20, 'horizon_days': 3})
    codes = [w['code'] for w in r['warnings']]
    assert r['status'] == 'partial' and codes.count('PENDING_SHARE_HIGH') == 1
    assert r['metrics']['pending_consumption_krw']['value'] == 50
    assert sum(e['p50_krw'] for e in r['datasets']['envelopes']) <= r['metrics']['total_expense_p50_krw']['value']


def test_risk_fixed_coverage_and_default_stress(row, snapshot):
    rent = dict(row, transaction_id='R1', category='주거·통신', subcategory='월세', account_id='A', card_id='', transaction_type='WITHDRAW', payment_method='ACCOUNT', amount_krw='100')
    r = Engine(Twin([normalize(rent)], '2026-09-06', snapshot)).run({'mode': 'risk', 'paths': 20, 'horizon_days': 30})
    cov = r['metrics']['fixed_coverage_months']
    assert cov['unit'] == 'months' and cov['value'] is not None and cov['value'] > 0
    assert [s['name'] for s in r['datasets']['stress_scenarios']][-1] == '고정지출 10% 인상 가정'


def test_ignored_label_columns_warning_once(tmp_path, row):
    path = tmp_path / 'labelled.csv'; row = dict(row, is_fixed='TRUE')
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(row)); w.writeheader(); w.writerow(row)
    r = Engine(Twin.from_csv(path)).run({'mode': 'forecast', 'paths': 20, 'horizon_days': 3})
    assert [w['code'] for w in r['warnings']].count('IGNORED_LABEL_COLUMNS') == 1
