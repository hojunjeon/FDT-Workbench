from __future__ import annotations
import copy
import itertools
import math
from datetime import date
import numpy as np
from .errors import FDTError
from .mapping import ENVELOPES, MAPPING_VERSION
from .model import Twin, MODEL_VERSION
from .simulation import RandomBundle, Simulation, generate_bundle, simulate
from .util import digest, month_date, probability, quantiles, validate, warning

MODES = {
    'forecast':'미래 상태 예측', 'what_if':'가정 분기 비교', 'goal':'목표 달성 가능성',
    'risk':'위험 분석', 'optimize':'제약 조건 내 행동 탐색'
}
LIMITATIONS = [
    '합성 단기 이력 기반 조건부 모델입니다. 실사용자 확률 보정/인과 효과를 검증하지 않았습니다.',
    '일 단위 마감 모델이며 당일 입출금 시각 순서와 공휴일/영업일 달력은 반영하지 않습니다.',
    '음수 현금은 미충족 자금 수요입니다. 실제 계좌의 초과 출금 허용을 의미하지 않습니다.',
    '현금 인출 이후의 지출, 미등록 계좌/보험 보장, 투자 수익률 및 대출 원리금 분해는 알 수 없습니다.',
    'resource_change는 구매 시점 지출 준비 여력 프록시이며 현금 잔액이나 순자산이 아닙니다.',
    'P10/P50/P90는 모델 경로의 분위수입니다. 실세계 결과를 포함한다고 보증하는 신뢰구간이 아닙니다.'
]


def metric(value, unit='KRW', basis='simulation', method='empirical_paths', evidence=None) -> dict:
    if isinstance(value, np.generic): value=value.item()
    return {'value':value,'unit':unit,'basis':basis,'method':method,'evidence':evidence or ['input_digest','model']}


def visual(id_: str, kind: str, title: str, dataset: str, x: str, y: list[str], unit='KRW', **kw) -> dict:
    return {'id':id_,'kind':kind,'title':title,'dataset':dataset,'x':x,'y':y,'unit':unit,
            'null_policy':'gap','note':'null은 0으로 그리지 않습니다. 예측 분위수는 조건부 모델 값입니다.',**kw}


def _put_quantiles(result: dict, prefix: str, values: np.ndarray | None, basis='simulation') -> None:
    for k,v in (quantiles(values).items() if values is not None else [(p,None) for p in ('p10','p50','p90')]):
        result['metrics'][prefix+'_'+k+'_krw']=metric(v,basis=basis)


def _goal_values(sim: Simulation, target: int, reserve: int) -> tuple[np.ndarray,np.ndarray,np.ndarray]:
    available=sim.free[:,-1]-reserve
    reached=available>=target
    joint=reached & ~sim.any_account_short
    return available,reached,joint


def _wilson(mask: np.ndarray) -> tuple[float,float]:
    n=len(mask); p=float(np.mean(mask)); z=1.959963984540054
    center=(p+z*z/(2*n))/(1+z*z/n)
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return max(0.,center-half),min(1.,center+half)


def _merge_scenario(base: dict, override: dict) -> dict:
    merged=copy.deepcopy(base)
    for k,v in override.items():
        if k=='expense_reductions':
            merged[k]={**merged.get(k,{}),**v}
        else: merged[k]=v
    return merged


