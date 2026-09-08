import csv
import copy
from dataclasses import replace
import pytest
from fdt import Twin,FDTError
from fdt.ingest import normalize,deduplicate,audit,load_csv,record_signature,transaction_signature
from fdt.mapping import FIXED_GROUPS, fixed_group
from conftest import FILES

@pytest.mark.parametrize('index,rows,user',[(0,337,'USR-DEMO-002'),(1,193,'USR-DEMO-003'),(2,262,'USR-DEMO-004'),(3,188,'USR-DEMO-005')])
def test_actual_inputs(index,rows,user):
    t=Twin.from_csv(FILES[index]);assert len(t.transactions)==rows;assert t.user_id==user
    assert {x.source for x in t.transactions}=={'SEED'}
    assert t.model['audit']['reconciliation_difference_krw']==0
    audit_report=t.model['audit']
    assert set(audit_report['fixed_totals_krw'])==set(FIXED_GROUPS)
    assert sum(audit_report['fixed_totals_krw'].values())==audit_report['kind_totals_krw'].get('fixed_expense',0)

@pytest.mark.parametrize('key,value',[
    ('amount_krw','-1'),('amount_krw','1.5'),('amount_krw','nan'),('amount_krw','1000000000001'),
    ('amount_krw',''),('source','DEMO'),('direction','OUT'),('status','cancel'),
    ('payment_method','WIRE'),('transaction_date','2026-02-30'),('transaction_time','24:00'),
    ('transaction_time','09:99'),('transaction_time','09'),('transaction_id',''),('card_id','')])
def test_bad_input(row,key,value):
    row[key]=value
    with pytest.raises(FDTError):normalize(row)

@pytest.mark.parametrize('label,kind', [('ATM 출금','cash_withdrawal'),('대출 상환','debt_service')])
def test_non_consumption(row,label,kind):
    row.update(subcategory=label,transaction_type='WITHDRAW',account_id='A',card_id='')
    t=normalize(row);assert t.kind==kind;assert t.budget_amount_krw==0;assert t.envelope is None

def test_savings_not_consumption(row):
    row.update(transaction_type='TRANSFER_OUT',category='저축·투자',subcategory='적금',exclude_tag='INTERNAL_TRANSFER')
    t=normalize(row);assert t.kind=='savings_out';assert t.budget_amount_krw==0

def test_bill_not_consumption(row):
    row.update(card_id='',account_id='A',transaction_type='CARD_SETTLEMENT')
    t=normalize(row);assert t.kind=='card_settlement';assert t.envelope is None

def test_reimbursement_not_salary(row):
    row.update(transaction_type='TRANSFER_IN',card_id='',account_id='A',subcategory='모임 정산')
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


def test_fixed_group_and_fixed_expense_normalization(row):
    assert FIXED_GROUPS == ('주거','공과금','통신','보험·사회보험','세금','구독·멤버십')
    assert fixed_group('월세') == '주거'
    assert fixed_group('월세 정기납부') is None
    row.update(subcategory='월세',category='주거·통신')
    t=normalize(row)
    assert (t.kind,t.fixed_group,t.envelope,t.subcategory,t.budget_amount_krw,t.mapping_fallback,t.pending)==(
        'fixed_expense','주거',None,None,0,False,False)


def test_pending_expense_isolated_from_mapping(row):
    row.update(subcategory='월세',category='주거·통신',confirm_status='PENDING')
    t=normalize(row)
    assert t.kind=='expense' and t.fixed_group is None and t.pending
    assert (t.envelope,t.subcategory,t.budget_amount_krw,t.mapping_fallback)==(None,None,0,False)
    assert (t.raw_category,t.raw_subcategory)==('주거·통신','월세')


def test_optional_and_ignored_columns_are_not_required_or_read(row):
    row.pop('exclude_tag')
    row.update(is_fixed='not-a-bool',is_recurring='anything',spend_pattern='not-an-enum',classify_source='fake')
    t=normalize(row)
    assert t.exclude_tag=='NONE' and t.is_fixed is False and t.is_recurring is False and t.spend_pattern=='N/A'


def test_direction_and_payment_method_mismatch_is_rejected(row):
    row['direction']='INCOME'
    with pytest.raises(FDTError) as error:
        normalize(row)
    assert error.value.code=='INCONSISTENT_RECORD'
    row.pop('direction')
    row['payment_method']='ACCOUNT'
    with pytest.raises(FDTError) as error:
        normalize(row)
    assert error.value.code=='INCONSISTENT_RECORD'


@pytest.mark.parametrize('transaction_type,direction,category,expected',[
    ('TRANSFER_IN',None,'기타','income'),
    ('TRANSFER_IN','TRANSFER','저축·투자','savings_out'),
    ('TRANSFER_OUT',None,'기타','expense'),
    ('TRANSFER_OUT','EXPENSE','기타','expense'),
    ('TRANSFER_OUT','TRANSFER','기타','internal_transfer'),
    ('TRANSFER_OUT',None,'저축·투자','savings_out'),
    ('TRANSFER',None,'기타','internal_transfer'),
])
def test_kind_uses_transaction_type_order(row,transaction_type,direction,category,expected):
    row.update(transaction_type=transaction_type,account_id='A',card_id='',category=category)
    if direction is not None:
        row['direction']=direction
    assert normalize(row).kind==expected


def test_transfer_out_to_third_party_stays_expense_unless_tagged(row):
    # 축의금·회비·모임 정산 송금은 TRANSFER_OUT이지만 소비다. 내 계좌 간 이동은 사용자 태그로만 안다.
    row.update(transaction_type='TRANSFER_OUT',account_id='A',card_id='',category='사회·경조',subcategory='경조사')
    row.pop('direction',None)
    assert normalize(row).kind=='expense'
    row['exclude_tag']='INTERNAL_TRANSFER'
    assert normalize(row).kind=='internal_transfer'


def test_load_csv_reports_ignored_columns(tmp_path,row):
    path=tmp_path/'ignored.csv'
    row.update(is_fixed='TRUE',is_recurring='TRUE',spend_pattern='IMPULSE',classify_source='legacy')
    with path.open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(row))
        writer.writeheader();writer.writerow(row)
    txs,meta=load_csv([path])
    assert len(txs)==1 and meta['ignored_columns']==['classify_source','is_fixed','is_recurring','spend_pattern']


def test_audit_tracks_fixed_and_pending_totals(row):
    row.update(subcategory='월세',category='주거·통신')
    fixed=normalize(row)
    pending_row=copy.deepcopy(row);pending_row.update(transaction_id='T2',subcategory='저녁/외식',confirm_status='PENDING',amount_krw='30')
    pending=normalize(pending_row)
    report=audit([fixed,pending])
    assert report['kind_totals_krw']=={'expense':30,'fixed_expense':100}
    assert report['fixed_totals_krw']=={group:(100 if group=='주거' else 0) for group in FIXED_GROUPS}
    assert report['pending_consumption_krw']==30 and report['pending_rows']==1
    assert report['envelope_totals_krw']=={} and report['reconciliation_difference_krw']==0
    pending_warning=next(w for w in report['warnings'] if w['code']=='PENDING_CLASSIFICATION')
    assert pending_warning['details']=={'count':1,'krw':30}


def test_record_signature_separates_record_from_classification(row):
    a=normalize(row)
    b=copy.deepcopy(row);b.update(subcategory='월세',category='주거·통신')
    b=normalize(b)
    assert record_signature(a)==record_signature(b)
    assert transaction_signature(a)!=transaction_signature(b)
