from dataclasses import dataclass, replace
import hashlib

from mesh_ir.authoring import AuthoredMemory
from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_single_core_program
from mesh_ir.ir.common import DType, MemorySpace, TensorRole
from mesh_ir.model import ContentDigest, MoeDynamicRegion, MoeExpertSpec, MoeKernelSpec, MoeLayerSpec
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.scheduled.model import AuthoredProgramOrigin, HaltAttrs, RequestBeginAttrs, RequestEndAttrs

WEIGHT_SEED = b"AI_MESH_GATE5_SYNTHETIC_WEIGHT_V1"
TENSOR_BYTES = 8192
HIDDEN = 32
INTERMEDIATE = 64
QUAD_CORES = 16
QUAD_PAIRS = QUAD_CORES // 2


def synthetic_weight_digest(size=TENSOR_BYTES) -> bytes:
    payload = WEIGHT_SEED * (size // len(WEIGHT_SEED) + 1)
    return hashlib.sha256(payload[:size]).digest()


def quad_reducer(core_id: int) -> int:
    return (core_id % QUAD_PAIRS) * 2


def quad_sender(core_id: int) -> int:
    return quad_reducer(core_id) + 1


@dataclass(frozen=True)
class MoeLayerLayout:
    hidden: int
    expert_cores: tuple[int, ...]
    gates: tuple[tuple[int, int, int], ...]
    scratch: tuple[int, int]
    max_m: int = 32
    top_k: int = 2
    capacity_q16: int = 0x10000
    max_tokens: int = 8
    max_requests: int = 2


def _with_layers(program, layouts):
    variant, = program.semantics.variants
    source = ProgramBindingCatalog(program).binding(variant.entrypoint_id, variant.profile_id, 'weight')
    weight = next(row for row in program.tensors if row.tensor_id == source.tensor.tensor_id)
    symbol = next(index for index, row in enumerate(program.strings, 1) if row.value == 'weight')
    commands = {row.command_id: row for row in program.commands}
    layers, experts, regions, kernels = [], [], [], []
    for index, layout in enumerate(layouts):
        layer_id = index + 1
        count = len(layout.gates)
        tokens = layout.max_tokens
        transfers = 8 if count <= 2 else count * 8
        expert_bytes = layout.hidden * INTERMEDIATE * 2
        layers.append(MoeLayerSpec(
            layer_id=layer_id, kernel_spec_index=index, expert_first=len(experts), expert_count=len(layout.expert_cores),
            top_k=layout.top_k, token_bytes=layout.hidden * 2, output_token_bytes=INTERMEDIATE * 2,
            capacity_factor_q16=layout.capacity_q16, overflow_policy=A.MOE_OVERFLOW_POLICY.DROP,
            transport_mode=A.MOE_TRANSPORT_MODE.VARIABLE_ALL_TO_ALL_V, dynamic_region_first=len(regions), dynamic_region_count=count,
            max_tokens_per_frozen_batch=tokens, max_requests_per_batch=layout.max_requests,
            max_routes=tokens * 2, max_materialized_commands=count * 32,
            max_materialized_descriptors=count * 32, max_materialized_transfers=transfers,
            max_dynamic_allocations=count * 8, flags=0, max_materialized_events=count * 16 + 1))
        for expert_id, core in enumerate(layout.expert_cores):
            experts.append(MoeExpertSpec(layer_id=layer_id, expert_id=expert_id, flags=0, core_id=core,
                reserved_core=0, weight_symbol_id=symbol, weight_region_offset=expert_id * expert_bytes,
                weight_bytes=expert_bytes, weight_digest_index=0))
        for ordinal, (core, insert, resume) in enumerate(layout.gates):
            regions.append(MoeDynamicRegion(region_id=ordinal + 1, layer_id=layer_id, core_id=core, stream_id=0,
                insert_after_command_id=insert, resume_before_command_id=resume, entry_event_id=commands[insert].signal_event,
                scratch_offset=layout.scratch[0], scratch_bytes=layout.scratch[1], scratch_alignment=64,
                max_overlay_commands=32, max_overlay_events=16, max_overlay_descriptors=32,
                max_overlay_transfers=8, max_overlay_allocations=8, flags=0))
        kernels.append(MoeKernelSpec(layer_id=layer_id, expert_opcode=A.OPCODE.GEMM,
            input_dtype=A.DTYPE.FP16, accum_dtype=A.DTYPE.FP32, output_dtype=A.DTYPE.FP16,
            batch=1, n=INTERMEDIATE, k=layout.hidden, transpose_flags=0,
            combine_kind=A.MOE_COMBINE_KIND.LOCAL_REDUCE, algorithm_id=layer_id,
            efficiency_q16=0x10000, tensor_setup_cycles=64, tensor_flush_cycles=32,
            input_token_bytes=layout.hidden * 2, output_token_bytes=INTERMEDIATE * 2, reserved0=0,
            weight_operand_bytes=expert_bytes, expert_result_alignment=64, max_m=layout.max_m,
            combine_setup_cycles=32, combine_flush_cycles=16, flags=0))
    program = replace(program, required_features=program.required_features | A.DYNAMIC_MOE_V1,
        content_digests=(ContentDigest(A.CONTENT_DIGEST_OBJECT_KIND['TENSOR'], 0, weight.tensor_id, weight.content_sha256),),
        moe_layer_specs=tuple(layers), moe_expert_specs=tuple(experts), moe_dynamic_regions=tuple(regions), moe_kernel_specs=tuple(kernels))
    return replace(program, semantic_sha256=program.computed_semantic_sha256)


def _scheduled_memory(memory, arch, name, cores):
    builder = ProgramBuilder(arch, AuthoredProgramOrigin('moe', name, 2))
    variant = builder.variant('main', name, name, records=memory.records(), allocations=memory.allocations,
        external_backings=memory.external_backings, binding_slots=memory.binding_slots)
    for core in cores:
        stream = variant.stream(core, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL | (A.STREAM_FLAGS.IS_LIFECYCLE if core == cores[0] else 0))
        if core == cores[0]:
            stream.control_command(RequestBeginAttrs())
        for operation in memory.effects:
            if operation.owner_core == core:
                stream.kernel_command(operation.op_id)
        if core == cores[0]:
            stream.control_command(RequestEndAttrs())
        stream.control_command(HaltAttrs())
    return builder.build()


def _gates(program):
    commands = {row.command_id: row for row in program.commands}
    gates = []
    for stream in program.semantics.streams:
        ids = program.semantics.stream_command_ids[stream.command_begin:stream.command_begin + stream.command_count]
        for insert, resume in zip(ids, ids[1:]):
            if commands[insert].opcode == A.OPCODE.GEMM:
                gates.append((stream.core_id, insert, resume))
    return tuple(gates)


def moe_min_program(arch):
    program = build_single_core_program(arch, weight_content_sha256=synthetic_weight_digest().hex())
    return _with_layers(program, (MoeLayerLayout(HIDDEN, (0, 0), _gates(program), (0x80000, 0x40000), max_m=8),))


def _paired_program(arch, core_count, capacity_factor_q16=0x10000, top_k=2):
    cores = tuple(range(core_count))
    memory = AuthoredMemory(arch, cores[0])
    weight_size = core_count * HIDDEN * INTERMEDIATE * 2
    source = memory.buffer('token', TensorRole.INPUT, DType.FP16, (1 if core_count == 2 else core_count, HIDDEN), MemorySpace.HBM, 0x100000)
    weight = memory.buffer('weight', TensorRole.WEIGHT, DType.FP16, (core_count * HIDDEN, INTERMEDIATE),
        MemorySpace.HBM, 0, content_sha256=synthetic_weight_digest(weight_size).hex())
    target = memory.buffer('output', TensorRole.OUTPUT, DType.FP16, (core_count // 2, INTERMEDIATE), MemorySpace.HBM, 0x200000)
    inputs, weights, partials, outputs, peers = [], [], [], [], []
    for core in cores:
        input_view = memory.view(source, f'token:{core}', 0 if core_count == 2 else core * HIDDEN, (1, HIDDEN), core_id=core)
        weight_view = memory.view(weight, f'weight:{core}', core * HIDDEN * INTERMEDIATE, (HIDDEN, INTERMEDIATE), core_id=core)
        inputs.append((input_view, memory.stage(input_view, f'input-stage:{core}', 0, core_id=core)))
        weights.append((weight_view, memory.stage(weight_view, f'weight-stage:{core}', 0x2000, core_id=core)))
        partials.append(memory.buffer(f'partial:{core}', TensorRole.ACTIVATION, DType.FP16,
            (1, INTERMEDIATE), MemorySpace.CORE_SRAM, 0x4000, core_id=core))
    for pair in range(core_count // 2):
        core = pair * 2
        peer = memory.stage(partials[core + 1], f'peer:{core}', 0x6000, core_id=core)
        output_view = memory.view(target, f'output:{core}', pair * INTERMEDIATE, (1, INTERMEDIATE), core_id=core)
        outputs.append((memory.stage(output_view, f'output-stage:{core}', 0x8000, core_id=core), output_view))
        peers.append(peer)
    for pair in range(core_count // 2):
        reducer, sender = pair * 2, pair * 2 + 1
        for core in (reducer, sender):
            memory.copy(f'load-token:{core}', *inputs[core])
            memory.copy(f'load-weight:{core}', *weights[core])
            memory.matmul(f'gemm:{core}', inputs[core][1], weights[core][1], partials[core])
        memory.copy(f'push:{sender}', partials[sender], peers[pair])
        memory.add(f'combine:{reducer}', partials[reducer], peers[pair], outputs[pair][0])
        memory.copy(f'store:{reducer}', *outputs[pair])
    name = f'paired-{core_count}-top{top_k}-capacity{capacity_factor_q16}'
    program = _scheduled_memory(memory, arch, name, cores)
    layout = MoeLayerLayout(HIDDEN, cores, _gates(program), (0x80000, 0x20000),
        top_k=top_k, capacity_q16=capacity_factor_q16,
        max_tokens=8 if core_count == 2 else 32, max_requests=2 if core_count == 2 else core_count)
    return _with_layers(program, (layout,))


def moe_dual_program(arch, capacity_factor_q16=0x10000, top_k=2):
    return _paired_program(arch, 2, capacity_factor_q16, top_k)


def moe_dual_drop_program(arch):
    return moe_dual_program(arch, capacity_factor_q16=0x8000)


def moe_dual_copy_program(arch):
    return moe_dual_program(arch, top_k=1)


def moe_quad_program(arch):
    return _paired_program(arch, QUAD_CORES, capacity_factor_q16=0x80000)


def moe_multi_program(arch):
    memory = AuthoredMemory(arch, 0)
    source = memory.buffer('token', TensorRole.INPUT, DType.FP16, (1, HIDDEN), MemorySpace.HBM, 0x100000)
    weight = memory.buffer('weight', TensorRole.WEIGHT, DType.FP16, (64, 64), MemorySpace.HBM, 0,
        content_sha256=synthetic_weight_digest().hex())
    target = memory.buffer('output', TensorRole.OUTPUT, DType.FP16, (2, INTERMEDIATE), MemorySpace.HBM, 0x200000)
    groups = []
    for index, hidden in enumerate((32, 16)):
        shift = index * 0x10000
        input_view = memory.view(source, f'token:{index}', 0, (1, hidden))
        weight_view = memory.view(weight, f'weight:{index}', 0, (hidden, INTERMEDIATE))
        output_view = memory.view(target, f'output:{index}', index * INTERMEDIATE, (1, INTERMEDIATE))
        groups.append((input_view, memory.stage(input_view, f'input-stage:{index}', shift),
            weight_view, memory.stage(weight_view, f'weight-stage:{index}', shift + 0x2000),
            memory.stage(output_view, f'output-stage:{index}', shift + 0x4000), output_view))
    previous = ()
    for index, (input_view, left, weight_view, right, result, output_view) in enumerate(groups):
        memory.copy(f'load-token:{index}', input_view, left, after=previous)
        memory.copy(f'load-weight:{index}', weight_view, right, after=previous)
        memory.matmul(f'gemm:{index}', left, right, result)
        previous = (memory.copy(f'store:{index}', result, output_view),)
    program = _scheduled_memory(memory, arch, 'multiple-layers', (0,))
    gates = _gates(program)
    layouts = tuple(MoeLayerLayout(hidden, (0,) * count, (gates[index],), scratch, max_m=8)
        for index, (hidden, count, scratch) in enumerate(((32, 2, (0x80000, 0x20000)), (16, 4, (0xA0000, 0x10000)))))
    return _with_layers(program, layouts)
