import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.burst_splitter import split_segment
import mesh_ir.golden_programs as golden
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import DmaKind, MemorySpace
from mesh_ir.ir.kernel_ir import DmaAttrs, KernelOpcode, RecvWaitAttrs
from mesh_ir.scheduled.addressing import derive_descriptor_execution_set
from mesh_ir.scheduled.model import AxiFenceAttrs, FenceScope, RepeatCommandAttrs, ScheduledDependencyKind
from mesh_ir.scheduled.verify import verify_program


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def arch():
    return load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")


def _commands_by_source(program):
    return {item.source_op_id: item for item in program.commands if item.source_op_id}


def _reaches(program, source, target):
    edges = {}
    for item in program.semantics.dependencies:
        if item.kind is ScheduledDependencyKind.STREAM_ORDER:
            continue
        edges.setdefault(item.source_command_id, set()).add(item.target_command_id)
    pending = [source]
    visited = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current not in visited:
            visited.add(current)
            pending.extend(edges.get(current, ()))
    return False


def test_read_error_retains_fault_binding_and_real_downstream_pipeline(arch):
    program = golden.build_dma_error_program(arch)
    commands = _commands_by_source(program)
    groups = {item.descriptor_ids[0]: item for item in program.semantics.descriptor_groups}
    load_descriptor = next(item for item in program.dma_descriptors if item.kind == DmaKind.LOAD)
    store_descriptor = next(item for item in program.dma_descriptors if item.kind == DmaKind.STORE)
    load = program.semantics.kernel_ops[groups[load_descriptor.descriptor_id].kernel_op_id - 1]
    store = program.semantics.kernel_ops[groups[store_descriptor.descriptor_id].kernel_op_id - 1]
    compute = next(item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.GEMM)

    assert verify_program(program, arch).program is program
    assert tuple(item.reference_binding.allocation_offset_bytes for item in program.semantics.binding_slots) == (0x800000, 0x200000)
    assert tuple(item.kind for item in program.dma_descriptors) == (DmaKind.LOAD, DmaKind.STORE)
    assert tuple(item.useful_bytes for item in program.dma_descriptors) == (128, 128)
    assert tuple(item.view_id for item in compute.reads) == (2, 2)
    assert (compute.attrs.tile.m_extent, compute.attrs.tile.n_extent, compute.attrs.tile.k_extent) == (8, 8, 8)
    assert tuple(item.attrs.kind for item in (load, store)) == (DmaKind.LOAD, DmaKind.STORE)
    assert _reaches(program, commands[load.op_id].command_id, commands[compute.op_id].command_id)
    assert _reaches(program, commands[compute.op_id].command_id, commands[store.op_id].command_id)


