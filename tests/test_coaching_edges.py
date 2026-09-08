"""Additional accounting edge cases share the life-first fixtures."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest
from fdt.coaching_contract import validate_review
from fdt.coaching_projection import project
from fdt.simulation import generate_bundle
from fdt.errors import FDTError

# Load the explicit neighboring test support path, independent of whether a
# downstream test runner treats tests/ as a package or imports standalone files.
_spec = importlib.util.spec_from_file_location('coaching_test_support', Path(__file__).with_name('test_coaching.py'))
_support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_support)
fixture_twin, run, request = _support.fixture_twin, _support.run, _support.request


def test_multiple_delayed_receipts_are_resolved_against_original_occurrences():
    twin = fixture_twin()
    bundle = generate_bundle(twin, 7, 20, 42)
    bundle.scheduled['salary'].append((5, np.full(20, 300, dtype=np.int64)))
    a = {'kind': 'income_delay', 'rule_id': 'salary', 'original_date': '2026-09-07', 'new_date': '2026-09-09'}
    b = {'kind': 'income_delay', 'rule_id': 'salary', 'original_date': '2026-09-09', 'new_date': '2026-09-10'}
    first = project(twin, bundle, [a, b])
    reverse = project(twin, bundle, [b, a])
    assert np.array_equal(first.cash, reverse.cash)
    assert np.array_equal(first.cash[:, -1, :], project(twin, bundle, []).cash[:, -1, :])


def test_protected_residual_spending_cannot_be_disguised_as_within_cap():
    twin = fixture_twin()
    twin.model['components'][1]['protected'] = True
    result = run(twin, changes=[{'kind': 'spending_cap', 'envelope': '외식', 'start_date': '2026-09-04',
                                 'end_date': '2026-09-10', 'amount_krw': 1}])
    assert result['next_action']['kind'] == 'resolve_committed_spending'
    assert result['comparison']['effect']['spending_change']['p50_krw'] == 0


def test_earmark_must_be_available_in_its_own_account():
    twin = fixture_twin(balance=1000)
    twin.snapshot['accounts'].append({'account_id': 'B', 'balance_krw': 100000})
    result = run(twin, changes=[{'kind': 'set_aside', 'account_id': 'A', 'amount_krw': 5000}])
    assert result['projection']['cash']['period_account_shortfall']['count'] == 0
    assert result['projection']['cash']['period_earmark_breach']['count'] == 20
    assert result['next_action']['kind'] == 'adjust_earmark_account'


@pytest.mark.parametrize('kind', [[], {}, None, True, 3])
def test_non_string_change_kind_is_a_validation_error(kind):
    with pytest.raises(FDTError):
        validate_review(request(changes=[{'kind': kind}]))
