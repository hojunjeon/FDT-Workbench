import copy
import numpy as np
import pytest
from fdt import FDTError
from fdt.simulation import simulate,generate_bundle


def test_debit_immediate(make_exact):
    t,b=make_exact();s=simulate(t,b)
    assert (s.cash_total[:,1:]==400).all();assert (s.payable==0).all()
    assert s.consumption.sum()==2000


def test_credit_authorization_then_billing_once(make_exact):
    t,b=make_exact(card_kind='CREDIT');s=simulate(t,b)
    assert (s.cash_total[:,1:8]==500).all()
    assert (s.payable[:,1:8]==100).all()
    assert (s.cash_total[:,8]==400).all();assert (s.payable[:,8]==0).all()
    assert (s.consumption.sum(axis=1)==100).all();assert (s.free[:,1:]==400).all()
    assert s.calendar[0]['date']=='2026-09-14'


def test_issue_date_not_payment_date(make_exact):
    t,b=make_exact(card_kind='CREDIT');t.snapshot['cards'][0]['payment_delay_days']=3
    s=simulate(t,b)
    assert s.calendar[0]['date']=='2026-09-17';assert (s.cash_total==500).all()
    assert (s.payable[:,-1]==100).all()


def test_known_bill_not_consumption(make_exact):
    t,b=make_exact(card_kind='CREDIT',amount=0)
    t.snapshot['cards'][0]['opening_payable_krw']=100
    t.snapshot['known_bills']=[{'bill_id':'b1','card_id':'C','due_date':'2026-09-08','amount_krw':100}]
    s=simulate(t,b)
    assert (s.cash_total[:,2:]==400).all();assert (s.consumption==0).all()
    assert (s.free==400).all()


def test_internal_transfer_conserves_cash(make_exact):
    t,b=make_exact(kind='internal_transfer',extra_accounts=[{'account_id':'B','balance_krw':100}])
    s=simulate(t,b)
    assert (s.cash_total==600).all();assert (s.cash[:,1:,0]==400).all();assert (s.cash[:,1:,1]==200).all()
    assert (s.resource==0).all() and (s.consumption==0).all()

@pytest.mark.parametrize('kind',['savings_out','cash_withdrawal','debt_service'])
def test_non_consumption_outflow_reduces_managed_cash(make_exact,kind):
    t,b=make_exact(kind=kind);s=simulate(t,b)
    assert (s.cash_total[:,-1]==400).all();assert s.consumption.sum()==0


def test_negative_cash_not_clipped(make_exact):
    t,b=make_exact(opening=50);s=simulate(t,b)
    assert (s.cash_total[:,-1]==-50).all();assert s.total_short.all()


def test_initial_shortfall_included(make_exact):
    t,b=make_exact(kind='income',opening=-10,amount=100);s=simulate(t,b)
    assert (s.cash_total[:,1:]==90).all();assert s.total_short.all()


def test_total_cash_not_account_sufficiency(make_exact):
    t,b=make_exact(opening=50,extra_accounts=[{'account_id':'B','balance_krw':1000}]);s=simulate(t,b)
    assert not s.total_short.any();assert s.any_account_short.all()


def test_fixed_protected_variable_reduced(make_exact):
    t,b=make_exact();a=simulate(t,b);c=simulate(t,b,{'expense_reductions':{'외식':.2}})
    assert (c.cash_total[:,-1]-a.cash_total[:,-1]==20).all()
    t.model['components'][0]['protected']=True
    np.testing.assert_equal(simulate(t,b,{'expense_reductions':{'외식':.9}}).resource,a.resource)


def test_identical_random_bundle_and_no_mutation(tiny):
    digest=tiny.content_digest
    b=generate_bundle(tiny,30,20,123);other=generate_bundle(tiny,30,20,123)
    np.testing.assert_equal(b.variable,other.variable)
    a=simulate(tiny,b);c=simulate(tiny,b,{})
    np.testing.assert_equal(a.resource,c.resource)
    simulate(tiny,b,{'expense_reductions':{'외식':.5}})
    assert tiny.content_digest==digest
    np.testing.assert_equal(b.variable,other.variable)


