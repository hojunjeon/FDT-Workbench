from pathlib import Path
import copy
import numpy as np
import pytest
from fdt.ingest import normalize
from fdt.model import Twin
from fdt.simulation import RandomBundle

ROOT=Path(__file__).resolve().parents[1]
FILES=sorted((ROOT/'data/demo').glob('*.csv'))

@pytest.fixture
def row():
    return {'user_id':'U','transaction_id':'T1','source':'SEED','transaction_type':'CARD',
            'transaction_date':'2026-09-06','transaction_time':'9:05','category':'식비',
            'subcategory':'점심','merchant':'가맹점','merchant_id':'M','amount_krw':'100','account_id':'','card_id':'C',
            'confirm_status':'AUTO','exclude_tag':'NONE','status':'NORMAL'}

@pytest.fixture
def snapshot():
    return {'as_of':'2026-09-06','source':'USER_ASSUMPTION','accounts':[{'account_id':'A','balance_krw':500}],
            'cards':[{'card_id':'C','kind':'DEBIT','settlement_account_id':'A'}]}

@pytest.fixture
def tiny(row,snapshot):
    return Twin([normalize(row)],'2026-09-06',snapshot)

@pytest.fixture
def make_exact(row,snapshot):
    def make(*,kind='expense',card_kind='DEBIT',amount=100,protected=False,opening=500,horizon=8,extra_accounts=None):
        r=copy.deepcopy(row);r['amount_krw']='0'
        snap=copy.deepcopy(snapshot);snap['accounts'][0]['balance_krw']=opening
        snap['accounts']+=extra_accounts or []
        if card_kind=='CREDIT': snap['cards'][0].update(kind='CREDIT',opening_payable_krw=0,payment_delay_days=0)
        t=Twin([normalize(r)],'2026-09-06',snap)
        f={'kind':kind,'envelope':'외식' if kind=='expense' else None,
           'account_id':None if kind=='expense' else 'A','card_id':'C' if kind=='expense' else None,
           'to_account_id':'B' if kind=='internal_transfer' else None,'fixed_group':None,'pending':False,
           'protected':protected,'budgeted':True}
        t.model['components']=[f];t.model['rules']=[]
        a=np.zeros((20,horizon,1),dtype=np.int64);a[:,0,0]=amount
        from datetime import date,timedelta
        dates=[date(2026,9,7)+timedelta(days=i) for i in range(horizon)]
        bundle=RandomBundle(a,{},dates,42,20,0)
        return t,bundle
    return make
