from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass, field, fields
from datetime import date, timedelta
from pathlib import Path
from statistics import median
import numpy as np

from .errors import FDTError
from .ingest import Transaction, audit, deduplicate, load_csv, normalize, transaction_signature
from .mapping import FIXED_GROUPS, MAPPING_VERSION, fixed_group as mapped_fixed_group
from .util import days, digest, next_month, validate, warning


MODEL_VERSION = 'calendar-block-bootstrap/2.0'


def flow(t: Transaction) -> dict:
    """Return the complete simulation component contract for a transaction."""
    return {
        'kind': t.kind,
        'envelope': t.envelope,
        'fixed_group': t.fixed_group,
        'pending': t.pending,
        'account_id': t.account_id,
        'card_id': t.card_id,
        'to_account_id': t.to_account_id,
        'protected': t.kind == 'fixed_expense',
        'budgeted': t.exclude_tag == 'NONE' and t.kind == 'expense' and not t.pending,
    }


def rule_dates(rule: dict, start: date, end: date) -> list[date]:
    d = date.fromisoformat(rule['next_date'])
    result = []
    for _ in range(1500):
        if d > end:
            break
        if d >= start:
            result.append(d)
        if rule['frequency'] == 'ONCE':
            break
        if rule['frequency'] == 'MONTHLY':
            d = next_month(d, rule['day_of_month'])
        else:
            d += timedelta(days=rule['interval_days'])
    return result


