import copy
import json
from pathlib import Path
import pytest
from fdt import Twin,Engine,FDTError
from fdt.util import read_json,validate
from conftest import FILES,ROOT

@pytest.mark.parametrize('mode',['forecast','what_if','goal','risk','optimize'])
@pytest.mark.parametrize('index',range(4))
def test_four_users_five_modes(index,mode):
    t=Twin.from_csv(FILES[index],snapshot=read_json(ROOT/f'examples/snapshot_{index+1:03}.json'))
    req=read_json(ROOT/f'examples/requests/{mode}.json');req.update(paths=30,horizon_days=30)
    result=Engine(t).run(req)
    assert result['status']=='ok'
    validate('result',result);json.dumps(result,allow_nan=False)
    for m in result['metrics'].values():
        if m['unit']=='probability' and m['value'] is not None: assert 0<=m['value']<=1
    for row in result['datasets']['projection']:
        assert row['cash_balance_p10_krw']<=row['cash_balance_p50_krw']<=row['cash_balance_p90_krw']
    Engine.validate_visualizations(result)

@pytest.mark.parametrize('mode',['forecast','what_if','goal','risk','optimize'])
def test_missing_snapshot_is_not_zero(mode):
    t=Twin.from_csv(FILES[0]);req=read_json(ROOT/f'examples/requests/{mode}.json');req.update(paths=20,horizon_days=7)
    r=Engine(t).run(req)
    assert r['metrics']['terminal_cash_p50_krw']['value'] is None
    assert r['status']==('insufficient_data' if mode in ('goal','optimize') else 'partial')
    assert r['datasets']['projection'][0]['cash_balance_p50_krw'] is None

@pytest.mark.parametrize('key,value',[
    ('mode','routing'),('horizon_days',0),('horizon_days',366),('horizon_days',True),
    ('paths',0),('paths',2001),('seed',-1),('bad','field'),
    ('scenario',{'expense_reductions':{'외식':1.1}}),('scenario',{'income_multiplier':float('nan')}),
    ('goal',{'target_krw':100}),('optimization',{}),('stress_scenarios',[])
])
def test_bad_requests_rejected(tiny,key,value):
    req={'mode':'forecast',key:value}
    with pytest.raises(FDTError):Engine(tiny).run(req)

def test_goal_requires_target(tiny):
    with pytest.raises(FDTError):Engine(tiny).run({'mode':'goal'})

def test_zero_change_what_if(tiny):
    before=tiny.to_dict()
    r=Engine(tiny).run({'mode':'what_if','scenario':{},'horizon_days':8,'paths':20})
    assert r['metrics']['paired_terminal_cash_delta_p50_krw']['value']==0
    assert r['datasets']['projection']==r['datasets']['branch_projection']
    assert tiny.to_dict()==before

def test_reproducible_result(tiny):
    e=Engine(tiny);req={'mode':'forecast','horizon_days':10,'paths':20,'seed':77}
    assert e.run(req)==e.run(req)

def test_optimize_safe_baseline_selects_no_change(row,snapshot):
    from fdt.ingest import normalize
    snapshot['accounts'][0]['balance_krw']=10**8
    t=Twin([normalize(row)],'2026-09-06',snapshot)
    r=Engine(t).run({'mode':'optimize','horizon_days':7,'paths':20})
    assert r['decision']['feasibility']=='feasible'
    assert all(v==0 for v in r['decision']['selected_reductions'].values())
    assert r['metrics']['selected_expected_saving_krw']['value']==0

def test_impossible_optimization_has_no_fake_solution(tiny):
    r=Engine(tiny).run({'mode':'optimize','goal':{'target_krw':10**12},'paths':20,'horizon_days':7})
    assert r['decision']['feasibility']=='infeasible'
    assert r['decision']['selected_candidate_id'] is None
    assert not r['decision']['executed']

def test_optimize_exhaustive_selected_is_minimum():
    t=Twin.from_csv(FILES[0],snapshot=read_json(ROOT/'examples/snapshot_001.json'))
    r=Engine(t).run({'mode':'optimize','paths':30,'horizon_days':30,'goal':{'target_krw':500000,'success_probability':.5}})
    rows=r['datasets']['candidates'];feasible=[x for x in rows if x['feasible']]
    assert len(rows)==27
    if feasible:
        assert r['metrics']['selected_expected_saving_krw']['value']==min(x['expected_saving_krw'] for x in feasible)

def test_optimization_candidate_limit(tiny):
    with pytest.raises(FDTError):
        Engine(tiny).run({'mode':'optimize','paths':20,'horizon_days':3,
            'optimization':{'envelopes':['외식','쇼핑','기타','교통비'],'reduction_grid':[0,.1,.2,.3,.4,.5]}})

def test_investment_shock_not_cash_injection():
    t=Twin.from_csv(FILES[2],snapshot=read_json(ROOT/'examples/snapshot_003.json'))
    r=Engine(t).run({'mode':'what_if','scenario':{'asset_shock_fraction':-.2},'paths':20,'horizon_days':7})
    assert r['metrics']['investment_mark_to_market_delta_krw']['value']==-2400000
    assert r['metrics']['paired_terminal_cash_delta_p50_krw']['value']==0

def test_goal_on_available_after_card_payable(snapshot,row):
    from fdt.ingest import normalize
    row['amount_krw']='0'
    snapshot['cards'][0].update(kind='CREDIT',opening_payable_krw=400,payment_delay_days=0)
    snapshot['known_bills']=[{'bill_id':'B','card_id':'C','due_date':'2026-09-20','amount_krw':400}]
    r=Engine(Twin([normalize(row)],'2026-09-06',snapshot)).run({'mode':'goal','goal':{'target_krw':200},'paths':20,'horizon_days':7})
    assert r['metrics']['p_goal_reached']['value']==0
    assert r['metrics']['goal_gap_p50_krw']['value']==100

def test_broken_visualization_contract(tiny):
    r=Engine(tiny).run({'mode':'forecast','paths':20,'horizon_days':7})
    r['visualizations'][0]['y']=['unavailable_field']
    with pytest.raises(FDTError):Engine.validate_visualizations(r)

def test_no_income_has_null_cv_not_nan(tiny):
    r=Engine(tiny).run({'mode':'risk','paths':20,'horizon_days':3})
    assert r['metrics']['income_cv_complete_months']['value'] is None
    assert r['metrics']['support_income_ratio']['value'] is None


def test_budget_observation_and_horizon_partial(row,snapshot):
    from fdt.ingest import normalize
    snapshot['budgets']={'외식':50}
    t=Twin([normalize(row)],'2026-09-06',snapshot)
    r=Engine(t).run({'mode':'risk','paths':20,'horizon_days':3})
    b=r['datasets']['budget_risk'][0]
    assert b['p_over_budget']==1 and b['observed_used_krw']==100
    assert b['observation_starts_midmonth'] and not b['full_month_forecast_coverage']


def test_fixed_spending_never_reduced_by_optimizer(row,snapshot):
    from fdt.ingest import normalize
    # 라벨(is_fixed)은 더 이상 보호 근거가 아니다. 고정지출 세부분류(월세)만 보호된다.
    row.update(category='주거·통신',subcategory='월세');snapshot['accounts'][0]['balance_krw']=100000
    t=Twin([normalize(row)],'2026-09-06',snapshot)
    r=Engine(t).run({'mode':'optimize','paths':20,'horizon_days':3})
    assert all(r['expected_saving_krw']==0 for r in r['datasets']['candidates'])
