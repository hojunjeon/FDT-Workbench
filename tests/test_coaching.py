"""Behavioral acceptance tests: decisions and accounting, not ten demo prompts."""
from __future__ import annotations

import copy
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest

from fdt.coaching import Coach, review_commitments
from fdt.coaching_contract import validate_review
from fdt.coaching_projection import project, summarize
from fdt.errors import FDTError
from fdt.model import Twin
from fdt.simulation import generate_bundle
from fdt.util import read_json

ROOT = Path(__file__).resolve().parents[1]
ASOF = '2026-09-03'


def fixture_twin(*, balance=10000, rent=0, reserve=500, as_of=ASOF):
    cutoff = date.fromisoformat(as_of)
    start = date(2026, 8, 1)
    dates = [start+timedelta(days=i) for i in range((cutoff-start).days+1)]
    components = [
        {'kind': 'income', 'account_id': 'A', 'protected': True},
        {'kind': 'expense', 'account_id': 'A', 'envelope': '외식', 'pending': False, 'protected': False, 'budgeted': True},
        {'kind': 'fixed_expense', 'account_id': 'A', 'fixed_group': '주거', 'protected': True},
    ]
    daily = np.zeros((len(dates), 3), dtype=np.int64)
    daily[:, 1] = 100
    rules = [{'rule_id': 'salary', 'next_date': (cutoff+timedelta(days=4)).isoformat(), 'frequency': 'ONCE',
              'component': 0, 'amount_samples': [2000], 'expected_amount_krw': 2000, 'source': 'USER_SCHEDULE'}]
    if rent:
        rules.append({'rule_id': 'rent', 'next_date': (cutoff+timedelta(days=2)).isoformat(), 'frequency': 'ONCE',
                      'component': 2, 'amount_samples': [rent], 'expected_amount_krw': rent, 'source': 'USER_SCHEDULE'})
    transactions = [SimpleNamespace(date='2026-08-01', active=True, kind='expense', pending=False,
                                    envelope='외식', amount_krw=0, budget_amount_krw=0, account_id='A'),
                    SimpleNamespace(date=as_of, active=True, kind='expense', pending=False,
                                    envelope='외식', amount_krw=200, budget_amount_krw=200, account_id='A')]
    snapshot = {'as_of': as_of, 'source': 'USER_ASSUMPTION', 'accounts': [{'account_id': 'A', 'balance_krw': balance}],
                'cards': [{'card_id': 'C', 'kind': 'CREDIT', 'settlement_account_id': 'A', 'opening_payable_krw': 0, 'payment_delay_days': 2}],
                'known_bills': [], 'budgets': {'외식': 1000}}
    if reserve is not None:
        snapshot['reserve_krw'] = reserve
    return SimpleNamespace(as_of=as_of, snapshot=snapshot, transactions=transactions, twin_id='test-user', revision=1,
                           content_digest='test-digest', cash_requirements=lambda: [],
                           model={'components': components, 'rules': rules, 'daily': daily, 'dates': dates,
                                  'behavior': {'observation_days': len(dates)},
                                  'audit': {'pending_consumption_krw': 0, 'kind_totals_krw': {'expense': 200}}})


def request(**extra):
    return {'on_date': ASOF, 'through_date': '2026-09-10', 'replay': True, 'paths': 20, 'seed': 42, **extra}


def run(twin=None, **extra):
    return Coach(twin or fixture_twin()).review(request(**extra))


def test_period_shortfall_is_not_terminal_shortfall():
    result = run(fixture_twin(balance=1000, rent=1200))
    cash = result['projection']['cash']
    assert cash['period_account_shortfall']['count'] == 20
    assert cash['terminal_account_shortfall']['count'] == 0
    assert result['next_action']['kind'] == 'prepare_payment_account'


