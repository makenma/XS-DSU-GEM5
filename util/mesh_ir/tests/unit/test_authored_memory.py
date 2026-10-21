from dataclasses import replace

import pytest

from mesh_ir.builder import ProgramBuilder
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import DType, MemorySpace, TensorRole
from mesh_ir.ir.kernel_ir import ElementRegion
from mesh_ir.scheduled.model import AuthoredProgramOrigin, HaltAttrs, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.scheduled.verify import verify_program
from tests.unit.test_gate2_authored_facade import _arch


def program_of(memory, arch):
    builder = ProgramBuilder(arch, AuthoredProgramOrigin('abi2', 'authored-memory', 1))
    variant = builder.variant('forward', 'main', 'main', records=memory.records(),
        allocations=memory.allocations, external_backings=memory.external_backings, binding_slots=memory.binding_slots)
    stream = variant.stream(memory.core_id, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    for operation in memory.effects:
        stream.kernel_command(operation.op_id)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def test_authored_memory_builds_admitted_load_compute_store():
    from mesh_ir.authoring import AuthoredMemory

    arch = _arch()
    memory = AuthoredMemory(arch, 3)
    source = memory.buffer('input', TensorRole.INPUT, DType.FP16, (2, 2), MemorySpace.HBM, 0, capacity_bytes=64)
    weight = memory.buffer('weight', TensorRole.WEIGHT, DType.FP16, (2, 2), MemorySpace.HBM, 64, content_sha256='ab' * 32, capacity_bytes=64)
    target = memory.buffer('output', TensorRole.OUTPUT, DType.FP16, (2, 2), MemorySpace.HBM, 128, capacity_bytes=64)
    left = memory.stage(source, 'left', 0)
    right = memory.stage(weight, 'right', 64)
    result = memory.stage(target, 'result', 128)
    memory.copy('load-input', source, left)
    memory.copy('load-weight', weight, right)
    memory.matmul('compute', left, right, result)
    memory.copy('store-output', result, target)
    program = program_of(memory, arch)
    assert verify_program(program, arch).program is program
    assert [item.kind for item in program.dma_descriptors] == [A.DMA_KIND.LOAD, A.DMA_KIND.LOAD, A.DMA_KIND.STORE]
    assert program.semantics.computations[0].operand_tensor_ids == (left.tensor_id, right.tensor_id)
    assert program.semantics.kernel_tensors[weight.tensor_id - 1].content_sha256 == 'ab' * 32
    assert program_of(memory, arch) == program


def test_authored_memory_preserves_read_only_buffer_content():
    from mesh_ir.authoring import AuthoredMemory
    from mesh_ir.ir.common import Access
    arch = _arch()
    memory = AuthoredMemory(arch, 3)
    source = memory.buffer('buffer', TensorRole.STATE, DType.FP16, (2, 2), MemorySpace.HBM, 0,
        access=Access.READ_ONLY, content_sha256='ab' * 32, capacity_bytes=64)
    local = memory.stage(source, 'staging', 0)
    memory.copy('load-buffer', source, local)
    program = program_of(memory, arch)
    verify_program(program, arch)
    tensor, = program.semantics.kernel_tensors
    assert tensor.role is TensorRole.STATE
    assert tensor.access is Access.READ_ONLY
    assert tensor.content_sha256 == 'ab' * 32


def test_authored_memory_preserves_partial_kv_initialization():
    from mesh_ir.authoring import AuthoredMemory

    arch = _arch()
    memory = AuthoredMemory(arch, 3)
    external = memory.buffer('kv', TensorRole.KV_CACHE, DType.INT8, (32,), MemorySpace.HBM, 0, capacity_bytes=64)
    local = memory.stage(external, 'kv-stage', 0)
    prefix = ElementRegion((0,), (16,), (1,))
    tail = ElementRegion((16,), (16,), (1,))
    memory.copy('read-prefix', external, local, source_region=prefix, destination_region=prefix)
    memory.fill('append', local, b'\x5a' * 8, region=tail)
    memory.copy('write-append', local, external, source_region=tail, destination_region=tail)
    program = program_of(memory, arch)
    assert verify_program(program, arch).program is program
    assert [item.useful_bytes for item in program.dma_descriptors] == [16, 16, 16]
    assert program.dma_descriptors[-1].dst.offset_bytes == 16


def test_authored_memory_does_not_invent_initial_residency():
    from mesh_ir.authoring import AuthoredMemory

    arch = _arch()
    memory = AuthoredMemory(arch, 3)
    external = memory.buffer('output', TensorRole.OUTPUT, DType.INT8, (16,), MemorySpace.HBM, 0, capacity_bytes=64)
    local = memory.stage(external, 'empty', 0)
    memory.copy('invalid-store', local, external)
    with pytest.raises(MeshIrError, match="E_TENSOR_NOT_RESIDENT"):
        program_of(memory, arch)


def test_authored_memory_rejects_foreign_and_forged_handles():
    from mesh_ir.authoring import AuthoredMemory

    arch = _arch()
    left, right = AuthoredMemory(arch, 3), AuthoredMemory(arch, 3)
    owned = left.buffer('owned', TensorRole.ACTIVATION, DType.INT8, (16,), MemorySpace.CORE_SRAM, 0)
    foreign = right.buffer('foreign', TensorRole.ACTIVATION, DType.INT8, (16,), MemorySpace.CORE_SRAM, 0)
    for handle in (foreign, replace(owned, object_id=999)):
        with pytest.raises(MeshIrError):
            left.fill('invalid', handle, bytes(8))


def test_authored_memory_invalid_declaration_leaves_no_records():
    from mesh_ir.authoring import AuthoredMemory

    arch = _arch()
    memory = AuthoredMemory(arch, 3)
    before = memory.records()
    with pytest.raises(MeshIrError):
        memory.buffer('bad', TensorRole.INPUT, DType.INT8, (64,), MemorySpace.HBM, 0, capacity_bytes=32)
    assert memory.records() == before
    assert memory.binding_slots == ()


def test_authored_regions_share_one_completion_and_keep_publish_chunks():
    from mesh_ir.authoring import AuthoredMemory

    arch = _arch()
    memory = AuthoredMemory(arch, 3)
    external = memory.buffer('output', TensorRole.OUTPUT, DType.INT8, (128,), MemorySpace.HBM, 0)
    local = memory.stage(external, 'publish', 0)
    memory.fill('fill', local, b'\x5a' * 8)
    regions = tuple(ElementRegion((start,), (64,), (1,)) for start in (0, 64))
    memory.copy_regions('publish', local, external, tuple((region, region) for region in regions))
    program = program_of(memory, arch)
    descriptors = [item for item in program.dma_descriptors if item.kind == A.DMA_KIND.STORE]
    assert len(descriptors) == 2
    assert {item.row_bytes for item in descriptors} == {64}
    assert len({item.command_id for item in descriptors}) == 1
    assert len({item.completion_event for item in descriptors}) == 1


def test_authored_duplicate_compute_keeps_the_existing_state():
    from mesh_ir.authoring import AuthoredMemory

    arch = _arch()
    memory = AuthoredMemory(arch, 3)
    source = memory.buffer('input', TensorRole.INPUT, DType.FP16, (2, 2), MemorySpace.HBM, 0, capacity_bytes=64)
    target = memory.buffer('output', TensorRole.OUTPUT, DType.FP16, (2, 2), MemorySpace.HBM, 64, capacity_bytes=64)
    left = memory.stage(source, 'left', 0)
    right = memory.stage(source, 'right', 64)
    output = memory.stage(target, 'output-stage', 128)
    memory.copy('load-left', source, left)
    memory.copy('load-right', source, right)
    memory.matmul('compute', left, right, output)
    before = memory.records()
    with pytest.raises(MeshIrError, match='E_ABI_DUPLICATE'):
        memory.matmul('compute', left, right, output)
    assert memory.records() == before


def test_authored_memory_owns_cross_core_copy_and_sum():
    from mesh_ir.authoring import AuthoredMemory

    arch = _arch()
    memory = AuthoredMemory(arch, 3)
    source = memory.buffer('source', TensorRole.INPUT, DType.FP16, (2, 2), MemorySpace.HBM, 0, capacity_bytes=64)
    target = memory.buffer('output', TensorRole.OUTPUT, DType.FP16, (2, 2), MemorySpace.HBM, 64, capacity_bytes=64)
    left = memory.stage(source, 'left', 0, core_id=3)
    remote = memory.stage(source, 'remote', 0, core_id=7)
    peer = memory.stage(source, 'peer', 64, core_id=3)
    total = memory.stage(target, 'sum', 128, core_id=3)
    memory.copy('load-left', source, left)
    memory.copy('load-remote', source, remote)
    memory.copy('peer', remote, peer)
    memory.add('sum', left, peer, total)
    memory.copy('store', total, target)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin('abi2', 'distributed-memory', 1))
    variant = builder.variant('forward', 'main', 'main', records=memory.records(),
        allocations=memory.allocations, external_backings=memory.external_backings, binding_slots=memory.binding_slots)
    for core in (3, 7):
        stream = variant.stream(core, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL | (A.STREAM_FLAGS.IS_LIFECYCLE if core == 3 else 0))
        if core == 3:
            stream.control_command(RequestBeginAttrs())
        for operation in memory.effects:
            if operation.owner_core == core:
                stream.kernel_command(operation.op_id)
        if core == 3:
            stream.control_command(RequestEndAttrs())
        stream.control_command(HaltAttrs())
    program = builder.build()
    assert verify_program(program, arch).program is program
    push, = (item for item in program.dma_descriptors if item.kind == A.DMA_KIND.P2P_PUSH)
    assert (push.src.owner_core, push.dst.owner_core, push.useful_bytes) == (7, 3, 8)
    receives = [item for item in program.commands if item.opcode == A.OPCODE.RECV_WAIT]
    assert len(receives) == 1 and receives[0].core_id == 3
    assert {item.owner_core for item in program.allocations} == {3, 7}