def validate_snapshot(snapshot: dict | None) -> None:
    if snapshot is None:
        return

    # Emit contract-specific schedule errors before generic schema validation.
    replacement_ids: set[str] = set()
    for schedule in snapshot.get('schedules', []):
        if not isinstance(schedule, dict):
            continue
        kind = schedule.get('kind')
        if kind == 'fixed_expense':
            if not schedule.get('fixed_group'):
                raise FDTError('SCHEDULE_FIXED_GROUP_REQUIRED', schedule.get('rule_id', ''))
            if 'envelope' in schedule:
                raise FDTError('SCHEDULE_ENVELOPE_FORBIDDEN', schedule.get('rule_id', ''))
        elif 'fixed_group' in schedule:
            raise FDTError('SCHEDULE_FIXED_GROUP_FORBIDDEN', schedule.get('rule_id', ''))
        ids = schedule.get('replaces_transaction_ids', [])
        if isinstance(ids, list):
            for transaction_id in ids:
                if transaction_id in replacement_ids:
                    raise FDTError('DUPLICATE_TRANSACTION_REPLACEMENT', transaction_id)
                replacement_ids.add(transaction_id)

    validate('snapshot', snapshot)
    for collection, key in [
        ('accounts', 'account_id'), ('cards', 'card_id'), ('known_bills', 'bill_id'),
        ('schedules', 'rule_id'), ('assets', 'asset_id'), ('liabilities', 'liability_id'),
    ]:
        ids = [x[key] for x in snapshot.get(collection, [])]
        if len(ids) != len(set(ids)):
            raise FDTError('DUPLICATE_SNAPSHOT_ID', collection)

    accounts = {a['account_id'] for a in snapshot['accounts']}
    cards = {c['card_id']: c for c in snapshot.get('cards', [])}
    coverage = snapshot.get('coverage', {})
    if coverage.get('all_assets_reported') and 'assets' not in snapshot:
        raise FDTError('ASSET_COVERAGE_INCOMPLETE', '전체 자산 보고 시 assets 배열(없으면 빈 배열)이 필요합니다.')
    if coverage.get('all_liabilities_reported') and 'liabilities' not in snapshot:
        raise FDTError('LIABILITY_COVERAGE_INCOMPLETE', '전체 부채 보고 시 liabilities 배열(없으면 빈 배열)이 필요합니다.')

    for card in cards.values():
        if card['settlement_account_id'] not in accounts:
            raise FDTError('UNKNOWN_SETTLEMENT_ACCOUNT', card['card_id'])
        if card['kind'] == 'DEBIT' and card.get('opening_payable_krw', 0) != 0:
            raise FDTError('DEBIT_PAYABLE', card['card_id'])
    for bill in snapshot.get('known_bills', []):
        if bill['card_id'] not in cards or cards[bill['card_id']]['kind'] != 'CREDIT':
            raise FDTError('INVALID_BILL_CARD', bill['bill_id'])
        if bill['due_date'] <= snapshot['as_of']:
            raise FDTError('PAST_BILL_DUE_DATE', '미래 납부일 또는 새 스냅샷이 필요합니다.', {'bill_id': bill['bill_id']})
    for card in cards.values():
        if card['kind'] == 'CREDIT':
            billed = sum(b['amount_krw'] for b in snapshot.get('known_bills', []) if b['card_id'] == card['card_id'])
            if billed != card['opening_payable_krw']:
                raise FDTError('OPENING_PAYABLE_MISMATCH', '기초 미결제액과 known_bills 합계가 다릅니다.', {'card_id': card['card_id']})

    for schedule in snapshot.get('schedules', []):
        if schedule['frequency'] == 'MONTHLY' and 'day_of_month' not in schedule:
            raise FDTError('SCHEDULE_DAY_REQUIRED', schedule['rule_id'])
        if schedule['frequency'] == 'INTERVAL' and 'interval_days' not in schedule:
            raise FDTError('SCHEDULE_INTERVAL_REQUIRED', schedule['rule_id'])
        if schedule['next_date'] <= snapshot['as_of']:
            raise FDTError('PAST_SCHEDULE', 'next_date는 snapshot 기준일 이후여야 합니다.')
        if schedule.get('card_id'):
            if schedule['card_id'] not in cards or schedule['kind'] not in ('expense', 'fixed_expense'):
                raise FDTError('INVALID_SCHEDULE_CARD', schedule['rule_id'])
        elif schedule.get('account_id') not in accounts:
            raise FDTError('INVALID_SCHEDULE_ACCOUNT', schedule['rule_id'])
        if schedule['kind'] == 'expense' and not schedule.get('envelope'):
            raise FDTError('SCHEDULE_ENVELOPE_REQUIRED', schedule['rule_id'])
        if schedule['kind'] == 'internal_transfer' and schedule.get('to_account_id') not in accounts:
            raise FDTError('INVALID_TRANSFER_TARGET', schedule['rule_id'])


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
        try:
            cutoff = date.fromisoformat(self.as_of)
        except (ValueError, TypeError) as e:
            raise FDTError('INVALID_AS_OF', str(e)) from e
        if any(t.date > self.as_of for t in self.transactions):
            raise FDTError('FUTURE_LEAKAGE', '기준일 이후 거래가 Twin에 있습니다.')
        if (cutoff - date.fromisoformat(self.transactions[0].date)).days > 1096:
            raise FDTError('HISTORY_LIMIT', '최대 이력 범위는 1,096일입니다.')
        if self.snapshot and self.snapshot['as_of'] > self.as_of:
            raise FDTError('FUTURE_SNAPSHOT', '미래 snapshot을 현재 Twin에 사용할 수 없습니다.')
        self.model = self._fit()

    @classmethod
    def from_csv(
        cls,
        paths: list[str | Path] | str | Path,
        *,
        snapshot: dict | None = None,
        as_of: str | None = None,
    ) -> 'Twin':
        txs, meta = load_csv([paths] if isinstance(paths, (str, Path)) else paths)
        cutoff = as_of or txs[-1].date
        try:
            date.fromisoformat(cutoff)
        except (ValueError, TypeError) as e:
            raise FDTError('INVALID_AS_OF', str(e)) from e
        selected = [t for t in txs if t.date <= cutoff]
        meta['future_rows_excluded'] = len(txs) - len(selected)
        if not selected:
            raise FDTError('NO_TRAINING_DATA', '기준일 이전 거래가 없습니다.')
        return cls(selected, cutoff, snapshot, dict(meta))

    @property
    def user_id(self) -> str:
        return self.transactions[0].user_id

    @property
    def twin_id(self) -> str:
        return 'twin-' + digest(self.user_id)[:16]

    @property
    def content_digest(self) -> str:
        return digest({
            'transactions': [transaction_signature(t) for t in self.transactions],
            'as_of': self.as_of,
            'snapshot': self.snapshot,
            'snapshot_dirty': bool(self.metadata.get('snapshot_dirty')),
            'model': MODEL_VERSION,
            'mapping': MAPPING_VERSION,
        })

    def to_dict(self) -> dict:
        return {
            'transactions': [t.as_dict() for t in self.transactions],
            'as_of': self.as_of,
            'snapshot': self.snapshot,
            'metadata': self.metadata,
            'revision': self.revision,
            'event_log': self.event_log,
        }

    @classmethod
    def from_dict(cls, value: dict) -> 'Twin':
        transactions = []
        for stored in value['transactions']:
            raw = stored.get('raw') if isinstance(stored, dict) else None
            if isinstance(raw, dict) and raw:
                try:
                    transactions.append(normalize(raw, stored.get('origin') or {}))
                    continue
                except FDTError:
                    pass
            legacy = dict(stored)
            legacy.setdefault(
                'fixed_group',
                mapped_fixed_group(legacy.get('raw_subcategory', '')) if legacy.get('kind') == 'fixed_expense' else None,
            )
            legacy.setdefault('pending', legacy.get('kind') == 'expense' and legacy.get('confirm_status') == 'PENDING')
            legacy.setdefault('origin', {})
            legacy.setdefault('raw', {})
            allowed = {f.name for f in fields(Transaction)}
            transactions.append(Transaction(**{k: v for k, v in legacy.items() if k in allowed}))
        return cls(
            transactions,
            value['as_of'],
            value.get('snapshot'),
            value.get('metadata', {}),
            value.get('revision', 0),
            value.get('event_log', {}),
        )

    @staticmethod
    def _cadence(ds: list[date]) -> tuple[str, int | None] | None:
        if len(ds) < 3:
            return None
        gaps = [(b - a).days for a, b in zip(ds, ds[1:])]
        for interval in (7, 14, 28):
            if all(g == interval for g in gaps):
                return 'INTERVAL', interval
        if (
            len({(d.year, d.month) for d in ds}) == len(ds)
            and all(25 <= g <= 36 for g in gaps)
            and max(d.day for d in ds) - min(d.day for d in ds) <= 5
        ):
            return 'MONTHLY', None
        return None

    @classmethod
    def _recurring_subset(cls, dates: list[date], amounts: dict[date, int]) -> tuple[list[date], tuple[str, int | None]] | None:
        # Only the (near) full observation set can prove a cadence.  Searching arbitrary date subsets
        # would turn any frequently visited merchant into a fake weekly rule (three visits 7 days apart).
        # The one tolerated exception is an amount outlier: a one-off invoice at a merchant that also
        # pays a fixed retainer.  Drop days whose amount is more than 50% away from the median and retry once.
        full = cls._cadence(dates)
        if full:
            return dates, full
        if len(dates) < 4:
            return None
        center = median(amounts[d] for d in dates)
        regular = [d for d in dates if center and abs(amounts[d] - center) <= 0.5 * center]
        if len(regular) < len(dates) and len(regular) >= 3:
            cadence = cls._cadence(regular)
            if cadence:
                return regular, cadence
        return None

    @staticmethod
    def _manual_flow(schedule: dict) -> dict:
        kind = schedule['kind']
        return {
            'kind': kind,
            'envelope': schedule.get('envelope'),
            'fixed_group': schedule.get('fixed_group'),
            'pending': False,
            'account_id': schedule.get('account_id'),
            'card_id': schedule.get('card_id'),
            'to_account_id': schedule.get('to_account_id'),
            'protected': True,
            'budgeted': kind == 'expense',
        }

    def _fit(self) -> dict:
        active = [t for t in self.transactions if t.active and t.kind != 'card_settlement']
        components: list[dict] = []
        idx: dict[str, int] = {}

        def component(flow_value: dict) -> int:
            key = digest(flow_value)
            if key not in idx:
                idx[key] = len(components)
                components.append(flow_value)
            return idx[key]

        groups: dict[tuple, list[Transaction]] = defaultdict(list)
        for t in active:
            key = (t.merchant_id, t.kind, t.raw_subcategory, t.account_id, t.card_id, t.to_account_id)
            groups[key].append(t)

        rules: list[dict] = []
        assigned: set[str] = set()
        for key, ts in sorted(groups.items(), key=lambda kv: str(kv[0])):
            by_date: dict[str, list[Transaction]] = defaultdict(list)
            for t in ts:
                by_date[t.date].append(t)
            dates = sorted(date.fromisoformat(d) for d in by_date)
            day_amounts = {d: sum(t.amount_krw for t in by_date[d.isoformat()]) for d in dates}
            subset = self._recurring_subset(dates, day_amounts)
            if not subset:
                continue
            recurring_dates, (frequency, interval) = subset
            selected = [t for d in recurring_dates for t in by_date[d.isoformat()]]
            representative = selected[-1]
            flow_value = flow(representative)
            flow_value['protected'] = True
            flow_idx = component(flow_value)
            amounts = [sum(t.amount_krw for t in by_date[d.isoformat()]) for d in recurring_dates]
            day_of_month = int(median(d.day for d in recurring_dates))
            next_date_value = recurring_dates[-1]
            while next_date_value.isoformat() <= self.as_of:
                next_date_value = (
                    next_month(next_date_value, day_of_month)
                    if frequency == 'MONTHLY'
                    else next_date_value + timedelta(days=interval)
                )
            group_digest = digest(key)
            rules.append({
                'rule_id': 'rec-' + group_digest[:12],
                'frequency': frequency,
                'next_date': next_date_value.isoformat(),
                'day_of_month': day_of_month,
                'interval_days': interval,
                'component': flow_idx,
                'amount_samples': amounts,
                'expected_amount_krw': int(median(amounts)),
                'evidence': [t.id for t in selected],
                'source': 'INFERRED',
                'evidence_level': 'repeated',
                'merchant_id': representative.merchant_id,
                'kind': flow_value['kind'],
                'fixed_group': flow_value['fixed_group'],
            })
            assigned.update(t.id for t in selected)

        snapshot = self.snapshot or {}
        inferred_ids = {r['rule_id'] for r in rules}
        replaces_rules = [s['replaces_rule_id'] for s in snapshot.get('schedules', []) if s.get('replaces_rule_id')]
        for schedule in snapshot.get('schedules', []):
            if schedule.get('replaces_rule_id') and schedule.get('replaces_transaction_ids'):
                raise FDTError('REPLACEMENT_CONFLICT', schedule['rule_id'])
        if len(replaces_rules) != len(set(replaces_rules)):
            raise FDTError('DUPLICATE_RULE_REPLACEMENT', '하나의 추정 일정은 한 번만 교체할 수 있습니다.')
        if any(rule_id not in inferred_ids for rule_id in replaces_rules):
            raise FDTError('UNKNOWN_RULE_REPLACEMENT', 'replaces_rule_id가 현재 추정 모델에 없습니다.')
        rules = [r for r in rules if r['rule_id'] not in replaces_rules]

        transaction_by_id = {t.id: t for t in active}
        replacement_ids: set[str] = set()
        for schedule in snapshot.get('schedules', []):
            for transaction_id in schedule.get('replaces_transaction_ids', []):
                if transaction_id in replacement_ids:
                    raise FDTError('DUPLICATE_TRANSACTION_REPLACEMENT', transaction_id)
                transaction = transaction_by_id.get(transaction_id)
                if transaction is None or transaction.kind != 'fixed_expense':
                    raise FDTError('UNKNOWN_TRANSACTION_REPLACEMENT', transaction_id)
                if transaction_id in assigned:
                    raise FDTError('REPLACEMENT_ALREADY_RULED', transaction_id)
                if any([
                    schedule.get('fixed_group') != transaction.fixed_group,
                    schedule.get('account_id') != transaction.account_id,
                    schedule.get('card_id') != transaction.card_id,
                ]):
                    raise FDTError('REPLACEMENT_CHANNEL_MISMATCH', transaction_id)
                replacement_ids.add(transaction_id)
                assigned.add(transaction_id)

        manual_schedules = snapshot.get('schedules', [])

        residual = [t for t in active if t.id not in assigned]
        for t in residual:
            component(flow(t))
        # Append manual components after observed components so replacing a rule
        # cannot reindex the historical daily matrix.
        for schedule in manual_schedules:
            flow_value = self._manual_flow(schedule)
            flow_idx = component(flow_value)
            rules.append({
                'rule_id': schedule['rule_id'],
                'frequency': schedule['frequency'],
                'next_date': schedule['next_date'],
                'day_of_month': schedule.get('day_of_month'),
                'interval_days': schedule.get('interval_days'),
                'component': flow_idx,
                'amount_samples': [schedule['amount_krw']],
                'expected_amount_krw': schedule['amount_krw'],
                'evidence': ['snapshot/schedules/' + schedule['rule_id']],
                'source': snapshot['source'],
                'evidence_level': 'user_supplied',
                'merchant_id': None,
                'kind': flow_value['kind'],
                'fixed_group': flow_value['fixed_group'],
            })
        if len({r['rule_id'] for r in rules}) != len(rules):
            raise FDTError('RULE_ID_CONFLICT', 'rule_id가 중복됩니다.')
        if len(rules) > 100:
            raise FDTError('RULE_LIMIT', '최대 반복/수동 일정 100개입니다.')
        if not components:
            component({
                'kind': 'income', 'envelope': None, 'fixed_group': None, 'pending': False,
                'account_id': None, 'card_id': None, 'to_account_id': None,
                'protected': False, 'budgeted': False,
            })
        if len(components) > 200:
            raise FDTError('COMPONENT_LIMIT', '최대 금융 채널 200개입니다.')

        calendar_days = days(date.fromisoformat(self.transactions[0].date), date.fromisoformat(self.as_of))
        start = calendar_days[0]
        daily = np.zeros((len(calendar_days), len(components)), dtype=np.int64)
        for t in residual:
            daily[(date.fromisoformat(t.date) - start).days, component(flow(t))] += t.amount_krw

        consumption = [t for t in active if t.kind == 'expense']
        income = [t for t in active if t.kind == 'income']
        fixed = [t for t in active if t.kind == 'fixed_expense']
        pending = [t for t in consumption if t.pending]
        support_total = sum(t.amount_krw for t in income if '가족' in t.raw_subcategory)
        weekday = []
        for weekday_number in range(7):
            denominator = sum(d.weekday() == weekday_number for d in calendar_days)
            total = sum(t.amount_krw for t in consumption if date.fromisoformat(t.date).weekday() == weekday_number)
            weekday.append({
                'weekday': weekday_number,
                'observed_days': denominator,
                'mean_expense_krw': round(total / denominator) if denominator else None,
            })

        audit_result = audit(self.transactions)
        # IGNORED_LABEL_COLUMNS is emitted once by the engine from metadata; not duplicated here.
        unscheduled = [t for t in residual if t.kind == 'fixed_expense']
        if unscheduled:
            grouped: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
            for t in unscheduled:
                grouped[(t.raw_subcategory, t.fixed_group)].append(t)
            items = []
            for (raw_subcategory, fixed_group), rows in sorted(grouped.items()):
                last = max(rows, key=lambda t: (t.date, t.time, t.id))
                items.append({
                    'raw_subcategory': raw_subcategory,
                    'fixed_group': fixed_group,
                    'count': len(rows),
                    'total_krw': sum(t.amount_krw for t in rows),
                    'last_date': last.date,
                    'last_amount_krw': last.amount_krw,
                    'transaction_ids': [t.id for t in sorted(rows, key=lambda t: (t.date, t.time, t.id))],
                })
            audit_result['warnings'].append(warning('FIXED_UNSCHEDULED', '반복 규칙에 배정되지 않은 고정지출이 있습니다.', items=items))

        fixed_totals = {group: sum(t.amount_krw for t in fixed if t.fixed_group == group) for group in FIXED_GROUPS}
        total_income = sum(t.amount_krw for t in income)
        behavior = {
            'observation_days': len(calendar_days),
            'weekday_expenses': weekday,
            'fixed_monthly_observed_krw': round(sum(fixed_totals.values()) * 30.4375 / len(calendar_days)),
            'fixed_groups_observed_krw': fixed_totals,
            'pending_consumption_krw': sum(t.amount_krw for t in pending),
            'support_income_ratio': support_total / total_income if total_income else None,
            'income_mean_monthly_krw': round(total_income * 30.4375 / len(calendar_days)),
            'rule_count': len(rules),
            'assigned_transaction_count': len(assigned),
        }
        return {
            'components': components,
            'daily': daily,
            'rules': sorted(rules, key=lambda r: r['rule_id']),
            'start': start,
            'dates': calendar_days,
            'behavior': behavior,
            'audit': audit_result,
        }

    def cash_requirements(self) -> list[str]:
        if not self.snapshot:
            return ['snapshot.accounts', 'snapshot.cards (결제 채널별 종류/정산 조건)']
        missing = []
        if self.metadata.get('snapshot_dirty'):
            missing.append('snapshot (관측 거래 갱신 후 새 snapshot 필요)')
        if self.snapshot['as_of'] != self.as_of:
            missing.append('snapshot.as_of (새 기준일의 권위 잔액 필요)')
        accounts = {a['account_id'] for a in self.snapshot['accounts']}
        cards = {c['card_id'] for c in self.snapshot.get('cards', [])}
        if not accounts:
            missing.append('snapshot.accounts')
        for flow_value in self.model['components']:
            if flow_value['card_id']:
                if flow_value['card_id'] not in cards:
                    missing.append('snapshot.cards/' + flow_value['card_id'])
            elif flow_value['account_id'] and flow_value['account_id'] not in accounts:
                missing.append('snapshot.accounts/' + flow_value['account_id'])
            elif not flow_value['account_id']:
                missing.append('flow.account_id')
            if flow_value['kind'] == 'internal_transfer' and flow_value['to_account_id'] not in accounts:
                missing.append('internal_transfer.to_account_id')
        return sorted(set(missing))

    def relationships(self) -> dict:
        nodes: dict[str, dict] = {self.user_id: {'id': self.user_id, 'type': 'user'}}
        edges: set[tuple[str, str, str]] = set()
        for t in self.transactions:
            nodes[t.id] = {'id': t.id, 'type': 'transaction'}
            edges.add((self.user_id, t.id, 'has_observation'))
            if t.merchant_id:
                edges.add((t.id, t.merchant_id, 'counterparty'))
            if t.envelope:
                edges.add((t.id, t.envelope, 'classified_as'))
            if t.fixed_group:
                fixed_id = 'fixed-group:' + t.fixed_group
                nodes[fixed_id] = {'id': fixed_id, 'type': 'fixed_group'}
                edges.add((t.id, fixed_id, 'classified_as'))
            if t.account_id:
                edges.add((t.id, t.account_id, 'recorded_on'))
            if t.card_id:
                edges.add((t.id, t.card_id, 'paid_by'))
            for id_, type_ in [(t.account_id, 'account'), (t.card_id, 'card'), (t.merchant_id, 'merchant'), (t.envelope, 'envelope')]:
                if id_:
                    nodes[id_] = {'id': id_, 'type': type_}
            if t.account_id:
                edges.add((self.user_id, t.account_id, 'owns'))
            if t.card_id:
                edges.add((self.user_id, t.card_id, 'owns'))
            if t.envelope:
                edges.add((t.merchant_id, t.envelope, 'observed_classification'))
        for card in (self.snapshot or {}).get('cards', []):
            for id_, kind in [(card['card_id'], 'card'), (card['settlement_account_id'], 'account')]:
                nodes[id_] = {'id': id_, 'type': kind}
            edges.add((card['card_id'], card['settlement_account_id'], 'settles_from'))
        for asset in (self.snapshot or {}).get('assets', []):
            nodes[asset['asset_id']] = {'id': asset['asset_id'], 'type': asset['kind']}
            edges.add((self.user_id, asset['asset_id'], 'reported_asset'))
        for liability in (self.snapshot or {}).get('liabilities', []):
            nodes[liability['liability_id']] = {'id': liability['liability_id'], 'type': 'liability'}
            edges.add((self.user_id, liability['liability_id'], 'reported_liability'))
        return {
            'nodes': sorted(nodes.values(), key=lambda n: n['id']),
            'edges': [{'from': a, 'to': b, 'relationship': c} for a, b, c in sorted(edges)],
        }

    def inspect(self) -> dict:
        snapshot = self.snapshot or {}
        missing = self.cash_requirements()
        cash = sum(a['balance_krw'] for a in snapshot.get('accounts', [])) if snapshot and not missing else None
        assets = sum(a['value_krw'] for a in snapshot.get('assets', []))
        debt = sum(d['principal_krw'] for d in snapshot.get('liabilities', []))
        payable = sum(c.get('opening_payable_krw', 0) for c in snapshot.get('cards', []))
        complete = all(snapshot.get('coverage', {}).get(k, False) for k in ('all_assets_reported', 'all_liabilities_reported'))
        classifications: dict[tuple, dict] = {}
        for t in self.transactions:
            if not t.active or t.confirm_status != 'CONFIRMED':
                continue
            key = (t.merchant_id, t.raw_subcategory, t.envelope, t.fixed_group)
            row = classifications.setdefault(key, {
                'merchant_id': t.merchant_id,
                'subcategory': t.raw_subcategory,
                'envelope': t.envelope,
                'fixed_group': t.fixed_group,
                'confirmed_count': 0,
                'last_date': t.date,
            })
            row['confirmed_count'] += 1
            row['last_date'] = max(row['last_date'], t.date)
        return {
            'twin_id': self.twin_id,
            'user_id': self.user_id,
            'revision': self.revision,
            'as_of': self.as_of,
            'input_digest': self.content_digest,
            'model_version': MODEL_VERSION,
            'mapping_version': MAPPING_VERSION,
            'audit': self.model['audit'],
            'provenance': self.metadata,
            'state': {
                'managed_cash_krw': cash,
                'reported_other_assets_krw': assets if 'assets' in snapshot else None,
                'reported_liabilities_krw': debt if 'liabilities' in snapshot else None,
                'net_worth_krw': cash + assets - debt - payable if complete and cash is not None else None,
                'source': snapshot.get('source'),
                'snapshot_as_of': snapshot.get('as_of'),
                'absolute_cash_ready': not missing,
                'missing': missing,
            },
            'behavior': self.model['behavior'],
            'recurring_rules': self.model['rules'],
            'merchant_classifications': sorted(classifications.values(), key=lambda r: (r['merchant_id'], r['subcategory'])),
            'components': self.model['components'],
            'relationships': self.relationships(),
        }
