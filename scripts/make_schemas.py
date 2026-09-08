"""Maintain all external input contracts in one place. Runtime uses the emitted JSON."""
from pathlib import Path
import json
ROOT = Path(__file__).resolve().parents[1] / 'fdt/schemas'
STR = {'type':'string','minLength':1,'maxLength':200}
DATE = {'type':'string','format':'date'}
MONEY = {'type':'integer','minimum':0,'maximum':10**12}
SIGNED = {'type':'integer','minimum':-10**12,'maximum':10**12}
PROB = {'type':'number','minimum':0,'maximum':1}
ENVS = ['외식','교통비','의료·건강','취미·여가','쇼핑','편의점·마트·잡화','기타']
FIXED = ['주거','공과금','통신','보험·사회보험','세금','구독·멤버십']
def obj(props, req=()):
    return {'type':'object','properties':props,'required':list(req),'additionalProperties':False}
def arr(item, maximum=100): return {'type':'array','items':item,'maxItems':maximum}
def enum(*vals): return {'enum':list(vals)}
def envmap(val): return {'type':'object','properties':{k:val for k in ENVS},'additionalProperties':False}
def write(name, schema):
    schema={'$schema':'https://json-schema.org/draft/2020-12/schema', **schema}
    (ROOT/f'{name}.json').write_text(json.dumps(schema,ensure_ascii=False,indent=2),encoding='utf-8')

schedule = obj({
    'rule_id':STR,'kind':enum('expense','fixed_expense','income','reimbursement','savings_out','cash_withdrawal','debt_service','internal_transfer'),
    'amount_krw':MONEY,'envelope':enum(*ENVS),'fixed_group':enum(*FIXED),'account_id':STR,'card_id':STR,'to_account_id':STR,
    'frequency':enum('MONTHLY','INTERVAL','ONCE'),'next_date':DATE,
    'day_of_month':{'type':'integer','minimum':1,'maximum':31},
    'interval_days':{'type':'integer','minimum':1,'maximum':366},
    'replaces_rule_id':STR,'replaces_transaction_ids':{'type':'array','items':STR,'maxItems':100,'minItems':1,'uniqueItems':True}
},['rule_id','kind','amount_krw','frequency','next_date'])
schedule['allOf']=[
    {'if':{'properties':{'kind':{'const':'fixed_expense'}}},
     'then':{'required':['fixed_group'],'not':{'required':['envelope']}}},
    {'if':{'properties':{'kind':{'not':{'const':'fixed_expense'}}}},
     'then':{'not':{'required':['fixed_group']}}}
]
account=obj({'account_id':STR,'balance_krw':SIGNED},['account_id','balance_krw'])
card=obj({'card_id':STR,'kind':enum('DEBIT','CREDIT'),'settlement_account_id':STR,
          'opening_payable_krw':MONEY,'payment_delay_days':{'type':'integer','minimum':0,'maximum':31}},
         ['card_id','kind','settlement_account_id'])
card['allOf']=[{'if':{'properties':{'kind':{'const':'CREDIT'}}},'then':{'required':['opening_payable_krw','payment_delay_days']}}]
write('snapshot',obj({
    'as_of':DATE,'source':enum('USER_ASSUMPTION','LIVE'),'accounts':arr(account,30),'cards':arr(card,30),
    'known_bills':arr(obj({'bill_id':STR,'card_id':STR,'due_date':DATE,'amount_krw':MONEY},['bill_id','card_id','due_date','amount_krw']),100),
    'schedules':arr(schedule,100),'reserve_krw':MONEY,'budgets':envmap(MONEY),
    'assets':arr(obj({'asset_id':STR,'kind':enum('investment','property','cash_wallet','savings','other'),'value_krw':MONEY},['asset_id','kind','value_krw']),100),
    'liabilities':arr(obj({'liability_id':STR,'principal_krw':MONEY},['liability_id','principal_krw']),100),
    'coverage':obj({'all_assets_reported':{'type':'boolean'},'all_liabilities_reported':{'type':'boolean'}})
},['as_of','source','accounts']))
cash_event=obj({'date':DATE,'account_id':STR,'amount_krw':MONEY,'direction':enum('INCOME','EXPENSE'),
                'fixed_group':enum(*FIXED)},['date','account_id','amount_krw','direction'])
cash_event['allOf']=[{'if':{'properties':{'direction':{'const':'INCOME'}}},
                      'then':{'not':{'required':['fixed_group']}}}]
