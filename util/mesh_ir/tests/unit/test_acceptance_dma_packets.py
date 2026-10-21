import pytest

from mesh_ir.acceptance import ContractError, build_dma_traffic
from mesh_ir.runtime_dma_timing import DmaExecutionIdentity


@pytest.mark.parametrize("kind,field,channel", [(1, "read_discarded_bytes", "AR"),
    (2, "write_drained_uncommitted_bytes", "AW"),
    (3, "write_drained_uncommitted_bytes", "AW")])
def test_drained_transfer_counts_issued_bursts(kind, field, channel):
    expected = [{"descriptor_id": 1, "kind": kind, "useful_bytes": 128, "bursts": 1}]
    actual = [{"descriptor_id": 1, field: 128}]
    identity = DmaExecutionIdentity(1, 0, 0, 0, 0, 1, 2, 0)
    traffic = build_dma_traffic("DC-30", "drained", expected, actual, 1,
        dma_bursts={identity: [{"channel": channel}]})
    assert traffic["status"] == "PASS"
    assert traffic["classes"][0]["actual_packets"] == 1
    with pytest.raises(ContractError):
        build_dma_traffic("DC-30", "drained", expected, actual, 1)


def test_missing_issued_burst_is_a_traffic_failure():
    expected = [{"descriptor_id": 1, "kind": 1, "useful_bytes": 128, "bursts": 1}]
    actual = [{"descriptor_id": 1, "read_bytes": 128, "read_bursts": 1}]
    traffic = build_dma_traffic("DC-30", "missing", expected, actual, 1, dma_bursts={})
    assert traffic["status"] == "FAIL"
