"""Create explicit DEMO assumptions; these values are NOT recovered from CSV balances."""
from pathlib import Path
from fdt import Twin
from fdt.util import write_json
ROOT=Path(__file__).resolve().parents[1]
for i,csv in enumerate(sorted((ROOT/'data/demo').glob('*.csv')),1):
    t=Twin.from_csv(csv)
    aid=next(x.account_id for x in t.transactions if x.account_id)
    card=next(x.card_id for x in t.transactions if x.card_id)
    snapshot={'as_of':t.as_of,'source':'USER_ASSUMPTION',
              'accounts':[{'account_id':aid,'balance_krw':{1:3000000,2:900000,3:5000000,4:2000000}[i]}],
              'cards':[{'card_id':card,'kind':'CREDIT' if i in (1,3) else 'DEBIT','settlement_account_id':aid}],
              'known_bills':[], 'reserve_krw':200000,'coverage':{'all_assets_reported':False,'all_liabilities_reported':False}}
    if i in (1,3):snapshot['cards'][0].update(opening_payable_krw=0,payment_delay_days=2)
    if i==3:
        snapshot['assets']=[{'asset_id':'DEMO-INVESTMENT','kind':'investment','value_krw':12000000}]
        snapshot['liabilities']=[{'liability_id':'DEMO-LOAN','principal_krw':100000000}]
    snapshot['budgets']={env:int(round(total*30.4375/t.model['behavior']['observation_days']*1.1/1000)*1000)
        for env,total in t.model['audit']['envelope_totals_krw'].items()}
    write_json(ROOT/f'examples/snapshot_{i:03}.json',snapshot)
requests={
 'forecast':{'mode':'forecast'},
 'what_if':{'mode':'what_if','scenario':{'name':'외식 20%, 쇼핑 10% 감축','expense_reductions':{'외식':.2,'쇼핑':.1}}},
 'goal':{'mode':'goal','goal':{'target_krw':3000000,'reserve_krw':200000,'success_probability':.8}},
 'risk':{'mode':'risk','stress_scenarios':[{'name':'수입 20% 감소','income_multiplier':.8},{'name':'소비 10% 상승','expense_multiplier':1.1},{'name':'투자 평가액 20% 하락','asset_shock_fraction':-.2}]},
 'optimize':{'mode':'optimize','goal':{'target_krw':3000000,'reserve_krw':200000,'success_probability':.8},
             'optimization':{'envelopes':['외식','취미·여가','쇼핑'],'reduction_grid':[0,.1,.2],'max_shortfall_probability':.1}}
}
for mode,req in requests.items():
    write_json(ROOT/f'examples/requests/{mode}.json',{'horizon_days':90,'paths':400,'seed':42,**req})