def test_authored_subview_keeps_one_external_weight_binding():
    from mesh_ir.authoring import AuthoredMemory

    arch = _arch()
    memory = AuthoredMemory(arch, 3)
    weight = memory.buffer('weight', TensorRole.WEIGHT, DType.FP16, (4, 2), MemorySpace.HBM, 0, content_sha256='ab' * 32, capacity_bytes=64)
    selected = memory.view(weight, 'second-half', 4, (2, 2))
    local = memory.stage(selected, 'matrix', 0)
    target = memory.buffer('output', TensorRole.OUTPUT, DType.FP16, (2, 2), MemorySpace.HBM, 64, capacity_bytes=64)
    output = memory.stage(target, 'result', 64)
    memory.copy('load', selected, local)
    memory.matmul('matmul', local, local, output)
    memory.copy('store', output, target)
    program = program_of(memory, arch)
    assert verify_program(program, arch).program is program
    load, = (row for row in program.dma_descriptors if row.kind == A.DMA_KIND.LOAD)
    assert (load.src.offset_bytes, load.useful_bytes) == (8, 8)
    assert len(memory.binding_slots) == 2
    assert len({row.object_id for row in memory.external_backings}) == 2


@pytest.mark.parametrize('beats', [1, 2])
def test_authored_copy_burst_limit_is_part_of_the_admitted_operation(beats):
    from mesh_ir.authoring import AuthoredMemory

    arch = _arch()
    memory = AuthoredMemory(arch, 3)
    external = memory.buffer('output', TensorRole.OUTPUT, DType.INT8, (128,), MemorySpace.HBM, 0)
    local = memory.stage(external, 'stage', 0)
    memory.fill('fill', local, bytes(8))
    operation = memory.copy('store', local, external, max_burst_beats=beats)
    program = program_of(memory, arch)
    assert operation.attrs.max_burst_beats == beats
    stores = [row for row in program.dma_descriptors if row.kind == A.DMA_KIND.STORE]
    assert stores and all(row.max_burst_beats == beats for row in stores)
    verify_program(program, arch)
