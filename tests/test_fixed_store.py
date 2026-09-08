"""Classification-only updates through the event reducer (contract 4)."""
import copy
import pytest
from fdt import FDTError
from fdt.store import apply_events


def test_classification_only_update_is_accepted_without_dirty_snapshot(tiny, row):
    pending = dict(row, source='LIVE', transaction_id='LIVE1', transaction_date='2026-09-06', confirm_status='PENDING')
    first = apply_events(tiny, [{'event_id': 'e1', 'user_id': 'U', 'type': 'transaction', 'transaction': pending}])
    assert [t for t in first.transactions if t.id == 'LIVE1'][0].pending
    fresh = copy.deepcopy(first.snapshot); fresh['source'] = 'LIVE'
    ready = apply_events(first, [{'event_id': 's1', 'user_id': 'U', 'type': 'snapshot', 'snapshot': fresh}])
    assert not ready.cash_requirements()
    confirmed = dict(pending, confirm_status='CONFIRMED')
    second = apply_events(ready, [{'event_id': 'e2', 'user_id': 'U', 'type': 'transaction', 'transaction': confirmed}])
    live = [t for t in second.transactions if t.id == 'LIVE1'][0]
    assert not live.pending and live.envelope == '외식' and second.metadata['reclassified_count'] == 1
    assert not second.cash_requirements()
    changed = dict(confirmed, amount_krw='999')
    with pytest.raises(FDTError) as e:
        apply_events(second, [{'event_id': 'e3', 'user_id': 'U', 'type': 'transaction', 'transaction': changed}])
    assert e.value.code == 'TRANSACTION_CONFLICT'
