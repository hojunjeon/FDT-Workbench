from __future__ import annotations
import copy
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any
import numpy as np
from .errors import FDTError
from .ingest import Transaction, audit, deduplicate, load_csv, normalize, transaction_signature
from .mapping import ENVELOPES, MAPPING_VERSION
from .util import days, digest, month_date, next_month, validate, warning

MODEL_VERSION = 'calendar-block-bootstrap/1.0'


def flow(t: Transaction) -> dict:
    return {'kind':t.kind, 'envelope':t.envelope, 'account_id':t.account_id,
            'card_id':t.card_id,'to_account_id':t.to_account_id,
            'protected': bool(t.is_fixed or t.is_recurring),
            'budgeted': t.exclude_tag=='NONE'}


def rule_dates(rule: dict, start: date, end: date) -> list[date]:
    d = date.fromisoformat(rule['next_date'])
    result = []
    for _ in range(1500):
        if d > end: break
        if d >= start: result.append(d)
        if rule['frequency']=='ONCE': break
        if rule['frequency']=='MONTHLY':
            d = next_month(d, rule['day_of_month'])
        else:
            d += timedelta(days=rule['interval_days'])
    return result


def validate_snapshot(snapshot: dict | None) -> None:
    if snapshot is None: return
    validate('snapshot', snapshot)
    for collection, key in [('accounts','account_id'),('cards','card_id'),('known_bills','bill_id'),
                            ('schedules','rule_id'),('assets','asset_id'),('liabilities','liability_id')]:
        ids = [x[key] for x in snapshot.get(collection,[])]
        if len(ids)!=len(set(ids)):
            raise FDTError('DUPLICATE_SNAPSHOT_ID', collection)
    accounts = {a['account_id'] for a in snapshot['accounts']}
    cards = {c['card_id']:c for c in snapshot.get('cards',[])}
    coverage=snapshot.get('coverage',{})
    if coverage.get('all_assets_reported') and 'assets' not in snapshot:
        raise FDTError('ASSET_COVERAGE_INCOMPLETE','전체 자산 보고 시 assets 배열(없으면 빈 배열)이 필요합니다.')
    if coverage.get('all_liabilities_reported') and 'liabilities' not in snapshot:
        raise FDTError('LIABILITY_COVERAGE_INCOMPLETE','전체 부채 보고 시 liabilities 배열(없으면 빈 배열)이 필요합니다.')
    for c in cards.values():
        if c['settlement_account_id'] not in accounts:
            raise FDTError('UNKNOWN_SETTLEMENT_ACCOUNT',c['card_id'])
        if c['kind']=='DEBIT' and c.get('opening_payable_krw',0)!=0:
            raise FDTError('DEBIT_PAYABLE',c['card_id'])
    for b in snapshot.get('known_bills',[]):
        if b['card_id'] not in cards or cards[b['card_id']]['kind']!='CREDIT':
            raise FDTError('INVALID_BILL_CARD',b['bill_id'])
        if b['due_date']<=snapshot['as_of']:
            raise FDTError('PAST_BILL_DUE_DATE','미래 납부일 또는 새 스냅샷이 필요합니다.',{'bill_id':b['bill_id']})
    for c in cards.values():
        if c['kind']=='CREDIT':
            billed = sum(b['amount_krw'] for b in snapshot.get('known_bills',[]) if b['card_id']==c['card_id'])
            if billed != c['opening_payable_krw']:
                raise FDTError('OPENING_PAYABLE_MISMATCH','기초 미결제액과 known_bills 합계가 다릅니다.',{'card_id':c['card_id']})
    for s in snapshot.get('schedules',[]):
        if s['frequency']=='MONTHLY' and 'day_of_month' not in s:
            raise FDTError('SCHEDULE_DAY_REQUIRED',s['rule_id'])
        if s['frequency']=='INTERVAL' and 'interval_days' not in s:
            raise FDTError('SCHEDULE_INTERVAL_REQUIRED',s['rule_id'])
        if s['next_date'] <= snapshot['as_of']:
            raise FDTError('PAST_SCHEDULE','next_date는 snapshot 기준일 이후여야 합니다.')
        if s.get('card_id'):
            if s['card_id'] not in cards or s['kind']!='expense':
                raise FDTError('INVALID_SCHEDULE_CARD',s['rule_id'])
        elif s.get('account_id') not in accounts:
            raise FDTError('INVALID_SCHEDULE_ACCOUNT',s['rule_id'])
        if s['kind']=='expense' and not s.get('envelope'):
            raise FDTError('SCHEDULE_ENVELOPE_REQUIRED',s['rule_id'])
        if s['kind']=='internal_transfer' and s.get('to_account_id') not in accounts:
            raise FDTError('INVALID_TRANSFER_TARGET',s['rule_id'])


