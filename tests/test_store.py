import copy
import pytest
from fdt import Twin,FDTError
from fdt.store import TwinStore,apply_events


def live_event(row,id_='event1',tx='LIVE1',date='2026-09-07'):
    return {'event_id':id_,'user_id':'U','type':'transaction','transaction':{**row,'source':'LIVE','transaction_id':tx,'transaction_date':date}}

def test_event_idempotency(tiny,row):
    event=live_event(row)
    new=apply_events(tiny,[event]);again=apply_events(new,[event])
    assert new is again;assert new.revision==1;assert tiny.revision==0
    assert len(new.transactions)==2

def test_same_day_change_requires_fresh_snapshot(tiny,row):
    new=apply_events(tiny,[live_event(row,date=tiny.as_of)])
    assert new.snapshot['as_of']==new.as_of
    assert new.cash_requirements()
    fresh=copy.deepcopy(new.snapshot);fresh['source']='LIVE';fresh['accounts'][0]['balance_krw']=400
    newest=apply_events(new,[{'event_id':'s1','user_id':'U','type':'snapshot','snapshot':fresh}])
    assert not newest.cash_requirements()
    assert newest.inspect()['state']['managed_cash_krw']==400

def test_batch_conflict_is_atomic(tiny,row):
    original=tiny.to_dict()
    a=live_event(row);bad=copy.deepcopy(a);bad['transaction']['amount_krw']='101'
    with pytest.raises(FDTError):apply_events(tiny,[a,bad])
    assert tiny.to_dict()==original

def test_wrong_user_rejected(tiny,row):
    e=live_event(row);e['user_id']='OTHER'
    with pytest.raises(FDTError):apply_events(tiny,[e])

def test_seed_event_rejected(tiny,row):
    e=live_event(row);e['transaction']['source']='SEED'
    with pytest.raises(FDTError):apply_events(tiny,[e])

def test_full_cancel_live_transaction(tiny,row):
    new=apply_events(tiny,[live_event(row)])
    latest=apply_events(new,[{'event_id':'c1','user_id':'U','type':'cancel_transaction','transaction_id':'LIVE1'}])
    assert not next(t for t in latest.transactions if t.id=='LIVE1').active
    assert latest.model['audit']['kind_totals_krw']['expense']==100

def test_cannot_cancel_seed_or_unknown(tiny):
    for id_ in ['T1','missing']:
        with pytest.raises(FDTError):apply_events(tiny,[{'event_id':'c1','user_id':'U','type':'cancel_transaction','transaction_id':id_}])

def test_sqlite_round_trip_and_cas(tmp_path,tiny,row):
    store=TwinStore(tmp_path/'tw.sqlite');store.create(tiny)
    with pytest.raises(FDTError):store.create(tiny)
    loaded=store.load();assert loaded.content_digest==tiny.content_digest
    first=apply_events(loaded,[live_event(row)])
    second=apply_events(loaded,[live_event(row,id_='event2',tx='LIVE2')])
    store.save(first,0)
    with pytest.raises(FDTError):store.save(second,0)
    assert store.load().revision==1
    with pytest.raises(FDTError):store.update([],0)

def test_store_batch_rollback(tmp_path,tiny,row):
    store=TwinStore(tmp_path/'t.sqlite');store.create(tiny)
    events=[live_event(row),{'event_id':'bad','user_id':'U','type':'snapshot','snapshot':{}}]
    with pytest.raises(FDTError):store.update(events,0)
    assert store.load().content_digest==tiny.content_digest

def test_no_database_on_load(tmp_path):
    path=tmp_path/'missing.sqlite'
    with pytest.raises(FDTError):TwinStore(path).load()
    assert not path.exists()

def test_update_duplicate_no_new_revision(tmp_path,tiny,row):
    store=TwinStore(tmp_path/'t.sqlite');store.create(tiny)
    event=live_event(row);new=store.update([event],0)
    again=store.update([event],1)
    assert new.revision==again.revision==1

def test_snapshot_then_transaction_is_dirty(tiny,row):
    fresh=copy.deepcopy(tiny.snapshot);fresh['source']='LIVE'
    updated=apply_events(tiny,[{'event_id':'s1','user_id':'U','type':'snapshot','snapshot':fresh},live_event(row,date=tiny.as_of)])
    assert updated.metadata['snapshot_dirty']


def test_cancel_requires_new_snapshot(tiny,row):
    new=apply_events(tiny,[live_event(row,date=tiny.as_of)])
    fresh=copy.deepcopy(tiny.snapshot);fresh['source']='LIVE'
    new=apply_events(new,[{'event_id':'s1','user_id':'U','type':'snapshot','snapshot':fresh}])
    assert not new.cash_requirements()
    newer=apply_events(new,[{'event_id':'c1','user_id':'U','type':'cancel_transaction','transaction_id':'LIVE1'}])
    assert newer.cash_requirements()

def test_event_wrong_payload_for_type_rejected(tiny,row):
    event=live_event(row);event['snapshot']={}
    with pytest.raises(FDTError):apply_events(tiny,[event])


def test_snapshot_readiness_changes_digest(tiny):
    fresh=Twin(tiny.transactions,tiny.as_of,tiny.snapshot,metadata={'snapshot_dirty':False})
    stale=Twin(tiny.transactions,tiny.as_of,tiny.snapshot,metadata={'snapshot_dirty':True})
    assert fresh.content_digest!=stale.content_digest
    assert not fresh.cash_requirements()
    assert stale.cash_requirements()

def test_packaged_event_fixture_is_valid():
    from pathlib import Path
    from fdt.util import read_json
    root=Path(__file__).resolve().parents[1]
    t=Twin.from_csv(root/'data/demo/consumer_001.csv',snapshot=read_json(root/'examples/snapshot_001.json'))
    events=read_json(root/'examples/events_001.json')
    new=apply_events(t,events)
    assert new.revision==1
    assert new.as_of=='2026-09-04'
    assert not new.cash_requirements()
    assert apply_events(new,events) is new