def test_seed_changes_draws():
    from conftest import FILES
    from fdt import Twin
    t=Twin.from_csv(FILES[0]);a=generate_bundle(t,30,20,1);b=generate_bundle(t,30,20,2)
    assert not np.array_equal(a.variable,b.variable)


def test_invalid_scenario_dates_and_rules(tiny):
    b=generate_bundle(tiny,3,20,1)
    with pytest.raises(FDTError):simulate(tiny,b,{'cancel_rule_ids':['unknown']})
    with pytest.raises(FDTError):simulate(tiny,b,{'cash_events':[{'date':'2027-01-01','account_id':'A','amount_krw':1,'direction':'INCOME'}]})
    with pytest.raises(FDTError):simulate(tiny,b,{'cash_events':[{'date':'2026-09-07','account_id':'OTHER','amount_krw':1,'direction':'INCOME'}]})


def test_memory_guard(tiny):
    with pytest.raises(FDTError):generate_bundle(tiny,365,2000,1)

def test_cancel_recurring_keeps_bundle_and_twin_intact():
    from conftest import FILES,ROOT
    from fdt import Twin
    from fdt.util import read_json
    t=Twin.from_csv(FILES[0],snapshot=read_json(ROOT/'examples/snapshot_001.json'))
    rule=next(r for r in t.model['rules'] if r['expected_amount_krw']==700000)
    digest=t.content_digest;b=generate_bundle(t,30,20,1)
    baseline=simulate(t,b);branch=simulate(t,b,{'cancel_rule_ids':[rule['rule_id']]})
    assert (branch.resource[:,-1]-baseline.resource[:,-1]==700000).all()
    assert t.content_digest==digest
    assert any(e['reference_id']==rule['rule_id'] for e in baseline.calendar)
    assert not any(e['reference_id']==rule['rule_id'] for e in branch.calendar)


def test_explicit_cash_event_preserves_invariant(make_exact):
    t,b=make_exact();s=simulate(t,b,{'cash_events':[{'date':'2026-09-08','account_id':'A','amount_krw':1000,'direction':'INCOME'}]})
    assert (s.cash_total[:,-1]==1400).all()
    np.testing.assert_equal(s.free-s.free[:,[0]],s.resource)


def test_unknown_balance_only_relative(make_exact):
    t,b=make_exact();t.snapshot=None;s=simulate(t,b)
    assert s.cash is None and s.payable is None and s.free is None
    assert (s.resource[:,-1]==-100).all()


def test_schedule_dates_beyond_horizon_labeled(make_exact):
    t,b=make_exact(card_kind='CREDIT',horizon=2);s=simulate(t,b)
    assert not s.calendar[0]['within_forecast_horizon']
    assert 'through_forecast_end_only' in s.calendar[0]['amount_coverage']

@pytest.mark.parametrize('seed',range(8))
def test_paired_reduction_monotonicity_on_actual_personas(seed):
    from conftest import FILES,ROOT
    from fdt import Twin
    from fdt.util import read_json
    index=seed%4
    t=Twin.from_csv(FILES[index],snapshot=read_json(ROOT/f'examples/snapshot_{index+1:03}.json'))
    bundle=generate_bundle(t,45,20,seed)
    base=simulate(t,bundle)
    cut=simulate(t,bundle,{'expense_reductions':{'외식':.37,'쇼핑':.19,'취미·여가':.4}})
    assert (cut.consumption<=base.consumption).all()
    assert (cut.resource>=base.resource).all()
    assert (cut.free>=base.free).all()
    less_income=simulate(t,bundle,{'income_multiplier':.5})
    assert (less_income.resource<=base.resource).all()


def test_integer_overflow_and_json_precision_guard(row):
    from fdt import Twin,Engine
    from fdt.ingest import normalize
    txs=[normalize({**row,'transaction_id':f'T{i}','amount_krw':str(10**12)}) for i in range(100)]
    t=Twin(txs,'2026-09-06')
    with pytest.raises(FDTError,match='안전한'):
        Engine(t).run({'mode':'forecast','horizon_days':365,'paths':20,'scenario':{'expense_multiplier':5}})