def test_stale_state_never_becomes_todays_spendable_money():
    result = run(on_date='2026-09-08', through_date='2026-09-15', replay=False)
    assert result['status'] == 'needs_data'
    assert result['projection'] is None
    assert result['next_action']['kind'] == 'refresh_data'
    assert result['observed_budgets'][0]['as_of'] == ASOF


def test_missing_cash_still_shows_observations_not_fabricated_balances():
    twin = fixture_twin()
    twin.cash_requirements = lambda: ['account_balances']
    result = run(twin)
    assert result['projection']['cash'] is None
    assert result['status'] == 'needs_data'
    assert result['observed_budgets'][0]['observed_remaining_krw'] == 800


def test_reserve_is_inherited_with_provenance_not_invented():
    result = run()
    assert result['context']['protected_cash_krw'] == 500
    assert result['context']['reserve_source'] == 'SNAPSHOT'
    assert any(w['code'] == 'ASSUMED_SNAPSHOT' for w in result['warnings'])
    assert run(protected_cash_krw=700)['context']['reserve_source'] == 'USER_REQUEST'


def test_unset_reserve_is_not_silent_zero():
    result = run(fixture_twin(reserve=None))
    assert result['next_action']['kind'] == 'choose_protected_cash'
    assert result['projection']['cash']['accounts'][0]['additional_one_off_room'] is None


def test_set_aside_is_not_spending_or_cash_outflow():
    result = run(changes=[{'kind': 'set_aside', 'account_id': 'A', 'amount_krw': 1000}])
    effect = result['comparison']['effect']
    assert effect['spending_change']['p50_krw'] == 0
    assert effect['terminal_cash_change']['p50_krw'] == 0
    assert effect['terminal_after_earmark_change']['p50_krw'] == -1000
    assert result['executed'] is False


def test_today_purchase_is_applied_to_opening_day():
    twin = fixture_twin()
    bundle = generate_bundle(twin, 7, 20, 42)
    branch = project(twin, bundle, [{'kind': 'expense', 'date': ASOF, 'account_id': 'A', 'envelope': '외식', 'amount_krw': 600}])
    assert np.all(branch.cash[:, 0, 0] == 9400)
    assert np.all(branch.spending[:, 0] == 600)


def test_credit_purchase_changes_available_money_not_immediate_cash():
    result = run(changes=[{'kind': 'expense', 'date': '2026-09-05', 'card_id': 'C', 'payment_date': '2026-09-20',
                          'envelope': '외식', 'amount_krw': 600}])
    effect = result['comparison']['effect']
    assert effect['terminal_cash_change']['p50_krw'] == 0
    assert effect['terminal_unencumbered_change']['p50_krw'] == -600
    assert effect['spending_change']['p50_krw'] == 600
    assert result['next_action']['kind'] == 'extend_to_payment_date'


def test_credit_settlement_happens_once():
    result = run(changes=[{'kind': 'expense', 'date': '2026-09-05', 'card_id': 'C', 'payment_date': '2026-09-09', 'amount_krw': 600}])
    assert result['comparison']['effect']['terminal_cash_change']['p50_krw'] == -600
    assert result['projection']['cash']['explanation']['card_settlement_krw'] == 600


def test_earmark_releases_when_planned_expense_is_paid():
    twin = fixture_twin()
    bundle = generate_bundle(twin, 7, 20, 42)
    branch = project(twin, bundle, [{'kind': 'expense', 'date': '2026-09-08', 'account_id': 'A',
                                  'amount_krw': 600, 'reserve_now': True}])
    assert np.all(branch.locked[:, :5, 0] == 600)
    assert np.all(branch.locked[:, 5:, 0] == 0)
    assert np.all(branch.cash[:, -1, 0] == 10700)
    assert np.all(branch.spending.sum(axis=1) == 1300)