class Engine:
    """Five pure query modes over one immutable-by-convention Twin.

    No model call reaches a bank, network, router or LLM.
    """
    def __init__(self,twin: Twin): self.twin=twin

    def run(self,request: dict) -> dict:
        validate('request',request)
        req={'horizon_days':90,'paths':400,'seed':42,**copy.deepcopy(request)}
        mode=req['mode']
        bundle=generate_bundle(self.twin,req['horizon_days'],req['paths'],req['seed'])
        base_scenario={} if mode=='what_if' else req.get('scenario',{})
        baseline=simulate(self.twin,bundle,base_scenario)
        result=self._base(req,bundle,baseline)
        if mode=='what_if': self._what_if(result,req,bundle,baseline)
        elif mode=='goal': self._goal(result,req,baseline)
        elif mode=='risk': self._risk(result,req,bundle,baseline)
        elif mode=='optimize': self._optimize(result,req,bundle,baseline)
        validate('result',result)
        self.validate_visualizations(result)
        return result

    def _base(self,req: dict,bundle: RandomBundle,sim: Simulation) -> dict:
        missing=self.twin.cash_requirements()
        basis='conditional_snapshot' if (self.twin.snapshot or {}).get('source')=='USER_ASSUMPTION' else 'observed_snapshot_plus_model'
        result={'schema_version':'1.0','mode':req['mode'],'status':'partial' if missing else 'ok',
            'twin_id':self.twin.twin_id,'revision':self.twin.revision,'as_of':self.twin.as_of,
            'horizon_days':req['horizon_days'],'input_digest':self.twin.content_digest,
            'model':{'version':MODEL_VERSION,'mapping_version':MAPPING_VERSION,'request_digest':digest(req),
                     'paths':req['paths'],'seed':req['seed'],'numpy_version':np.__version__,
                     'rng':'Generator(PCG64)','time_zone':'Asia/Seoul (date-level)',
                     'historical_days':self.twin.model['behavior']['observation_days'],
                     'source_counts':{s:sum(t.source==s for t in self.twin.transactions) for s in ('SEED','LIVE')},
                     'calibrated':False,'absolute_cash_ready':not missing},
            'assumptions':[
                {'code':'RESAMPLING','source':'ENGINE_DESIGN','detail':'잔여 금융 벡터는 요일 정렬 7일 블록으로 반복된다고 가정합니다.'},
                {'code':'RECURRENCE','source':'ESTIMATED','detail':'반복 금액을 재표본추출하고 추정일에 발생시킵니다. 공휴일 보정은 없습니다.'},
                {'code':'OBSERVATION_COVERAGE','source':'ENGINE_DESIGN','detail':'관측 기간 내 거래 없는 날은 0일 벡터입니다. 누락 거래를 검증하지 못합니다.'},
                {'code':'CARD_POLICY','source':(self.twin.snapshot or {}).get('source','UNKNOWN'),
                 'values':(self.twin.snapshot or {}).get('cards',[]),'detail':'CSV의 CARD 표기만으로 체크/신용 종류를 판정하지 않습니다.'},
                {'code':'SNAPSHOT','source':(self.twin.snapshot or {}).get('source','UNKNOWN'),
                 'as_of':(self.twin.snapshot or {}).get('as_of'),'detail':'USER_ASSUMPTION 잔액은 실제 잔액이 아닙니다.'},
                {'code':'SCENARIO','source':'REQUEST','values':req.get('scenario',{})}
            ],
            'warnings':copy.deepcopy(self.twin.model['audit']['warnings']),
            'limitations':list(LIMITATIONS),'metrics':{},'datasets':{},'visualizations':[]}
        if missing:
            result['required_inputs']=missing
            result['warnings'].append(warning('ABSOLUTE_STATE_UNAVAILABLE','현재 잔액/정산 정보 부족으로 절대 현금 경로는 계산하지 않습니다.',missing=missing))
        if bundle.fallback_days:
            result['warnings'].append(warning('SHORT_HISTORY_FALLBACK','충분한 과거 7일 블록이 없어 일별 요일 표본을 사용했습니다.',days=bundle.fallback_days))
        if self.twin.model['behavior']['observation_days']<60:
            result['warnings'].append(warning('SHORT_HISTORY','60일 미만의 관측으로 추정이 불안정할 수 있습니다.'))
        result['warnings'].append(warning('UNCALIBRATED_MODEL','확률은 모델 조건부 추정이며 외부 검증을 거치지 않았습니다.'))
        _put_quantiles(result,'terminal_resource_change',sim.resource[:,-1],basis='purchase_time_resource_proxy')
        _put_quantiles(result,'terminal_cash',sim.cash_total[:,-1] if sim.cash_total is not None else None,basis)
        _put_quantiles(result,'total_expense',sim.consumption.sum(axis=1))
        result['metrics']['p_total_cash_shortfall']=metric(probability(sim.total_short) if sim.total_short is not None else None,'probability',basis)
        result['metrics']['p_any_account_shortfall']=metric(probability(sim.any_account_short) if sim.any_account_short is not None else None,'probability',basis)
        result['metrics']['expected_expense_krw']=metric(round(float(sim.consumption.sum(axis=1).mean())))
        result['datasets']['projection']=sim.daily_rows(self.twin.as_of)
        result['datasets']['calendar']=sim.calendar
        result['datasets']['envelopes']=[{'envelope':env,**{k+'_krw':v for k,v in quantiles(sim.by_envelope[:,:,j].sum(axis=1)).items()}}
                                          for j,env in enumerate(ENVELOPES)]
        prefix='cash_balance' if not missing else 'resource_change'
        result['visualizations'].append(visual('projection','band_line','예측 자금 경로' if not missing else '구매시점 자금 여력 변화 (현금 잔액 아님)',
            'projection','date',[prefix+'_p50_krw'],lower=prefix+'_p10_krw',upper=prefix+'_p90_krw'))
        result['visualizations'].append(visual('envelopes','bar','봉투별 예상 소비','envelopes','envelope',['p50_krw']))
        result['visualizations'].append(visual('calendar','table','반복 일정 / 청구 예상','calendar','date',['expected_amount_krw']))
        return result

    def _what_if(self,result: dict,req: dict,bundle: RandomBundle,base: Simulation) -> None:
        branch=simulate(self.twin,bundle,req['scenario'])
        result['datasets']['branch_projection']=branch.daily_rows(self.twin.as_of)
        result['datasets']['branch_calendar']=branch.calendar
        _put_quantiles(result,'paired_terminal_resource_delta',branch.resource[:,-1]-base.resource[:,-1],basis='paired_common_random_numbers')
        _put_quantiles(result,'paired_terminal_cash_delta',branch.cash_total[:,-1]-base.cash_total[:,-1] if branch.cash_total is not None else None,basis='paired_common_random_numbers')
        _put_quantiles(result,'paired_expense_saving',base.consumption.sum(axis=1)-branch.consumption.sum(axis=1),basis='paired_common_random_numbers')
        result['metrics']['branch_p_any_account_shortfall']=metric(probability(branch.any_account_short) if branch.any_account_short is not None else None,'probability')
        prefix='cash_balance' if branch.cash_total is not None else 'resource_change'
        rows=[]
        for a,b in zip(result['datasets']['projection'],result['datasets']['branch_projection']):
            rows.append({'date':a['date'],'baseline_p50_krw':a[prefix+'_p50_krw'],'branch_p50_krw':b[prefix+'_p50_krw']})
        result['datasets']['comparison']=rows
        result['visualizations'].append(visual('branch_compare','line','기준 / 가정 분기 비교','comparison','date',['baseline_p50_krw','branch_p50_krw']))
        self._asset_shock(result,req['scenario'])
        result['decision']={'branch_mutates_twin':False,'comparison_method':'paired_common_random_numbers',
                            'intervention':req['scenario'],'causal_effect_claim':False}

    def _asset_shock(self,result: dict,scenario: dict) -> None:
        snapshot=self.twin.snapshot or {}
        known='assets' in snapshot
        value=sum(a['value_krw'] for a in snapshot.get('assets',[]) if a['kind']=='investment') if known else None
        delta=round(value*scenario.get('asset_shock_fraction',0)) if value is not None else None
        result['metrics']['investment_mark_to_market_delta_krw']=metric(delta,basis='explicit_static_asset_shock',method='reported_investments_times_shock')
        if 'asset_shock_fraction' in scenario and not known:
            result['warnings'].append(warning('INVESTMENT_VALUE_UNKNOWN','투자 평가액이 없어 자산 충격 금액은 null입니다.'))
        result['assumptions'].append({'code':'ASSET_SHOCK','source':'REQUEST','fraction':scenario.get('asset_shock_fraction',0),
            'detail':'정적으로 보고된 투자 평가액에 대한 1회 충격입니다. 현금 유입 또는 예측 수익률이 아닙니다.'})

    def _goal(self,result: dict,req: dict,sim: Simulation) -> None:
        g=req['goal']; target=g['target_krw']; reserve=g.get('reserve_krw',(self.twin.snapshot or {}).get('reserve_krw',0))
        desired=g.get('success_probability',0.8)
        result['metrics']['goal_target_krw']=metric(target,basis='request',method='provided')
        result['metrics']['goal_reserve_krw']=metric(reserve,basis='request_or_snapshot',method='provided_or_zero_explicit_default')
        if sim.free is None:
            result['status']='insufficient_data'
            result['metrics']['p_goal_reached']=metric(None,'probability')
            result['metrics']['p_goal_and_no_shortfall']=metric(None,'probability')
            result['decision']={'goal_basis':'cash_minus_card_payable_minus_reserve','feasibility':'unknown'}
            return
        available,reached,joint=_goal_values(sim,target,reserve)
        result['metrics']['p_goal_reached']=metric(probability(reached),'probability')
        result['metrics']['p_goal_and_no_shortfall']=metric(probability(joint),'probability')
        low,high=_wilson(reached)
        result['metrics']['goal_mc_sampling95_low']=metric(low,'probability',basis='monte_carlo_sampling_only',method='wilson_not_model_uncertainty')
        result['metrics']['goal_mc_sampling95_high']=metric(high,'probability',basis='monte_carlo_sampling_only',method='wilson_not_model_uncertainty')
        gaps=np.maximum(target-available,0)
        _put_quantiles(result,'goal_gap',gaps)
        injections=(req['horizon_days']-1)//30+1
        needed=int(math.ceil(float(np.quantile(gaps,desired,method='higher'))/injections))
        result['metrics']['additional_external_income_each_30d_krw']=metric(needed,basis='hypothetical_new_money',method='terminal_gap_quantile_divided_by_installments')
        result['datasets']['goal_distribution']=[{'percentile':p,'available_krw':int(round(float(np.percentile(available,p)))),'target_krw':target} for p in (0,10,25,50,75,90,100)]
        result['visualizations'].append(visual('goal_distribution','line','만기 가용 현금 분위수와 목표','goal_distribution','percentile',['available_krw','target_krw']))
        result['decision']={'goal_basis':'cash_minus_card_payable_minus_reserve','deadline':sim.dates[-1].isoformat(),
            'required_success_probability':desired,'feasibility':'meets_threshold' if probability(joint)>=desired else 'below_threshold',
            'external_income_installment_days':list(range(1,req['horizon_days']+1,30)),
            'external_income_note':'이 금액은 신규 외부 자금 조건이며 절약으로 자동 생성되지 않습니다. 중간 계좌 부족을 해소한다고 보장하지 않습니다.'}

    def _risk(self,result: dict,req: dict,bundle: RandomBundle,sim: Simulation) -> None:
        reserve=(self.twin.snapshot or {}).get('reserve_krw',0)
        if sim.cash is not None:
            severity=np.maximum(-sim.cash_total.min(axis=1),0)
            _put_quantiles(result,'maximum_total_cash_shortage',severity)
            result['metrics']['p_liquid_below_reserve']=metric(probability(np.any(sim.free<reserve,axis=1)),'probability')
            result['datasets']['account_risk']=[{'account_id':id_,'p_shortfall':probability(np.any(sim.cash[:,:,j]<0,axis=1)),
                'minimum_balance_p10_krw':quantiles(sim.cash[:,:,j].min(axis=1))['p10']} for j,id_ in enumerate(sim.account_ids)]
            first=[]
            dates=[self.twin.as_of]+[d.isoformat() for d in sim.dates]
            negative=np.any(sim.cash<0,axis=2)
            first_indices=np.where(negative.any(axis=1),negative.argmax(axis=1),-1)
            for i,d in enumerate(dates):
                first.append({'date':d,'probability_first_shortfall':probability(first_indices==i)})
            result['datasets']['first_shortfall']=first
            result['visualizations'].append(visual('account_risk','bar','계좌별 잔액 부족 확률','account_risk','account_id',['p_shortfall'],'probability'))
            result['visualizations'].append(visual('shortfall_timing','line','첫 부족일 확률 (경로별 최초 1회)','first_shortfall','date',['probability_first_shortfall'],'probability'))
        else:
            result['metrics']['p_liquid_below_reserve']=metric(None,'probability')
        result['metrics']['support_income_ratio']=metric(self.twin.model['behavior']['support_income_ratio'],'ratio',basis='observed_labels',method='family_labeled_income_over_total_income')
        totals={}
        first_day=self.twin.model['start']; end=date.fromisoformat(self.twin.as_of)
        for d in self.twin.model['dates']:
            key=(d.year,d.month)
            if month_date(d.year,d.month,1)>=first_day and month_date(d.year,d.month,31)<=end: totals[key]=0
        for t in self.twin.transactions:
            key=(date.fromisoformat(t.date).year,date.fromisoformat(t.date).month)
            if key in totals and t.active and t.kind=='income':totals[key]+=t.amount_krw
        vals=list(totals.values())
        cv=float(np.std(vals,ddof=1)/np.mean(vals)) if len(vals)>1 and np.mean(vals)>0 else None
        result['metrics']['income_cv_complete_months']=metric(cv,'ratio',basis='observed_complete_months',method='sample_std_over_mean')
        result['metrics']['income_complete_month_count']=metric(len(vals),'count',basis='observed',method='calendar_complete_months')
        self._budget_risk(result,sim)
        stresses=req.get('stress_scenarios',[{'name':'수입 20% 감소 가정','income_multiplier':0.8},{'name':'소비 물가 10% 상승 가정','expense_multiplier':1.1}])
        rows=[]
        for i,stress in enumerate(stresses):
            scenario=_merge_scenario(req.get('scenario',{}),stress)
            other=simulate(self.twin,bundle,scenario)
            investment=sum(a['value_krw'] for a in (self.twin.snapshot or {}).get('assets',[]) if a['kind']=='investment') if 'assets' in (self.twin.snapshot or {}) else None
            rows.append({'stress_id':str(i),'name':stress.get('name','stress-'+str(i)),
                         'terminal_resource_p50_krw':quantiles(other.resource[:,-1])['p50'],
                         'paired_resource_delta_p50_krw':quantiles(other.resource[:,-1]-sim.resource[:,-1])['p50'],
                         'p_any_account_shortfall':probability(other.any_account_short) if other.cash is not None else None,
                         'investment_shock_delta_krw':round(investment*scenario.get('asset_shock_fraction',0)) if investment is not None else None})
        result['datasets']['stress_scenarios']=rows
        result['assumptions'].append({'code':'STRESS_SCENARIOS','source':'REQUEST' if 'stress_scenarios' in req else 'ENGINE_ILLUSTRATIVE_DEFAULT','values':stresses,
            'detail':'충격 수치는 상황 가정이지 외부 경제 예측이 아닙니다.'})
        result['visualizations'].append(visual('stress','bar','충격별 자금 여력 변화','stress_scenarios','name',['paired_resource_delta_p50_krw']))

    def _budget_risk(self,result: dict,sim: Simulation) -> None:
        budgets=(self.twin.snapshot or {}).get('budgets',{})
        if not budgets: return
        as_of=date.fromisoformat(self.twin.as_of)
        monthend=month_date(as_of.year,as_of.month,31)
        count=sum(d<=monthend for d in sim.dates)
        rows=[]
        for env,limit in budgets.items():
            used=sum(t.budget_amount_krw for t in self.twin.transactions if t.active and t.envelope==env and t.date[:7]==self.twin.as_of[:7])
            total=sim.budget_by_envelope[:,:count,ENVELOPES.index(env)].sum(axis=1)+used
            rows.append({'envelope':env,'budget_krw':limit,'observed_used_krw':used,
                'projected_used_p50_krw':quantiles(total)['p50'],'p_over_budget':probability(total>limit),
                'coverage_end':min(sim.dates[-1],monthend).isoformat(),'full_month_forecast_coverage':sim.dates[-1]>=monthend,
                'observation_starts_midmonth':self.twin.model['start']>date(as_of.year,as_of.month,1)})
        result['datasets']['budget_risk']=rows
        result['visualizations'].append(visual('budget_risk','bar','이번 달 봉투 초과확률','budget_risk','envelope',['p_over_budget'],'probability'))
        if any(r['observation_starts_midmonth'] for r in rows):
            result['warnings'].append(warning('BUDGET_HISTORY_INCOMPLETE','이번 달 초 이력이 없어 관측 범위에 대한 초과확률만 계산했습니다.'))

    def _optimize(self,result: dict,req: dict,bundle: RandomBundle,base: Simulation) -> None:
        if base.free is None:
            result['status']='insufficient_data';result['decision']={'feasibility':'unknown','selected_candidate_id':None}
            return
        opt=req.get('optimization',{})
        envelopes=opt.get('envelopes',['외식','취미·여가','쇼핑'])
        grid=sorted(opt.get('reduction_grid',[0,0.1,0.2]))
        count=len(grid)**len(envelopes)
        if count>128: raise FDTError('OPTIMIZATION_LIMIT','최대 유한 후보는 128개입니다.',{'candidates':count})
        if count*req['horizon_days']*req['paths']*len(self.twin.model['components'])>180000000:
            raise FDTError('OPTIMIZATION_WORK_LIMIT','후보×시뮬레이션 연산량 한도를 초과했습니다.')
        g=req.get('goal',{'target_krw':0})
        target=g['target_krw'];reserve=g.get('reserve_krw',(self.twin.snapshot or {}).get('reserve_krw',0))
        desired=g.get('success_probability',0.8);maxshort=opt.get('max_shortfall_probability',0.1)
        base_spend=float(base.consumption.sum(axis=1).mean())
        rows=[]
        for n,combo in enumerate(itertools.product(grid,repeat=len(envelopes))):
            changes=dict(zip(envelopes,combo))
            scenario=copy.deepcopy(req.get('scenario',{}))
            existing=scenario.get('expense_reductions',{})
            scenario['expense_reductions']={**existing,**{env:1-(1-existing.get(env,0))*(1-cut) for env,cut in changes.items()}}
            sim=simulate(self.twin,bundle,scenario)
            _,_,joint=_goal_values(sim,target,reserve)
            pshort=probability(sim.any_account_short);pjoint=probability(joint)
            floors_ok=True
            for env,minval in opt.get('minimum_remaining_monthly_krw',{}).items():
                remaining=0.
                for j,f in enumerate(self.twin.model['components']):
                    if f['kind']=='expense' and f['envelope']==env and not f['protected']:
                        remaining+=float(bundle.variable[:,:,j].sum(axis=1).mean())*(1-scenario['expense_reductions'].get(env,0))*scenario.get('expense_multiplier',1)*30.4375/req['horizon_days']
                floors_ok &= remaining+1e-8>=minval
            saving=max(0,int(round(base_spend-float(sim.consumption.sum(axis=1).mean()))))
            rows.append({'candidate_id':f'candidate-{n:03}','reductions':changes,'effective_reductions':scenario['expense_reductions'],
                         'expected_saving_krw':saving,'p_goal_and_no_shortfall':pjoint,'p_any_account_shortfall':pshort,
                         'terminal_free_p50_krw':quantiles(sim.free[:,-1]-reserve)['p50'],
                         'minimum_spending_satisfied':bool(floors_ok),
                         'feasible':bool(pjoint>=desired and pshort<=maxshort and floors_ok)})
        feasible=sorted([r for r in rows if r['feasible']],key=lambda r:(r['expected_saving_krw'],sum(r['reductions'].values()),r['candidate_id']))
        selected=feasible[0] if feasible else None
        for r in rows:
            r['selected']=bool(selected and r['candidate_id']==selected['candidate_id'])
            r['pareto']=not any(o['expected_saving_krw']<=r['expected_saving_krw'] and o['p_goal_and_no_shortfall']>=r['p_goal_and_no_shortfall'] and
                (o['expected_saving_krw']<r['expected_saving_krw'] or o['p_goal_and_no_shortfall']>r['p_goal_and_no_shortfall']) for o in rows)
        result['datasets']['candidates']=rows
        result['metrics']['candidate_count']=metric(len(rows),'count',method='exhaustive_grid')
        result['metrics']['feasible_candidate_count']=metric(len(feasible),'count',method='constraints')
        result['metrics']['selected_expected_saving_krw']=metric(selected['expected_saving_krw'] if selected else None,method='minimum_expected_spend_change_among_feasible')
        result['decision']={'feasibility':'feasible' if selected else 'infeasible','selected_candidate_id':selected['candidate_id'] if selected else None,
            'selected_reductions':selected['reductions'] if selected else None,
            'target_krw':target,'reserve_krw':reserve,'required_joint_success':desired,'maximum_any_account_shortfall':maxshort,
            'optimality_scope':'EXHAUSTIVE_FINITE_GRID_ONLY','objective':'MIN_EXPECTED_CONSUMPTION_REDUCTION',
            'protected_fixed_and_recurring':True,'executed':False}
        result['visualizations'].append(visual('candidate_frontier','scatter','행동 변화와 목표 확률 — 유한 후보','candidates','expected_saving_krw',['p_goal_and_no_shortfall'],'probability',note='x는 KRW, y는 확률. feasible/selected 필드를 범례로 사용합니다. 전역 최적 금융전략을 의미하지 않습니다.'))

    @staticmethod
    def validate_visualizations(result: dict) -> None:
        for spec in result['visualizations']:
            if spec['dataset'] not in result['datasets']:
                raise FDTError('CHART_DATASET','시각화 데이터셋이 없습니다.')
            rows=result['datasets'][spec['dataset']]
            fields=[spec['x'],*spec['y']]+[spec[k] for k in ('lower','upper') if k in spec]
            for row in rows:
                if any(k not in row for k in fields):
                    raise FDTError('CHART_FIELD','시각화 필드가 데이터에 없습니다.',{'chart':spec['id']})
