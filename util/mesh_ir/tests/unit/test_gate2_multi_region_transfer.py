import dataclasses
import itertools
import math

import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, DType, DmaKind, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.kernel_ir import BufferObject, ControlToken, DistributionKind, DmaAttrs, ElementRegion, KernelOp, KernelOpcode, KernelTensor, OperandAccess, OperandAccessMode, Placement, RecvWaitAttrs, StateOrigin, StateTransition, TensorShard, TensorState
from mesh_ir.analysis.regions import flat_interval_regions
from tests.unit.test_gate2_kernel_ir import create_kernel, declarations, refreshed


def _flattened_offsets(shape, regions):
    offsets = []
    for region in regions:
        for coordinates in itertools.product(*(range(extent) for extent in region.shape)):
            logical = tuple(origin + coordinate * step for origin, coordinate, step in zip(region.origin, coordinates, region.steps))
            offset = 0
            for axis, coordinate in enumerate(logical):
                suffix = 1
                for extent in shape[axis + 1 :]:
                    suffix *= extent
                offset += coordinate * suffix
            offsets.append(offset)
    return tuple(offsets)


@pytest.mark.parametrize(
    ("shape", "begin", "count"),
    (
        ((3, 5), 0, 10),
        ((3, 5), 0, 15),
        ((3, 5), 4, 4),
        ((2, 3, 5), 5, 25),
    ),
)
def test_flat_interval_regions_preserve_aligned_ends_and_ranked_coverage(shape, begin, count):
    regions = flat_interval_regions(shape, begin, count)
    assert _flattened_offsets(shape, regions) == tuple(range(begin, begin + count))
    assert sum(math.prod(region.shape) for region in regions) == count


def _cross_row_transfer():
    tensor = KernelTensor(1, 0, None, 1, 0, "partial", TensorRole.WEIGHT, DType.FP32, (3, 5), (5, 1), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 60, 60, "a" * 64)
    shards = (
        TensorShard(1, 1, 1, 3, DistributionKind.REPLICATED, (0, 0), (3, 5), (3, 5)),
        TensorShard(2, 1, 1, 7, DistributionKind.REPLICATED, (0, 0), (3, 5), (3, 5)),
    )
    objects = (
        BufferObject(1, 1, 3, MemorySpace.CORE_SRAM, (3, 5), (5, 1), 60, 4, False, 0),
        BufferObject(2, 1, 7, MemorySpace.CORE_SRAM, (3, 8), (8, 1), 96, 4, False, 0),
    )
    views, declarations_ = declarations(objects, (1, 2))
    views = (
        dataclasses.replace(views[0], layout=None),
        dataclasses.replace(views[1], padded_shape=(3, 5), valid_shape=(3, 5), object_strides=(8, 1), layout=None),
    )
    first = ElementRegion((0, 4), (1, 1), (1, 1))
    second = ElementRegion((1, 0), (1, 3), (1, 1))
    reads = (
        OperandAccess(1, 1, first, OperandAccessMode.READ),
        OperandAccess(1, 1, second, OperandAccessMode.READ),
    )
    writes = (
        StateTransition(2, 3, 2, first),
        StateTransition(2, 3, 2, second),
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.PRE_RESIDENT),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
    )
    send = KernelOp(5, 0, "p2p:41", KernelOpcode.DMA, 3, 0, reads, writes, DmaAttrs(DmaKind.P2P_PUSH, 3, 3, 7, 41, b""), (), 1)
    receive = KernelOp(6, 0, "recv:41", KernelOpcode.RECV_WAIT, 7, 0, (), (), RecvWaitAttrs(41, 3, 7, 16), (1,), 2)
    return create_kernel("a" * 64, "b" * 64, "forward", "cross-row", (tensor,), (), (Placement(1, (3, 7)),), shards, (), objects, views, states, (ControlToken(1), ControlToken(2)), (*declarations_, send, receive))


def test_cross_row_p2p_is_one_transfer_and_one_grouped_destination_generation():
    kernel = _cross_row_transfer()
    kernel.verify()
    send = kernel.ops[-2]
    assert len(send.reads) == len(send.writes) == 2
    assert {item.old_state_id for item in send.writes} == {2}
    assert {item.new_state_id for item in send.writes} == {3}
    assert kernel.ops[-1].attrs.expected_bytes == 16


@pytest.mark.parametrize("mutation", ("unequal", "overlap", "reordered", "wrong_bytes"))
def test_cross_row_p2p_rejects_inexact_piece_mapping(mutation):
    kernel = _cross_row_transfer()
    send, receive = kernel.ops[-2:]
    if mutation == "unequal":
        send = dataclasses.replace(send, writes=send.writes[:1])
    elif mutation == "overlap":
        send = dataclasses.replace(send, reads=(send.reads[0], send.reads[0]), writes=(send.writes[0], send.writes[0]))
    elif mutation == "reordered":
        send = dataclasses.replace(send, writes=tuple(reversed(send.writes)))
    else:
        receive = dataclasses.replace(receive, attrs=dataclasses.replace(receive.attrs, expected_bytes=12))
    changed = refreshed(dataclasses.replace(kernel, ops=(*kernel.ops[:-2], send, receive)))
    with pytest.raises(MeshIrError):
        changed.verify()
