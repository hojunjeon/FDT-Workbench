"""Fixed-expense aggregation, multipliers, overrides and pending handling in the simulator (contract 5)."""
import numpy as np
import pytest
from fdt import FDTError
from fdt.simulation import generate_bundle, simulate


def _fixed_flow(kind='fixed_expense', group='주거'):
    return {'kind': kind, 'envelope': None, 'fixed_group': group, 'pending': False, 'account_id': 'A',
            'card_id': None, 'to_account_id': None, 'protected': True, 'budgeted': False}


def test_fixed_expense_is_separate_from_consumption_and_immune_to_reductions(make_exact):
    t, b = make_exact(); t.model['components'] = [_fixed_flow()]
    s = simulate(t, b)
    assert s.fixed[:, 0].tolist() == [100] * 20 and s.consumption.sum() == 0 and s.by_envelope.sum() == 0
    assert s.fixed_by_group[:, 0, 0].tolist() == [100] * 20 and (s.cash_total[:, -1] == 400).all()
    cut = simulate(t, b, {'expense_reductions': {'외식': .9}, 'expense_multiplier': 2})
    np.testing.assert_equal(cut.fixed, s.fixed)
    up = simulate(t, b, {'fixed_multiplier': 1.5})
    assert up.fixed[:, 0].tolist() == [150] * 20 and (up.cash_total[:, -1] == 350).all()
    assert up.daily_rows('2026-09-06')[1]['cumulative_fixed_p50_krw'] == 150


def test_fixed_expense_on_credit_card_is_billed(make_exact):
    t, b = make_exact(card_kind='CREDIT'); f = _fixed_flow(); f.update(account_id=None, card_id='C'); t.model['components'] = [f]
    s = simulate(t, b)
    assert (s.payable[:, 1] == 100).all() and any(c['event_type'] == 'CARD_BILL' for c in s.calendar)


def test_pending_expense_counts_in_consumption_but_not_envelopes(make_exact):
    t, b = make_exact(); t.model['components'][0].update(pending=True, envelope=None, budgeted=False)
    s = simulate(t, b)
    assert s.pending[:, 0].tolist() == [100] * 20 and s.consumption[:, 0].tolist() == [100] * 20 and s.by_envelope.sum() == 0
    assert (s.cash_total[:, -1] == 400).all()


def test_fixed_overrides_and_conflicts(tiny):
    b = generate_bundle(tiny, 30, 20, 1)
    tiny.model['rules'] = [{'rule_id': 'rent', 'frequency': 'ONCE', 'next_date': '2026-09-10', 'day_of_month': None, 'interval_days': None,
                            'component': 0, 'amount_samples': [700], 'expected_amount_krw': 700, 'evidence': [], 'source': 'INFERRED',
                            'evidence_level': 'repeated', 'merchant_id': 'M', 'kind': 'fixed_expense', 'fixed_group': '주거'}]
    tiny.model['components'] = [_fixed_flow()]
    b.scheduled = {'rent': [(3, np.full(20, 700, dtype=np.int64))]}
    b.variable[:] = 0  # isolate the scheduled rent from the bootstrapped residual
    base = simulate(tiny, b)
    over = simulate(tiny, b, {'fixed_overrides': [{'rule_id': 'rent', 'amount_krw': 750}], 'fixed_multiplier': 2})
    assert base.fixed[:, 3].tolist() == [700] * 20 and over.fixed[:, 3].tolist() == [1500] * 20
    assert [c for c in over.calendar if c['reference_id'] == 'rent'][0]['expected_amount_krw'] == 1500
    for scenario, code in [({'fixed_overrides': [{'rule_id': 'nope', 'amount_krw': 1}]}, 'UNKNOWN_RULE'),
                           ({'fixed_overrides': [{'rule_id': 'rent', 'amount_krw': 1}, {'rule_id': 'rent', 'amount_krw': 2}]}, 'DUPLICATE_OVERRIDE'),
                           ({'fixed_overrides': [{'rule_id': 'rent', 'amount_krw': 1}], 'cancel_rule_ids': ['rent']}, 'OVERRIDE_CANCEL_CONFLICT')]:
        with pytest.raises(FDTError) as e:
            simulate(tiny, b, scenario)
        assert e.value.code == code
    tiny.model['components'][0].update(kind='expense', envelope='외식', fixed_group=None)
    tiny.model['rules'][0].update(kind='expense', fixed_group=None)
    with pytest.raises(FDTError) as e:
        simulate(tiny, b, {'fixed_overrides': [{'rule_id': 'rent', 'amount_krw': 1}]})
    assert e.value.code == 'OVERRIDE_NOT_FIXED'


def test_cash_event_with_fixed_group(tiny):
    b = generate_bundle(tiny, 10, 20, 1)
    s = simulate(tiny, b, {'cash_events': [{'date': '2026-09-08', 'account_id': 'A', 'amount_krw': 50, 'direction': 'EXPENSE', 'fixed_group': '공과금'}]})
    assert s.fixed[:, 1].tolist() == [50] * 20 and s.fixed_by_group[:, 1, 1].tolist() == [50] * 20 and s.by_envelope[:, 1, -1].sum() == 0
    assert [c for c in s.calendar if c['reference_id'] == 'cash_event'][0]['event_type'] == 'SCENARIO_FIXED_EXPENSE'
    with pytest.raises(FDTError) as e:
        simulate(tiny, b, {'cash_events': [{'date': '2026-09-08', 'account_id': 'A', 'amount_krw': 50, 'direction': 'INCOME', 'fixed_group': '공과금'}]})
    assert e.value.code == 'SCENARIO_FIXED_GROUP_INCOME'