@dataclass
class Twin:
    transactions: list[Transaction]
    as_of: str
    snapshot: dict | None = None
    metadata: dict = field(default_factory=dict)
    revision: int = 0
    event_log: dict = field(default_factory=dict)
    model: dict = field(init=False, repr=False)

    def __post_init__(self):
        self.transactions, _ = deduplicate(self.transactions)
        self.snapshot = copy.deepcopy(self.snapshot)
        validate_snapshot(self.snapshot)
        try: cutoff = date.fromisoformat(self.as_of)
        except (ValueError,TypeError) as e: raise FDTError('INVALID_AS_OF',str(e)) from e
        if any(t.date>self.as_of for t in self.transactions):
            raise FDTError('FUTURE_LEAKAGE','기준일 이후 거래가 Twin에 있습니다.')
        if (cutoff-date.fromisoformat(self.transactions[0].date)).days > 1096:
            raise FDTError('HISTORY_LIMIT','최대 이력 범위는 1,096일입니다.')
        if self.snapshot and self.snapshot['as_of']>self.as_of:
            raise FDTError('FUTURE_SNAPSHOT','미래 snapshot을 현재 Twin에 사용할 수 없습니다.')
        self.model = self._fit()

    @classmethod
    def from_csv(cls, paths: list[str | Path] | str | Path, *, snapshot: dict | None = None, as_of: str | None = None) -> 'Twin':
        txs, meta = load_csv([paths] if isinstance(paths,(str,Path)) else paths)
        cutoff = as_of or txs[-1].date
        try: date.fromisoformat(cutoff)
        except (ValueError,TypeError) as e: raise FDTError('INVALID_AS_OF',str(e)) from e
        selected = [t for t in txs if t.date<=cutoff]
        meta['future_rows_excluded'] = len(txs)-len(selected)
        if not selected: raise FDTError('NO_TRAINING_DATA','기준일 이전 거래가 없습니다.')
        return cls(selected,cutoff,snapshot,meta)

    @property
    def user_id(self) -> str: return self.transactions[0].user_id

    @property
    def twin_id(self) -> str: return 'twin-'+digest(self.user_id)[:16]

    @property
    def content_digest(self) -> str:
        return digest({'transactions':[transaction_signature(t) for t in self.transactions],
                       'as_of':self.as_of,'snapshot':self.snapshot,'snapshot_dirty':bool(self.metadata.get('snapshot_dirty')),
                       'model':MODEL_VERSION,'mapping':MAPPING_VERSION})

    def to_dict(self) -> dict:
        return {'transactions':[t.as_dict() for t in self.transactions], 'as_of':self.as_of,
                'snapshot':self.snapshot,'metadata':self.metadata,'revision':self.revision,'event_log':self.event_log}

    @classmethod
    def from_dict(cls, value: dict) -> 'Twin':
        # Re-run normalization from the preserved raw row so a mapping-version
        # upgrade fixes already-created local Twins without rewriting the ledger.
        transactions = []
        for stored in value['transactions']:
            raw = stored.get('raw') if isinstance(stored, dict) else None
            if isinstance(raw, dict) and raw:
                try:
                    transactions.append(normalize(raw, stored.get('origin') or {}))
                    continue
                except FDTError:
                    pass
            transactions.append(Transaction(**stored))
        return cls(transactions,value['as_of'],value.get('snapshot'),
                   value.get('metadata',{}),value.get('revision',0),value.get('event_log',{}))

    def _fit(self) -> dict:
        active = [t for t in self.transactions if t.active and t.kind!='card_settlement']
        components: list[dict] = []
        idx: dict[str,int] = {}
        def component(f: dict) -> int:
            key=digest(f)
            if key not in idx:
                idx[key]=len(components);components.append(f)
            return idx[key]
        groups: dict[tuple,list[Transaction]] = defaultdict(list)
        for t in active:
            g=(t.merchant_id,t.kind,t.raw_subcategory,t.account_id,t.card_id,t.to_account_id,
               t.is_recurring if t.kind=='income' else None)
            groups[g].append(t)
        rules, assigned = [], set()
        for key, ts in sorted(groups.items(),key=lambda kv:str(kv[0])):
            by_date: dict[str,list[Transaction]] = defaultdict(list)
            for t in ts: by_date[t.date].append(t)
            ds=sorted(date.fromisoformat(d) for d in by_date)
            hinted=any(t.is_recurring for t in ts)
            if len(ds)<2 or (not hinted and len(ds)<3): continue
            gaps=[(b-a).days for a,b in zip(ds,ds[1:])]
            interval=None; freq=None
            for p in (7,14,28):
                if len(ds)>=3 and all(g==p for g in gaps):
                    freq='INTERVAL';interval=p;break
            dom=int(median(d.day for d in ds))
            if freq is None and len({(d.year,d.month) for d in ds})==len(ds) and all(25<=g<=36 for g in gaps) and max(d.day for d in ds)-min(d.day for d in ds)<=5:
                freq='MONTHLY'
            if freq is None: continue
            f=flow(ts[-1]);f['protected']=True
            fidx=component(f)
            amounts=[sum(t.amount_krw for t in by_date[d.isoformat()]) for d in ds]
            nxt=ds[-1]
            while nxt.isoformat()<=self.as_of:
                nxt = next_month(nxt,dom) if freq=='MONTHLY' else nxt+timedelta(days=interval)
            rule={'rule_id':'rec-'+digest(key)[:12], 'frequency':freq,'next_date':nxt.isoformat(),
                  'day_of_month':dom,'interval_days':interval,'component':fidx,'amount_samples':amounts,
                  'expected_amount_krw':int(median(amounts)),'evidence':[t.id for t in ts],
                  'source':'INFERRED','evidence_level':'limited' if len(ds)<3 else 'repeated',
                  'merchant_id':ts[-1].merchant_id}
            rules.append(rule);assigned.update(t.id for t in ts)
        snapshot=self.snapshot or {}
        inferred_ids={r['rule_id'] for r in rules}
        replacements=[s['replaces_rule_id'] for s in snapshot.get('schedules',[]) if s.get('replaces_rule_id')]
        if len(replacements)!=len(set(replacements)):
            raise FDTError('DUPLICATE_RULE_REPLACEMENT','하나의 추정 일정은 한 번만 교체할 수 있습니다.')
        if any(r not in inferred_ids for r in replacements):
            raise FDTError('UNKNOWN_RULE_REPLACEMENT','replaces_rule_id가 현재 추정 모델에 없습니다.')
        rules=[r for r in rules if r['rule_id'] not in replacements]
        for s in snapshot.get('schedules',[]):
            f={'kind':s['kind'],'envelope':s.get('envelope'),'account_id':s.get('account_id'),
               'card_id':s.get('card_id'),'to_account_id':s.get('to_account_id'),'protected':True,'budgeted':True}
            r={'rule_id':s['rule_id'],'frequency':s['frequency'],'next_date':s['next_date'],
               'day_of_month':s.get('day_of_month'),'interval_days':s.get('interval_days'),
               'component':component(f),'amount_samples':[s['amount_krw']],
               'expected_amount_krw':s['amount_krw'],'evidence':['snapshot/schedules/'+s['rule_id']],
               'source':snapshot['source'],'evidence_level':'user_supplied','merchant_id':None}
            rules.append(r)
        if len({r['rule_id'] for r in rules})!=len(rules):
            raise FDTError('RULE_ID_CONFLICT','rule_id가 중복됩니다.')
        if len(rules)>100: raise FDTError('RULE_LIMIT','최대 반복/수동 일정 100개입니다.')
        residual = [t for t in active if t.id not in assigned]
        for t in residual: component(flow(t))
        if not components:
            component({'kind':'income','envelope':None,'account_id':None,'card_id':None,'to_account_id':None,'protected':False,'budgeted':False})
        if len(components)>200: raise FDTError('COMPONENT_LIMIT','최대 금융 채널 200개입니다.')
        calendar_days=days(date.fromisoformat(self.transactions[0].date),date.fromisoformat(self.as_of))
        daily=np.zeros((len(calendar_days),len(components)),dtype=np.int64)
        start=calendar_days[0]
        for t in residual:
            daily[(date.fromisoformat(t.date)-start).days,component(flow(t))]+=t.amount_krw
        consumption=[t for t in active if t.kind=='expense']
        income=[t for t in active if t.kind=='income']
        support=sum(t.amount_krw for t in income if '가족' in t.raw_subcategory)
        weekday=[]
        for w in range(7):
            denominator=sum(d.weekday()==w for d in calendar_days)
            total=sum(t.amount_krw for t in consumption if date.fromisoformat(t.date).weekday()==w)
            weekday.append({'weekday':w,'observed_days':denominator,'mean_expense_krw':round(total/denominator) if denominator else None})
        behavior={'observation_days':len(calendar_days),'weekday_expenses':weekday,
                  'impulse_labeled_count':sum(t.spend_pattern=='IMPULSE' for t in consumption),
                  'impulse_labeled_spend_krw':sum(t.amount_krw for t in consumption if t.spend_pattern=='IMPULSE'),
                  'support_income_ratio':support/sum(t.amount_krw for t in income) if sum(t.amount_krw for t in income) else None,
                  'income_mean_monthly_krw':round(sum(t.amount_krw for t in income)*30.4375/len(calendar_days)),
                  'rule_count':len(rules), 'assigned_transaction_count':len(assigned)}
        return {'components':components,'daily':daily,'rules':sorted(rules,key=lambda r:r['rule_id']),
                'start':start,'dates':calendar_days,'behavior':behavior,'audit':audit(self.transactions)}

    def cash_requirements(self) -> list[str]:
        if not self.snapshot: return ['snapshot.accounts','snapshot.cards (결제 채널별 종류/정산 조건)']
        missing=[]
        if self.metadata.get('snapshot_dirty'):
            missing.append('snapshot (관측 거래 갱신 후 새 snapshot 필요)')
        if self.snapshot['as_of']!=self.as_of: missing.append('snapshot.as_of (새 기준일의 권위 잔액 필요)')
        accounts={a['account_id'] for a in self.snapshot['accounts']}
        cards={c['card_id'] for c in self.snapshot.get('cards',[])}
        if not accounts: missing.append('snapshot.accounts')
        for f in self.model['components']:
            if f['card_id']:
                if f['card_id'] not in cards: missing.append('snapshot.cards/'+f['card_id'])
            elif f['account_id'] and f['account_id'] not in accounts:
                missing.append('snapshot.accounts/'+f['account_id'])
            elif not f['account_id']:
                missing.append('flow.account_id')
            if f['kind']=='internal_transfer' and f['to_account_id'] not in accounts:
                missing.append('internal_transfer.to_account_id')
        return sorted(set(missing))

    def relationships(self) -> dict:
        nodes: dict[str,dict] = {self.user_id:{'id':self.user_id,'type':'user'}}
        edges:set[tuple[str,str,str]]=set()
        for t in self.transactions:
            nodes[t.id]={'id':t.id,'type':'transaction'}
            edges.add((self.user_id,t.id,'has_observation'))
            edges.add((t.id,t.merchant_id,'counterparty'))
            if t.envelope: edges.add((t.id,t.envelope,'classified_as'))
            if t.account_id: edges.add((t.id,t.account_id,'recorded_on'))
            if t.card_id: edges.add((t.id,t.card_id,'paid_by'))
            for id_,type_ in [(t.account_id,'account'),(t.card_id,'card'),(t.merchant_id,'merchant'),(t.envelope,'envelope')]:
                if id_: nodes[id_]={'id':id_,'type':type_}
            if t.account_id: edges.add((self.user_id,t.account_id,'owns'))
            if t.card_id: edges.add((self.user_id,t.card_id,'owns'))
            if t.envelope: edges.add((t.merchant_id,t.envelope,'observed_classification'))
        for c in (self.snapshot or {}).get('cards',[]):
            for id_,kind in [(c['card_id'],'card'),(c['settlement_account_id'],'account')]: nodes[id_]={'id':id_,'type':kind}
            edges.add((c['card_id'],c['settlement_account_id'],'settles_from'))
        for a in (self.snapshot or {}).get('assets',[]):
            nodes[a['asset_id']]={'id':a['asset_id'],'type':a['kind']}
            edges.add((self.user_id,a['asset_id'],'reported_asset'))
        for liability in (self.snapshot or {}).get('liabilities',[]):
            nodes[liability['liability_id']]={'id':liability['liability_id'],'type':'liability'}
            edges.add((self.user_id,liability['liability_id'],'reported_liability'))
        return {'nodes':sorted(nodes.values(),key=lambda n:n['id']),
                'edges':[{'from':a,'to':b,'relationship':c} for a,b,c in sorted(edges)]}

    def inspect(self) -> dict:
        snapshot=self.snapshot or {}
        missing=self.cash_requirements()
        cash=sum(a['balance_krw'] for a in snapshot.get('accounts',[])) if snapshot and not missing else None
        assets=sum(a['value_krw'] for a in snapshot.get('assets',[]))
        debt=sum(d['principal_krw'] for d in snapshot.get('liabilities',[]))
        payable=sum(c.get('opening_payable_krw',0) for c in snapshot.get('cards',[]))
        complete=all(snapshot.get('coverage',{}).get(k,False) for k in ('all_assets_reported','all_liabilities_reported'))
        return {'twin_id':self.twin_id,'user_id':self.user_id,'revision':self.revision,'as_of':self.as_of,
                'input_digest':self.content_digest,'model_version':MODEL_VERSION,'mapping_version':MAPPING_VERSION,
                'audit':self.model['audit'],'provenance':self.metadata,
                'state':{'managed_cash_krw':cash,'reported_other_assets_krw':assets if 'assets' in snapshot else None,
                         'reported_liabilities_krw':debt if 'liabilities' in snapshot else None,
                         'net_worth_krw':cash+assets-debt-payable if complete and cash is not None else None,
                         'source':snapshot.get('source'),'snapshot_as_of':snapshot.get('as_of'),
                         'absolute_cash_ready':not missing,'missing':missing},
                'behavior':self.model['behavior'],'recurring_rules':self.model['rules'],
                'components':self.model['components'],'relationships':self.relationships()}