def test_income_delay_preserves_amount_and_changes_only_one_occurrence():
    twin = fixture_twin()
    bundle = generate_bundle(twin, 7, 20, 42)
    bundle.scheduled['salary'].append((5, np.arange(20, dtype=np.int64)+100))
    original = copy.deepcopy(bundle)
    before = project(twin, bundle, [])
    after = project(twin, bundle, [{'kind': 'income_delay', 'rule_id': 'salary', 'original_date': '2026-09-07', 'new_date': '2026-09-10'}])
    assert np.array_equal(before.cash[:, -1, :], after.cash[:, -1, :])
    assert np.all(after.cash[:, 4, 0] == before.cash[:, 4, 0]-2000)
    assert any(r['date'] == '2026-09-09' for r in after.calendar if r['event_type'] == 'RECURRING_INCOME')
    assert np.array_equal(original.variable, bundle.variable)
    assert original.scheduled['salary'][0][0] == bundle.scheduled['salary'][0][0]


def test_delay_outside_window_is_not_income_destruction():
    result = run(changes=[{'kind': 'income_delay', 'rule_id': 'salary', 'original_date': '2026-09-07', 'new_date': '2026-10-01'}])
    assert any(w['code'] == 'INCOME_AFTER_WINDOW' for w in result['warnings'])
    assert result['comparison']['effect']['terminal_cash_change']['p50_krw'] == -2000


def test_start_dated_cap_does_not_rewrite_prior_days_or_fixed_bills():
    twin = fixture_twin(rent=1200)
    bundle = generate_bundle(twin, 7, 20, 42)
    before = project(twin, bundle, [])
    after = project(twin, bundle, [{'kind': 'spending_cap', 'envelope': '외식', 'start_date': '2026-09-07', 'end_date': '2026-09-10', 'amount_krw': 150}])
    assert np.array_equal(before.spending[:, :4], after.spending[:, :4])
    assert np.all(after.spending[:, 4:].sum(axis=1) == 150)
    assert np.array_equal(before.fixed, after.fixed)


def test_cap_below_scheduled_spending_is_not_claimed_feasible():
    twin = fixture_twin()
    twin.model['rules'].append({'rule_id': 'meal', 'next_date': '2026-09-08', 'frequency': 'ONCE', 'component': 1,
                                'amount_samples': [500], 'expected_amount_krw': 500, 'source': 'USER_SCHEDULE'})
    result = run(twin, changes=[{'kind': 'spending_cap', 'envelope': '외식', 'start_date': '2026-09-07', 'end_date': '2026-09-10', 'amount_krw': 100}])
    assert any(w['code'] == 'CAP_BELOW_COMMITTED_SPENDING' for w in result['warnings'])
    assert result['next_action']['kind'] == 'resolve_committed_spending'


def test_remaining_quantiles_are_computed_from_remaining_paths():
    twin = fixture_twin()
    pr = project(twin, generate_bundle(twin, 7, 20, 42), [])
    index = list(__import__('fdt.mapping', fromlist=['ENVELOPES']).ENVELOPES).index('외식')
    pr.usage[:, 1:, index] = 0
    pr.usage[:, 1, index] = np.arange(20)*100
    rows = [{'envelope': '외식', 'budget_krw': 1000, 'observed_used_krw': 200}]
    b = summarize(twin, pr, 500, rows)['budgets'][0]
    assert b['remaining_at_cutoff']['p10_krw'] < b['remaining_at_cutoff']['p90_krw']
    assert b['remaining_at_cutoff']['p10_krw'] == round(np.percentile(800-np.arange(20)*100, 10))


def test_next_month_budget_is_not_silently_approved():
    twin = fixture_twin(as_of='2026-09-30')
    result = Coach(twin).review({'on_date': '2026-09-30', 'through_date': '2026-10-07', 'replay': True, 'paths': 20})
    assert result['projection']['budget_cutoff_date'] == '2026-09-30'
    assert result['projection']['budgets'][0]['remaining_at_cutoff']['p50_krw'] == 800
    assert any(w['code'] == 'NEXT_MONTH_BUDGET_UNCONFIRMED' for w in result['warnings'])