def test_write_error_is_initialized_and_exactly_two_full_bursts(arch):
    program = golden.build_dma_write_error_program(arch)
    size = 2 * arch.axi_max_burst_beats * arch.axi_data_bytes
    load = next(item for item in program.dma_descriptors if item.kind == DmaKind.LOAD)
    stores = [item for item in program.dma_descriptors if item.kind == DmaKind.STORE]
    traffic = [item for item in program.semantics.intrinsic_traffic.descriptors if item.identity.descriptor_id == stores[0].descriptor_id][0]
    bursts = split_segment(traffic.remote_address, traffic.useful_bytes, arch.axi_data_bytes, traffic.max_burst_beats)

    assert verify_program(program, arch).program is program
    assert len(stores) == 1
    assert stores[0].useful_bytes == stores[0].row_bytes == size
    assert traffic.bursts == 2
    assert tuple(item.beat_base for item in bursts) == (traffic.remote_address, traffic.remote_address + size // 2)
    assert _reaches(program, load.command_id, stores[0].command_id)


def test_fence_watermark_precedes_independent_store(arch):
    program = golden.build_dma_fence_program(arch)
    commands = _commands_by_source(program)
    effects = {item.stable_key: item for item in program.semantics.kernel_ops}
    load = effects["fence:load"]
    fill = effects["fence:fill"]
    vector = effects["fence:vector"]
    store = effects["fence:store"]
    fence = next(item for item in program.commands if item.opcode == A.OPCODE.AXI_FENCE)
    read = commands[load.op_id]
    write = commands[store.op_id]
    end = next(item for item in program.commands if item.opcode == A.OPCODE.REQUEST_END)

    assert verify_program(program, arch).program is program
    assert len(program.semantics.streams) == 2
    assert program.op_attrs[fence.attr_index - 1].payload_dict()["fence_scope"] == A.FENCE_SCOPE.ALL_INSTANCE
    assert read.signal_event not in {item.event_id for item in program.command_waits[fence.wait_begin:fence.wait_begin + fence.wait_count]}
    assert _reaches(program, commands[fill.op_id].command_id, commands[vector.op_id].command_id)
    assert _reaches(program, commands[vector.op_id].command_id, write.command_id)
    assert not _reaches(program, fence.command_id, write.command_id)
    assert not _reaches(program, write.command_id, fence.command_id)
    assert _reaches(program, read.command_id, end.command_id)
    assert _reaches(program, write.command_id, end.command_id)
    assert vector.reads[0].region.shape == (8,)
    assert vector.attrs.cost == vector.attrs.cost.__class__(16, 16, 32, 0, 8, 0)
    control = next(item for item in program.semantics.streams if item.physical_stream_id == 0)
    members = program.commands[control.command_begin:control.command_begin + control.command_count]
    assert [item.command_id for item in members].index(read.command_id) < [item.command_id for item in members].index(fence.command_id)


def test_pin_program_uses_two_unordered_store_readers_of_one_initialized_allocation(arch):
    program = golden.build_dma_pin_program(arch)
    fill = next(item for item in program.dma_descriptors if item.kind == DmaKind.LOCAL_FILL)
    stores = [item for item in program.dma_descriptors if item.kind == DmaKind.STORE]
    commands = {item.command_id: item for item in program.commands}

    assert verify_program(program, arch).program is program
    assert [item.kind for item in program.dma_descriptors] == [DmaKind.LOCAL_FILL, DmaKind.STORE, DmaKind.STORE]
    assert len(stores) == 2
    assert stores[0].src == stores[1].src
    assert stores[0].dst != stores[1].dst
    assert all(_reaches(program, fill.command_id, item.command_id) for item in stores)
    assert not _reaches(program, stores[0].command_id, stores[1].command_id)
    assert not _reaches(program, stores[1].command_id, stores[0].command_id)
    assert commands[stores[0].command_id].stream_id != commands[stores[1].command_id].stream_id


@pytest.fixture(scope="module")
def pin_runtime_case(arch):
    path = ROOT / "configs/example/ai_mesh/run_mesh_dma_garnet.py"
    tree = ast.parse(path.read_text())
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in {"PIN_SEEDS", "PIN_VERIFY"} for target in node.targets):
            selected.append(node)
        if isinstance(node, ast.FunctionDef) and node.name in {"fatal_if", "_dual_fnv", "check_pin_serialize"}:
            selected.append(node)

    def fatal(message, *args):
        raise AssertionError(message % args if args else message)

    namespace = {
        "HBM_BASE": 0x800000000,
        "A": A,
        "fatal": fatal,
    }
    exec(compile(ast.Module(selected, type_ignores=[]), path, "exec"), namespace)
    schedule = golden.build_dma_pin_program(arch).canonical_dict()
    stores = [item for item in schedule["sections"]["DMA_DESCRIPTORS"] if item["kind"] == int(A.DMA_KIND.STORE)]
    want = namespace["_dual_fnv"](bytes([0x22]) * 128)

    def result(second_issue=20, digests=(want, want)):
        first, second = stores
        return {
            "cores": [{
                "command_issue_ticks": {str(first["command_id"]): 10, str(second["command_id"]): second_issue},
                "command_done_ticks": {str(first["command_id"]): 20, str(second["command_id"]): 30},
            }],
            "memory_endpoint": {"verifies": [{"digest": item} for item in digests]},
        }

    return namespace, SimpleNamespace(schedule=schedule), result


def test_pin_runtime_case_observes_store_admission_and_both_outputs(pin_runtime_case):
    namespace, ctx, result = pin_runtime_case

    assert namespace["PIN_SEEDS"] == []
    assert namespace["PIN_VERIFY"] == [(0x800200000, 128), (0x800200100, 128)]
    namespace["check_pin_serialize"](ctx, result(), {}, [])
    with pytest.raises(AssertionError, match="issued before"):
        namespace["check_pin_serialize"](ctx, result(second_issue=19), {}, [])


@pytest.mark.parametrize("bad_index", (0, 1))
def test_pin_runtime_case_rejects_either_incorrect_output(pin_runtime_case, bad_index):
    namespace, ctx, result = pin_runtime_case
    want = namespace["_dual_fnv"](bytes([0x22]) * 128)
    digests = [want, want]
    digests[bad_index] = namespace["_dual_fnv"](bytes([0x23]) * 128)

    with pytest.raises(AssertionError, match="initialized payload"):
        namespace["check_pin_serialize"](ctx, result(digests=digests), {}, [])