scenario=obj({
    'name':STR,'expense_reductions':envmap(PROB),
    'income_multiplier':{'type':'number','minimum':0,'maximum':5},
    'expense_multiplier':{'type':'number','minimum':0,'maximum':5},
    'cancel_rule_ids':arr(STR,100),
    'fixed_multiplier':{'type':'number','minimum':0,'maximum':5},
    'fixed_overrides':arr(obj({'rule_id':STR,'amount_krw':MONEY},['rule_id','amount_krw']),100),
    'cash_events':arr(cash_event,100),
    'asset_shock_fraction':{'type':'number','minimum':-1,'maximum':5}
})
goal=obj({'target_krw':MONEY,'reserve_krw':MONEY,'success_probability':PROB},['target_krw'])
opt=obj({'envelopes':{'type':'array','items':enum(*ENVS),'uniqueItems':True,'minItems':1,'maxItems':4},
         'reduction_grid':{'type':'array','items':PROB,'uniqueItems':True,'minItems':1,'maxItems':6},
         'max_shortfall_probability':PROB,'minimum_remaining_monthly_krw':envmap(MONEY)})
request=obj({'mode':enum('forecast','what_if','goal','risk','optimize'),
    'horizon_days':{'type':'integer','minimum':1,'maximum':365},
    'paths':{'type':'integer','minimum':20,'maximum':2000},
    'seed':{'type':'integer','minimum':0,'maximum':2**32-1},
    'scenario':scenario,'goal':goal,'optimization':opt,'stress_scenarios':arr(scenario,5)},['mode'])
request['allOf']=[
 {'if':{'properties':{'mode':{'const':'goal'}}},'then':{'required':['goal']}},
 {'if':{'properties':{'mode':{'const':'what_if'}}},'then':{'required':['scenario']}},
 {'if':{'properties':{'mode':{'not':{'enum':['goal','optimize']}}}},'then':{'not':{'required':['goal']}}},
 {'if':{'properties':{'mode':{'not':{'const':'optimize'}}}},'then':{'not':{'required':['optimization']}}},
 {'if':{'properties':{'mode':{'not':{'const':'risk'}}}},'then':{'not':{'required':['stress_scenarios']}}}
]
write('request',request)
metric=obj({'value':{'type':['number','null']},'unit':enum('KRW','probability','count','ratio','days','months'),
            'basis':STR,'method':STR,'evidence':arr(STR,100)},['value','unit','basis','method','evidence'])
warning=obj({'code':STR,'message':{'type':'string'},'details':{'type':'object'}},['code','message'])
vis=obj({'id':STR,'kind':enum('band_line','line','bar','scatter','table'),
    'title':STR,'dataset':STR,'x':STR,'y':arr(STR,10),'lower':STR,'upper':STR,'unit':STR,
    'null_policy':{'const':'gap'},'note':{'type':'string'}},['id','kind','title','dataset','x','y','unit','null_policy','note'])
result=obj({'schema_version':{'const':'1.0'},'mode':enum('forecast','what_if','goal','risk','optimize'),
            'status':enum('ok','partial','insufficient_data'), 'twin_id':STR,'revision':{'type':'integer','minimum':0},
            'as_of':DATE,'horizon_days':{'type':'integer'},'model':{'type':'object'},'input_digest':STR,
            'assumptions':arr({'type':'object'},200),'warnings':arr(warning,200),'limitations':arr({'type':'string'},50),
            'metrics':{'type':'object','additionalProperties':metric},
            'datasets':{'type':'object','additionalProperties':{'type':'array','items':{'type':'object'}}},
            'visualizations':arr(vis,30),'decision':{'type':'object'},'required_inputs':arr(STR,100)},
           ['schema_version','mode','status','twin_id','revision','as_of','horizon_days','model','input_digest','assumptions','warnings','limitations','metrics','datasets','visualizations'])
write('result',result)
event=obj({'event_id':STR,'user_id':STR,'type':enum('transaction','cancel_transaction','snapshot'),
                  'transaction':{'type':'object'},'transaction_id':STR,'snapshot':{'type':'object'}},['event_id','user_id','type'])
event['allOf']=[{'if':{'properties':{'type':{'const':kind}}},'then':{'required':[field],
    'not':{'anyOf':[{'required':[other]} for other in ('transaction','transaction_id','snapshot') if other!=field]}}}
    for kind,field in [('transaction','transaction'),('cancel_transaction','transaction_id'),('snapshot','snapshot')]]
write('event',event)
