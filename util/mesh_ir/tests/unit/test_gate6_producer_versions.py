import hashlib

import pytest

from mesh_ir.workload_producer import _row_is_produced


def run(address, data, tick, command):
    return (address, len(data), hashlib.sha256(data).hexdigest(), data, tick, command)


@pytest.mark.parametrize('payload,accepted', [(b'abcdefgh', False), (b'abXYefgh', True)])
def test_partial_overwrite_changes_the_version_inside_a_longer_run(payload, accepted):
    row = {'address': 0, 'size': 8, 'bytes_hex': payload.hex(),
           'digest': hashlib.sha256(payload).hexdigest()}
    pool = [run(0, b'abcdefgh', 1, 10), run(2, b'XY', 2, 20)]
    assert (_row_is_produced(row, pool, 3) is not None) is accepted


def test_missing_middle_coverage_fails_closed():
    row = {'address': 0, 'size': 6, 'bytes_hex': b'abcdef'.hex(),
           'digest': hashlib.sha256(b'abcdef').hexdigest()}
    assert _row_is_produced(row, [run(0, b'ab', 1, 10), run(4, b'ef', 2, 20)], 3) is None


def test_whole_run_digest_cannot_prove_a_subrange():
    digest = hashlib.sha256(b'abcdefgh').hexdigest()
    row = {'address': 2, 'size': 2, 'bytes_hex': None, 'digest': digest}
    assert _row_is_produced(row, [(0, 8, digest, None, 1, 10)], 2) is None


def test_equal_timestamp_conflicting_writers_are_not_arbitrarily_selected():
    row = {'address': 0, 'size': 2, 'bytes_hex': b'ab'.hex(),
           'digest': hashlib.sha256(b'ab').hexdigest()}
    assert _row_is_produced(row, [run(0, b'ab', 1, 10), run(0, b'XY', 1, 20)], 2) is None


def test_exact_digest_evidence_and_future_writes():
    digest = hashlib.sha256(b'ab').hexdigest()
    row = {'address': 0, 'size': 2, 'bytes_hex': None, 'digest': digest}
    assert _row_is_produced(row, [(0, 2, digest, None, 1, 10), run(0, b'XY', 3, 20)], 2) is not None