def test_cash_bridge_reconciles_means_and_does_not_double_count_cards():
    result = run(changes=[{'kind': 'expense', 'date': ASOF, 'card_id': 'C', 'payment_date': '2026-09-08', 'amount_krw': 100}])
    b = result['projection']['cash']['explanation']
    expected = b['opening_cash_krw']+b['income_krw']+b['reimbursement_krw']+b['rounding_adjustment_krw']-sum(b[k] for k in (
        'direct_spending_krw', 'direct_fixed_krw', 'card_settlement_krw', 'debt_service_krw', 'savings_out_krw', 'cash_withdrawal_krw'))
    assert expected == b['terminal_cash_krw']
    assert b['direct_spending_krw'] == 700
    assert b['card_settlement_krw'] == 100


def test_zero_observed_shortfall_is_not_a_guarantee():
    result = run()
    assert '안전 보장' in ' '.join(w['detail'] for w in result['warnings'])
    assert result['projection']['cash']['period_account_shortfall']['count'] == 0
    assert result['context']['calibrated'] is False
    assert '불가능' not in result['next_action']['detail']


def test_saved_cap_is_reviewed_against_later_actuals_not_applied_automatically():
    twin = fixture_twin()
    plans = [{'id': 'p', 'label': '지난 외식 한도', 'action': {'kind': 'spending_cap', 'envelope': '외식',
        'start_date': '2026-09-01', 'end_date': ASOF, 'amount_krw': 100}}]
    progress = review_commitments(twin, plans)[0]
    assert progress['state'] == 'over_cap'
    assert progress['remaining_krw'] == -100
    result = Coach(twin).review(request(), plans)
    assert result['comparison'] is None
    assert result['next_action']['kind'] == 'reset_plan'


def test_incomplete_follow_up_never_claims_success():
    plans = [{'id': 'p', 'label': '이번 주', 'action': {'kind': 'spending_cap', 'envelope': '외식',
        'start_date': '2026-09-01', 'end_date': '2026-09-10', 'amount_krw': 1000}}]
    progress = review_commitments(fixture_twin(), plans)[0]
    assert progress['state'] == 'in_progress'
    assert not progress['coverage_complete']


@pytest.mark.parametrize('bad', [
    {'through_date': '2027-09-10'}, {'on_date': 'not-a-date'}, {'paths': True}, {'protected_cash_krw': -1},
    {'changes': [{'kind': 'expense', 'date': ASOF, 'amount_krw': 10, 'card_id': 'C'}]},
    {'changes': [{'kind': 'expense', 'date': ASOF, 'amount_krw': True, 'account_id': 'A'}]},
    {'changes': [{'kind': 'spending_cap', 'envelope': '외식', 'start_date': ASOF, 'end_date': '2026-09-10', 'amount_krw': 10}]},
    {'changes': [{'kind': 'spending_cap', 'envelope': '외식', 'subcategory': '배달', 'start_date': '2026-09-04', 'end_date': '2026-09-10', 'amount_krw': 10}]},
    {'changes': [{'kind': 'income_delay', 'rule_id': 'salary', 'original_date': '2026-09-07', 'new_date': '2026-09-06'}]},
    {'mode': 'optimize'},
])
def test_invalid_or_unsupported_meanings_fail_explicitly(bad):
    with pytest.raises(FDTError):
        validate_review(request(**bad))


@pytest.mark.parametrize('persona', ['001', '002', '003', '004'])
def test_coaching_runs_on_real_repository_personas(persona):
    twin = Twin.from_csv(ROOT / f'data/demo/consumer_{persona}.csv', snapshot=read_json(ROOT / f'examples/snapshot_{persona}.json'))
    result = Coach(twin).review({'on_date': twin.as_of, 'through_date': (date.fromisoformat(twin.as_of)+timedelta(days=7)).isoformat(), 'replay': True, 'paths': 20})
    assert result['next_action']['evidence_refs']
    assert result['projection']['cash'] is not None
    assert result['comparison'] is None
    assert result['executed'] is False
