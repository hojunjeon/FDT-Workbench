import copy
from dataclasses import replace
from datetime import date
import numpy as np
import pytest
from fdt import Twin,FDTError
from fdt.ingest import normalize
from fdt.model import rule_dates,validate_snapshot
from conftest import FILES

@pytest.mark.parametrize('path',FILES)
def test_missing_balance_never_reconstructed(path):
    t=Twin.from_csv(path);state=t.inspect()['state']
    assert state['managed_cash_krw'] is None and state['net_worth_krw'] is None
    assert not state['absolute_cash_ready']

def test_family_support_without_recurring_flag():
    t=Twin.from_csv(FILES[1])
    incoming=[r for r in t.model['rules'] if t.model['components'][r['component']]['kind']=='income']
    support_ids={x.id for x in t.transactions if x.raw_subcategory=='가족 지원'}
    assert len([r for r in incoming if support_ids & set(r['evidence'])])==2

def test_same_day_rent_aggregated():
    t=Twin.from_csv(FILES[1])
    rent_ids={x.id for x in t.transactions if x.raw_subcategory=='월세'}
    rs=[r for r in t.model['rules'] if set(r['evidence']) & rent_ids]
    assert len(rs)==1;assert rs[0]['amount_samples']==[550000]*3

def test_28day_schedule_detected():
    t=Twin.from_csv(FILES[3])
    assert any(r['frequency']=='INTERVAL' and r['interval_days']==28 for r in t.model['rules'])

def test_project_invoice_not_mixed_with_recurring_contract():
    t=Twin.from_csv(FILES[2])
    income=[r for r in t.model['rules'] if t.model['components'][r['component']]['kind']=='income']
    assert any(r['amount_samples']==[531850]*3 for r in income)
    assert not any(2901000 in r['amount_samples'] for r in income)

def test_month_end_clamp():
    r={'frequency':'MONTHLY','day_of_month':31,'next_date':'2027-01-31'}
    assert rule_dates(r,date(2027,1,1),date(2027,3,31))==[date(2027,1,31),date(2027,2,28),date(2027,3,31)]

def test_cutoff_prevents_future_leakage():
    early=Twin.from_csv(FILES[0],as_of='2026-07-31')
    assert max(x.date for x in early.transactions)<='2026-07-31'
    assert early.metadata['future_rows_excluded']>0
    later=Twin.from_csv(FILES[0])
    selected=[x for x in later.transactions if x.date<='2026-07-31']
    assert early.content_digest==Twin(selected,'2026-07-31').content_digest
    with pytest.raises(FDTError):Twin(selected,'2026-06-01')

def test_actual_zero_days_present():
    t=Twin.from_csv(FILES[0]);assert len(t.model['dates'])==90
    assert len({x.date for x in t.transactions})==86

def test_unknown_card_policy_blocks_absolute(row,snapshot):
    snapshot['cards']=[]
    t=Twin([normalize(row)],'2026-09-06',snapshot)
    assert t.cash_requirements()==['snapshot.cards/C']

def test_snapshot_asof_staleness(row,snapshot):
    snapshot['as_of']='2026-09-05'
    t=Twin([normalize(row)],'2026-09-06',snapshot)
    assert t.cash_requirements()

@pytest.mark.parametrize('issue',['missing_account','negative_bill','mismatch','duplicate','past_schedule'])
def test_snapshot_rejects_bad_relations(snapshot,issue):
    if issue=='missing_account':snapshot['cards'][0]['settlement_account_id']='OTHER'
    elif issue=='negative_bill':snapshot['known_bills']=[{'bill_id':'B','card_id':'C','due_date':'2026-09-07','amount_krw':-1}]
    elif issue=='mismatch':snapshot['cards'][0].update(kind='CREDIT',opening_payable_krw=100,payment_delay_days=0)
    elif issue=='duplicate':snapshot['accounts']*=2
    else:snapshot['schedules']=[{'rule_id':'r','kind':'income','account_id':'A','amount_krw':10,'frequency':'MONTHLY','next_date':'2026-09-05','day_of_month':5}]
    with pytest.raises(FDTError):validate_snapshot(snapshot)

def test_whole_networth_needs_coverage(row,snapshot):
    snapshot['assets']=[{'asset_id':'S','kind':'savings','value_krw':1000}]
    snapshot['liabilities']=[{'liability_id':'L','principal_krw':200}]
    assert Twin([normalize(row)],'2026-09-06',snapshot).inspect()['state']['net_worth_krw'] is None
    snapshot['coverage']={'all_assets_reported':True,'all_liabilities_reported':True}
    assert Twin([normalize(row)],'2026-09-06',snapshot).inspect()['state']['net_worth_krw']==1300

def test_rules_not_duplicated_in_pool():
    t=Twin.from_csv(FILES[0]);ids={i for r in t.model['rules'] for i in r['evidence']}
    remaining=sum(x.amount_krw for x in t.transactions if x.active and x.id not in ids and x.kind!='card_settlement')
    assert t.model['daily'].sum()==remaining

def test_model_round_trip(tiny):
    other=Twin.from_dict(tiny.to_dict())
    assert other.content_digest==tiny.content_digest
    np.testing.assert_equal(tiny.model['daily'],other.model['daily'])

def test_manual_schedule_override_does_not_add_twice():
    t=Twin.from_csv(FILES[0])
    rent=next(r for r in t.model['rules'] if r['expected_amount_krw']==700000)
    aid=next(x.account_id for x in t.transactions if x.account_id)
    snapshot={'as_of':t.as_of,'source':'USER_ASSUMPTION','accounts':[{'account_id':aid,'balance_krw':1000000}],
              'schedules':[{'rule_id':'manual-rent','kind':'expense','amount_krw':600000,'envelope':'기타','account_id':aid,
                'frequency':'MONTHLY','day_of_month':10,'next_date':'2026-09-10','replaces_rule_id':rent['rule_id']}]}
    adjusted=Twin.from_csv(FILES[0],snapshot=snapshot)
    assert not any(r['rule_id']==rent['rule_id'] for r in adjusted.model['rules'])
    assert any(r['rule_id']=='manual-rent' for r in adjusted.model['rules'])
    assert np.array_equal(t.model['daily'],adjusted.model['daily'][:,:t.model['daily'].shape[1]])


def test_complete_coverage_requires_explicit_empty_arrays(snapshot):
    snapshot['coverage']={'all_assets_reported':True,'all_liabilities_reported':True}
    with pytest.raises(FDTError):validate_snapshot(snapshot)


def test_relationship_graph_references_exist(tiny):
    graph=tiny.relationships();ids={n['id'] for n in graph['nodes']}
    assert any(n['type']=='transaction' for n in graph['nodes'])
    assert all(e['from'] in ids and e['to'] in ids for e in graph['edges'])
