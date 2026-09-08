import csv
import copy
from dataclasses import replace
import pytest
from fdt import Twin,FDTError
from fdt.ingest import normalize,deduplicate,audit
from conftest import FILES

@pytest.mark.parametrize('index,rows,user',[(0,337,'USR-DEMO-002'),(1,193,'USR-DEMO-003'),(2,262,'USR-DEMO-004'),(3,188,'USR-DEMO-005')])
def test_actual_inputs(index,rows,user):
    t=Twin.from_csv(FILES[index]);assert len(t.transactions)==rows;assert t.user_id==user
    assert {x.source for x in t.transactions}=={'SEED'}
    assert t.model['audit']['reconciliation_difference_krw']==0

@pytest.mark.parametrize('key,value',[
    ('amount_krw','-1'),('amount_krw','1.5'),('amount_krw','nan'),('amount_krw','1000000000001'),
    ('amount_krw',''),('source','DEMO'),('direction','OUT'),('status','cancel'),
    ('is_fixed','1'),('transaction_date','2026-02-30'),('transaction_time','24:00'),
    ('transaction_time','09:99'),('transaction_time','09'),('transaction_id',''),('card_id','')])
def test_bad_input(row,key,value):
    row[key]=value
    with pytest.raises(FDTError):normalize(row)

@pytest.mark.parametrize('label,kind', [('ATM 출금','cash_withdrawal'),('대출 상환','debt_service')])
def test_non_consumption(row,label,kind):
    row.update(subcategory=label,payment_method='ACCOUNT',transaction_type='WITHDRAW',account_id='A',card_id='')
    t=normalize(row);assert t.kind==kind;assert t.budget_amount_krw==0;assert t.envelope is None

def test_savings_not_consumption(row):
    row.update(direction='TRANSFER',category='저축·투자',subcategory='적금',exclude_tag='INTERNAL_TRANSFER')
    t=normalize(row);assert t.kind=='savings_out';assert t.budget_amount_krw==0

def test_bill_not_consumption(row):
    row.update(payment_method='ACCOUNT',card_id='',account_id='A',transaction_type='CARD_SETTLEMENT')
    t=normalize(row);assert t.kind=='card_settlement';assert t.envelope is None

def test_reimbursement_not_salary(row):
    row.update(direction='INCOME',transaction_type='TRANSFER_IN',payment_method='ACCOUNT',card_id='',account_id='A',subcategory='모임 정산')
    assert normalize(row).kind=='reimbursement'

def test_cancellation_excluded(row):
    row['status']='CANCELED';t=normalize(row)
    assert not t.active;assert t.budget_amount_krw==0;assert audit([t])['kind_totals_krw']=={}

@pytest.mark.parametrize('tag',['DUTCH','EMERGENCY','CARRYOVER'])
def test_budget_exclusion_does_not_erase_cash(row,tag):
    row['exclude_tag']=tag;t=normalize(row)
    assert t.amount_krw==100 and t.budget_amount_krw==0 and t.kind=='expense'

def test_unknown_mapping_uses_raw_category_before_other(row):
    row.update(category='교통', subcategory='새 교통수단')
    t=normalize(row)
    assert t.raw_subcategory=='새 교통수단'
    assert t.mapping_fallback
    assert t.envelope=='교통비'
    assert t.subcategory=='새 교통수단'

def test_unknown_category_preserves_raw_subcategory(row):
    row.update(category='새 카테고리', subcategory='새 세부분류')
    t=normalize(row)
    assert t.mapping_fallback and t.envelope=='기타' and t.subcategory=='새 세부분류'

def test_mapping_audit_exposes_raw_categories_and_quality(row):
    a=normalize(row)
    other=copy.deepcopy(row);other.update(transaction_id='T2', category='교통', subcategory='새 교통수단')
    b=normalize(other)
    report=audit([a,b])
    assert report['mapping_quality']['expense_rows']==2
    assert report['mapping_quality']['exact_rows']==1
    assert report['mapping_quality']['fallback_rows']==1
    assert report['mapping_quality']['fallback_pairs']=={'교통 > 새 교통수단':1}
    assert report['raw_category_totals_krw']['교통']==100

def test_duplicates_and_conflict(row):
    a=normalize(row);b=normalize(row,{'row':4})
    txs,n=deduplicate([a,b]);assert n==1 and len(txs)==1
    with pytest.raises(FDTError,match='동일 ID'):
        deduplicate([a,replace(a,amount_krw=200)])

def test_mixed_users_and_empty(row):
    t=normalize(row)
    with pytest.raises(FDTError):deduplicate([])
    with pytest.raises(FDTError):deduplicate([t,replace(t,id='T2',user_id='OTHER')])
    with pytest.raises(FDTError):Twin.from_csv(FILES[:2])

def test_bom_time_and_file_order(tmp_path,row):
    f=tmp_path/'한글.csv'
    with f.open('w',encoding='utf-8-sig',newline='') as stream:
        w=csv.DictWriter(stream,fieldnames=list(row));w.writeheader();w.writerow(row)
    t=Twin.from_csv(f);assert t.transactions[0].time=='09:05:00'
    assert t.metadata['input_rows']==1

def test_missing_columns_and_empty_file(tmp_path,row):
    f=tmp_path/'empty.csv';f.write_text('a,b\n')
    with pytest.raises(FDTError):Twin.from_csv(f)
    row.pop('source')
    with pytest.raises(FDTError):normalize(row)
