from __future__ import annotations
import copy
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from .errors import FDTError
from .ingest import normalize, record_signature, transaction_signature
from .model import Twin, validate_snapshot
from .util import canonical, digest, validate


def apply_events(twin: Twin, events: list[dict]) -> Twin:
    """All-or-nothing pure reducer. Authoritative snapshots, not SEED sums, move observed balances."""
    if not isinstance(events,list) or len(events)>1000:
        raise FDTError('EVENT_BATCH_LIMIT','이벤트 배열은 최대 1,000건입니다.')
    txs={t.id:t for t in twin.transactions}
    log=copy.deepcopy(twin.event_log); snapshot=copy.deepcopy(twin.snapshot)
    as_of=twin.as_of;changed=False;reclassified_count=0
    snapshot_dirty=twin.metadata.get('snapshot_dirty',False)
    for event in events:
        validate('event',event)
        if event['user_id']!=twin.user_id:
            raise FDTError('EVENT_USER_MISMATCH','다른 사용자의 이벤트입니다.')
        id_=event['event_id']; hashed=digest(event)
        if id_ in log:
            if log[id_]!=hashed: raise FDTError('EVENT_CONFLICT','동일 이벤트 ID의 내용이 다릅니다.')
            continue
        kind=event['type']
        if kind=='transaction':
            if 'transaction' not in event: raise FDTError('EVENT_PAYLOAD_REQUIRED','transaction')
            payload={k:v for k,v in event['transaction'].items()
                     if k not in ('direction','payment_method','exclude_tag','to_account_id') or v}
            t=normalize(payload,{'event_id':id_})
            if t.source!='LIVE': raise FDTError('SEED_NOT_LIVE','이벤트 경로는 LIVE만 허용합니다.')
            if t.user_id!=twin.user_id: raise FDTError('EVENT_USER_MISMATCH','거래 사용자 불일치')
            if t.id in txs:
                existing=txs[t.id]
                if record_signature(t)!=record_signature(existing):
                    raise FDTError('TRANSACTION_CONFLICT',t.id)
                if transaction_signature(t)!=transaction_signature(existing):
                    # Classification-only updates preserve balances and snapshot readiness.
                    txs[t.id]=t;reclassified_count+=1
            else:
                snapshot_dirty=True
                txs[t.id]=t
            as_of=max(as_of,t.date)
        elif kind=='cancel_transaction':
            if 'transaction_id' not in event: raise FDTError('EVENT_PAYLOAD_REQUIRED','transaction_id')
            id_tx=event['transaction_id']
            if id_tx not in txs: raise FDTError('UNKNOWN_ORIGINAL_TRANSACTION',id_tx)
            original=txs[id_tx]
            if original.source!='LIVE': raise FDTError('CANNOT_CANCEL_SEED','SEED는 실제 결제가 아닙니다.')
            if original.active: snapshot_dirty=True
            row={k:v for k,v in {**original.raw,'status':'CANCELED'}.items()
                 if k not in ('direction','payment_method','exclude_tag','to_account_id') or v}
            txs[id_tx]=normalize(row,{**original.origin,'cancel_event_id':id_})
            # The balance is not changed: a new account snapshot is needed from the gateway.
        else:
            if 'snapshot' not in event: raise FDTError('EVENT_PAYLOAD_REQUIRED','snapshot')
            validate_snapshot(event['snapshot'])
            if event['snapshot']['source']!='LIVE': raise FDTError('EVENT_SNAPSHOT_SOURCE','갱신 snapshot은 LIVE여야 합니다.')
            snapshot=copy.deepcopy(event['snapshot']);as_of=max(as_of,snapshot['as_of'])
            snapshot_dirty=snapshot['as_of']!=as_of
        log[id_]=hashed;changed=True
    if not changed: return twin
    if len(log)>10000: raise FDTError('EVENT_LOG_LIMIT','v0.1 이벤트 로그는 최대 10,000건입니다.')
    metadata=copy.deepcopy(twin.metadata)
    metadata['event_count']=len(log)
    metadata['snapshot_dirty']=snapshot_dirty
    if reclassified_count:
        metadata['reclassified_count']=metadata.get('reclassified_count',0)+reclassified_count
    return Twin(list(txs.values()),as_of,snapshot,metadata,twin.revision+1,log)


class TwinStore:
    """Single-user local SQLite store. CAS rejects a stale writer; connections always close."""
    def __init__(self,path: str | Path): self.path=Path(path).expanduser().resolve()

    def create(self,twin: Twin) -> None:
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path,timeout=10)) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS twin (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL)')
            if conn.execute('SELECT id FROM twin WHERE id=1').fetchone():
                raise FDTError('STORE_EXISTS','이미 Twin이 있습니다. 다른 DB 경로를 사용하세요.')
            conn.execute('INSERT INTO twin(id,revision,payload) VALUES(1,?,?)',(twin.revision,canonical(twin.to_dict())))

    def load(self) -> Twin:
        if not self.path.is_file(): raise FDTError('STORE_NOT_FOUND',str(self.path))
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=10)) as conn:
            row=conn.execute('SELECT revision,payload FROM twin WHERE id=1').fetchone()
        if not row: raise FDTError('STORE_EMPTY','저장된 Twin이 없습니다.')
        twin=Twin.from_dict(json.loads(row[1]))
        if twin.revision!=row[0]: raise FDTError('STORE_CORRUPT','revision 불일치')
        return twin

    def save(self,twin: Twin,expected_revision: int) -> None:
        if not self.path.is_file(): raise FDTError('STORE_NOT_FOUND',str(self.path))
        if twin.revision!=expected_revision+1:
            raise FDTError('INVALID_REVISION','변경 batch의 revision은 정확히 1 증가해야 합니다.')
        with closing(sqlite3.connect(self.path,timeout=10)) as conn, conn:
            cursor=conn.execute('UPDATE twin SET revision=?,payload=? WHERE id=1 AND revision=?',
                                (twin.revision,canonical(twin.to_dict()),expected_revision))
            if cursor.rowcount!=1: raise FDTError('REVISION_CONFLICT','다른 갱신이 먼저 저장되었습니다.')

    def update(self,events: list[dict],expected_revision: int) -> Twin:
        current=self.load()
        if current.revision!=expected_revision: raise FDTError('REVISION_CONFLICT','요청 revision이 최신이 아닙니다.')
        new=apply_events(current,events)
        if new is not current: self.save(new,expected_revision)
        return new