def test_shape_matrix_preserves_all_geometry_and_cross_core_completion(arch):
    program = golden.build_dma_shapes_program(arch)
    by_kind = {item.kind: item for item in program.dma_descriptors}
    p2p = by_kind[DmaKind.P2P_PUSH]
    store = by_kind[DmaKind.STORE]
    recv = next(item for item in program.semantics.kernel_ops if type(item.attrs) is RecvWaitAttrs)
    p2p_op = next(item for item in program.semantics.kernel_ops if type(item.attrs) is DmaAttrs and item.attrs.kind is DmaKind.P2P_PUSH)
    reduce = next(item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.LOCAL_REDUCE)
    effects = {item.stable_key: item for item in program.semantics.kernel_ops}
    commands = _commands_by_source(program)

    assert verify_program(program, arch).program is program
    assert set(by_kind) == {DmaKind.PREFETCH, DmaKind.LOCAL_FILL, DmaKind.P2P_PUSH, DmaKind.STORE}
    assert (p2p.rows, p2p.row_bytes, p2p.useful_bytes, p2p.src_stride_bytes, p2p.dst_stride_bytes) == (2, 64, 128, 64, 128)
    assert (store.rows, store.row_bytes, store.useful_bytes, store.src_stride_bytes, store.dst_stride_bytes) == (2, 64, 128, 64, 256)
    assert p2p.physical_storage_bytes == 192
    assert store.physical_storage_bytes == 320
    assert recv.attrs.transfer_id == p2p_op.attrs.transfer_id
    assert p2p_op.done_token in recv.after_tokens
    assert _reaches(program, commands[effects["shapes:fill:accumulator"].op_id].command_id, commands[reduce.op_id].command_id)
    assert _reaches(program, p2p.command_id, commands[reduce.op_id].command_id)
    assert _reaches(program, commands[reduce.op_id].command_id, store.command_id)


def test_fence_scopes_have_matching_real_transaction_families(arch):
    program = golden.build_fence_scopes_program(arch)
    fence_commands = {
        item.source.attrs.scope: item.command_id
        for item in program.semantics.command_semantics
        if hasattr(item.source, "attrs") and type(item.source.attrs) is AxiFenceAttrs
    }
    descriptors = program.dma_descriptors
    commands = _commands_by_source(program)
    effects = {item.stable_key: item for item in program.semantics.kernel_ops}
    control = next(item for item in program.semantics.streams if item.core_id == 0)
    members = program.commands[control.command_begin:control.command_begin + control.command_count]
    position = {item.command_id: index for index, item in enumerate(members)}

    assert verify_program(program, arch).program is program
    assert set(fence_commands) == set(FenceScope)
    assert {item.kind for item in descriptors} >= {DmaKind.LOAD, DmaKind.STORE, DmaKind.P2P_PUSH}
    assert any(item.kind == DmaKind.STORE and item.dst.memory_space == MemorySpace.HOST_SHARED for item in descriptors)
    preceding = {
        FenceScope.DMA_READ: "scopes:load",
        FenceScope.DMA_WRITE: "scopes:hbm-store",
        FenceScope.P2P: "scopes:p2p",
        FenceScope.HOST_SHARED_WRITE: "scopes:shared-store",
    }
    assert all(position[commands[effects[key].op_id].command_id] < position[fence_commands[scope]] for scope, key in preceding.items())


@pytest.mark.parametrize(
    ("factory", "first_offset"),
    ((golden.build_cross_error_program, 0x800000), (golden.build_cross_fault_program, 0x100080)),
)
def test_cross_fault_cases_use_distinct_inputs_and_real_cross_core_consumer(arch, factory, first_offset):
    program = factory(arch)
    loads = [item for item in program.dma_descriptors if item.kind == DmaKind.LOAD]
    compute = next(item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.GEMM)
    consumer = next(item for item in program.semantics.kernel_ops if item.owner_core == 1 and item.opcode not in (KernelOpcode.ALLOC, KernelOpcode.VIEW))
    commands = _commands_by_source(program)

    assert verify_program(program, arch).program is program
    assert tuple(item.reference_binding.allocation_offset_bytes for item in program.semantics.binding_slots) == (first_offset, 0x100000)
    assert len({item.dst for item in loads}) == 2
    assert all(_reaches(program, item.command_id, commands[compute.op_id].command_id) for item in loads)
    assert _reaches(program, loads[0].command_id, commands[consumer.op_id].command_id)


def test_repeat_error_replays_initialized_load_store_body_three_times(arch):
    program = golden.build_repeat_error_program(arch)
    repeat_command = next(item for item in program.commands if item.opcode == A.OPCODE.REPEAT)
    repeat = next(item.source.attrs for item in program.semantics.command_semantics if hasattr(item.source, "attrs") and type(item.source.attrs) is RepeatCommandAttrs)
    end = next(item for item in program.commands if item.opcode == A.OPCODE.REQUEST_END)
    reports = program.semantics.intrinsic_traffic.descriptors

    assert verify_program(program, arch).program is program
    assert repeat.subrange_command_count == 2
    assert repeat.repeat_count == 3
    assert tuple(item.execution_count for item in reports) == (3, 3)
    assert _reaches(program, repeat_command.command_id, end.command_id)
    executions, _ = derive_descriptor_execution_set(program, arch)
    assert tuple(item.descriptor.identity.direction.name for item in executions) == ("READ", "WRITE")
