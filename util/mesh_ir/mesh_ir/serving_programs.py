"""Agent serving program fixtures and profile assembly (Gate 6 R1)."""

from __future__ import annotations

import dataclasses

from mesh_ir.generated import abi as A
from mesh_ir.model import (
    StringEntry,
    AgentInstanceMemberBinding,
    AgentInstanceTensorBinding,
    AgentInstanceProfile,
    AgentPublishSurrogateBinding,
    AgentRequestBindingRequirement,
    AgentRequestProfile,
    AgentSourceCoreMap,
)

MEMORY_SPACE_CORE_SRAM = A.MEMORY_SPACE.CORE_SRAM
FULL_VIEW_SLOT_SHIFT = 4096

HIDDEN = 8
INTERMEDIATE = 8
FP16 = A.DTYPE.FP16
RO = A.ACCESS_KIND.READ_ONLY
RW = A.ACCESS_KIND.READ_WRITE
HBM_REGION = 0

TOKEN_BYTES = HIDDEN * 2
WEIGHT_BYTES = HIDDEN * INTERMEDIATE * 2
OUTPUT_BYTES = HIDDEN * INTERMEDIATE * 2
KV_BYTES_PER_TOKEN = 16
PREFILL_TOKENS = 8
DECODE_CHUNK_TOKENS = 1
KV_BYTES = PREFILL_TOKENS * KV_BYTES_PER_TOKEN
KV_ALLOC_BYTES = KV_BYTES + KV_BYTES_PER_TOKEN

INPUT_BINDING_BYTES = TOKEN_BYTES * PREFILL_TOKENS
HOST_OUTPUT_BYTES = OUTPUT_BYTES
PUBLISH_CHUNK_BYTES = OUTPUT_BYTES // 2

IN_OFFSET = 0x0000
W_OFFSET = 0x2000
KV_OFFSET = 0x3000
ACT_OFFSET = 0x5000
PUB_OFFSET = 0x4000

IN_BASE = 0x100000
W_BASE = 0x200000
OUT_BASE = 0x300000
KV_BASE = 0x400000


def project_member_tensor_bindings(program):
    from mesh_ir.generated import agent_abi as AG
    from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog

    catalog = ProgramBindingCatalog(program)
    rows = []
    for instance in program.agent_instance_profiles:
        for member in program.agent_instance_member_bindings[instance.member_binding_first:
                instance.member_binding_first + instance.member_binding_count]:
            for kind, symbol, root in (
                (AG.BINDING_KIND.HOST_INPUT, member.static_input_symbol_id, instance.primary_input_symbol_id),
                (AG.BINDING_KIND.HOST_OUTPUT, member.static_output_symbol_id, instance.primary_output_symbol_id),
                (AG.BINDING_KIND.KV_EXTERNAL, member.static_kv_symbol_id, instance.primary_kv_symbol_id),
            ):
                if not symbol:
                    continue
                binding = catalog.binding(instance.mesh_entrypoint_id, instance.mesh_profile_id,
                    program.strings[symbol - 1].value)
                rows.append(AgentInstanceTensorBinding(instance.instance_profile_id,
                    member.member_ordinal, kind, symbol, root, 0, binding.slot.required_allocation_bytes))
    return tuple(sorted(rows, key=lambda row: (row.instance_profile_id, row.member_ordinal, row.static_symbol_id)))


def serving_program(arch, output_tokens=1, kv_splits=1, *, publish_max_burst_beats=None, input_parts=1):
    return _serving_program(arch, output_tokens, dual=False, kv_splits=kv_splits,
        publish_max_burst_beats=publish_max_burst_beats, input_parts=input_parts)


def repair_serving_program(arch):
    return _serving_program(arch, 2, dual=False, repair=True)


def split_kv_serving_program(arch):
    return _serving_program(arch, 2, dual=False, kv_splits=2)


def keyed_serving_program(arch):
    from mesh_ir.serving_profiles import apply_request_profile_keys

    return apply_request_profile_keys(serving_program(arch))


def full_view_serving_program(arch, output_tokens=1, padding_tokens=0):
    return _serving_program(arch, output_tokens, dual=True, padding_tokens=padding_tokens)


def moe_full_view_serving_program(arch, output_tokens=1, padding_tokens=0, *, publish_max_burst_beats=None):
    return _serving_program(arch, output_tokens, dual=True, dynamic_moe=True,
                            padding_tokens=padding_tokens, publish_max_burst_beats=publish_max_burst_beats)


@dataclasses.dataclass(frozen=True)
class ServingPhase:
    instance_id: int
    name: str
    phase: int
    member_count: int
    kv_before: int
    valid_tokens: int
    input_bytes: int
    input_offset: int = 0
    request_profile: int = 1
    path: int = A.PATH_KIND.INITIAL_PREFILL


def _serving_program(arch, output_tokens, dual, kv_splits=1, dynamic_moe=False, repair=False,
                     padding_tokens=0, publish_max_burst_beats=None, input_parts=1):
    from mesh_ir.authoring import AuthoredMemory
    from mesh_ir.builder import ProgramBuilder
    from mesh_ir.canonical import semantic_sha256
    from mesh_ir.execution_view import semantic_profile_ranges
    from mesh_ir.ir.common import DType, MemorySpace, StorageClass, TensorRole
    from mesh_ir.ir.kernel_ir import ElementRegion
    from mesh_ir.moe_programs import synthetic_weight_digest
    from mesh_ir.scheduled.model import AuthoredProgramOrigin, HaltAttrs, KernelCommandSource, LocalAllocationBacking, RequestBeginAttrs, RequestEndAttrs

    if type(output_tokens) is not int or output_tokens <= 0 or type(kv_splits) is not int or kv_splits <= 0 or PREFILL_TOKENS % kv_splits:
        raise ValueError('serving token and split counts must be positive and exactly divisible')
    if type(padding_tokens) is not int or padding_tokens < 0:
        raise ValueError('serving padding must be nonnegative')
    if type(input_parts) is not int or input_parts < 1 or INPUT_BINDING_BYTES % (input_parts * arch.axi_data_bytes):
        raise ValueError('input parts must divide the aligned input capacity')
    output_bytes = output_tokens * OUTPUT_BYTES
    input_capacity = INPUT_BINDING_BYTES * (2 if repair else 1)
    full_tokens = 2 * PREFILL_TOKENS + output_tokens if repair else PREFILL_TOKENS
    kv_bytes = (full_tokens + output_tokens) * KV_BYTES_PER_TOKEN
    phases = []
    for member_count, base in (((1, 11), (2, 111)) if dual else ((1, 11),)):
        suffix = '' if member_count == 1 else '_ordinal1'
        phases.append(ServingPhase(base, 'serving_prefill' + suffix, A.PHASE.PREFILL,
            member_count, 0, PREFILL_TOKENS, INPUT_BINDING_BYTES))
        for index in range(output_tokens):
            phases.append(ServingPhase(base + 1 + index, f'serving_decode_{index}{suffix}',
                A.PHASE.DECODE, member_count, PREFILL_TOKENS + index, 1, 0))
        phases.append(ServingPhase(base + 1 + output_tokens, 'serving_publish' + suffix,
            A.PHASE.PUBLISH, member_count, PREFILL_TOKENS + output_tokens, 0, 0))
    if repair:
        for path in (A.PATH_KIND.KV_REUSE, A.PATH_KIND.REPREFILL):
            before = PREFILL_TOKENS + output_tokens if path == A.PATH_KIND.KV_REUSE else 0
            for index in range(output_tokens + 2):
                phase = A.PHASE.PREFILL if index == 0 else A.PHASE.DECODE if index <= output_tokens else A.PHASE.PUBLISH
                consumed = INPUT_BINDING_BYTES if before else input_capacity
                phases.append(ServingPhase(200 + path * 10 + index, f'repair_{path}_{index}', phase, 1,
                    before if index == 0 else full_tokens + min(index - 1, output_tokens),
                    (PREFILL_TOKENS if before else full_tokens) if index == 0 else 1 if phase == A.PHASE.DECODE else 0,
                    consumed if index == 0 else 0, input_capacity - consumed if index == 0 else 0, 2, path))
    builder = ProgramBuilder(arch, AuthoredProgramOrigin('serving', 'request-lifecycle', 2))
    authored = []
    input_mappings = []
    fill_pattern = A.DMA_FILL_RUNTIME_BOUND_SENTINEL.to_bytes(8, 'little')
    for phase in phases:
        memory = AuthoredMemory(arch, arch.core_ids[0])
        members = []
        for ordinal in range(phase.member_count):
            hbm_shift = ordinal * FULL_VIEW_SLOT_SHIFT
            sram_shift = 0 if phase.member_count == 1 else (ordinal + 1) * 0x10000
            names = {kind: f'{kind}_slot_0' if ordinal == 0 else f'full_view_ordinal1_static_{kind}_symbol_id'
                     for kind in ('in', 'out', 'kv')}
            if ordinal:
                names['in'] = 'full_view_ordinal1_static_input_symbol_id'
                names['out'] = 'full_view_ordinal1_static_output_symbol_id'
            member = {}
            if phase.phase == A.PHASE.PREFILL:
                input_rows = (phase.input_offset + phase.input_bytes) // TOKEN_BYTES
                rows = input_rows + padding_tokens
                input_capacity_bytes = ((input_rows * TOKEN_BYTES + arch.axi_data_bytes - 1) // arch.axi_data_bytes) * arch.axi_data_bytes
                if input_parts > 1:
                    part_rows = input_rows // input_parts
                    member['in'] = memory.buffer(f'input-stage:{ordinal}', TensorRole.INPUT, DType.FP16,
                        (rows, HIDDEN), MemorySpace.CORE_SRAM, IN_OFFSET + sram_shift,
                        storage_class=StorageClass.EXTERNAL)
                    member['input_views'] = tuple(memory.view(member['in'], f'input-part:{ordinal}:{part}',
                        part * part_rows * HIDDEN, (part_rows, HIDDEN)) for part in range(input_parts))
                    member['inputs'] = tuple(memory.stage(view, names['in'] if part == 0 else f"{names['in']}:part:{part}",
                        IN_BASE + hbm_shift + part * part_rows * TOKEN_BYTES, space=MemorySpace.HOST_SHARED)
                        for part, view in enumerate(member['input_views']))
                    input_mappings.extend((phase.instance_id, ordinal, part,
                        names['in'] if part == 0 else f"{names['in']}:part:{part}", part_rows * TOKEN_BYTES)
                        for part in range(input_parts))
                elif padding_tokens:
                    member['in'] = memory.buffer(f'input-stage:{ordinal}', TensorRole.INPUT, DType.FP16,
                        (rows, HIDDEN), MemorySpace.CORE_SRAM, IN_OFFSET + sram_shift,
                        storage_class=StorageClass.EXTERNAL)
                    valid = memory.view(member['in'], f'input-valid:{ordinal}', 0, (input_rows, HIDDEN))
                    member['input'] = memory.stage(valid, names['in'], IN_BASE + hbm_shift,
                        space=MemorySpace.HOST_SHARED, capacity_bytes=input_capacity_bytes)
                else:
                    member['input'] = memory.buffer(names['in'], TensorRole.INPUT, DType.FP16,
                        (input_rows, HIDDEN), MemorySpace.HOST_SHARED, IN_BASE + hbm_shift,
                        capacity_bytes=input_capacity_bytes)
                    member['in'] = memory.stage(member['input'], f'input-stage:{ordinal}', IN_OFFSET + sram_shift)
                if ordinal == 0:
                    weight = memory.buffer('weight', TensorRole.WEIGHT, DType.FP16, (HIDDEN, INTERMEDIATE),
                        MemorySpace.HBM, W_BASE, content_sha256=synthetic_weight_digest().hex())
                member['weight'] = weight
                member['w'] = memory.stage(weight, f'weight-stage:{ordinal}', W_OFFSET + sram_shift)
                member['act'] = memory.buffer(f'activation:{ordinal}', TensorRole.ACTIVATION, DType.FP16,
                    (rows, INTERMEDIATE), MemorySpace.CORE_SRAM, ACT_OFFSET + sram_shift)
            if phase.phase in (A.PHASE.PREFILL, A.PHASE.DECODE):
                member['external_kv'] = memory.buffer(names['kv'], TensorRole.KV_CACHE, DType.INT8, (kv_bytes,),
                    MemorySpace.HBM, KV_BASE + hbm_shift,
                    capacity_bytes=((kv_bytes + arch.axi_data_bytes - 1) // arch.axi_data_bytes) * arch.axi_data_bytes)
                member['kv'] = memory.stage(member['external_kv'], f'kv-stage:{ordinal}', KV_OFFSET + sram_shift)
            else:
                member['output'] = memory.buffer(names['out'], TensorRole.OUTPUT, DType.INT8, (output_bytes,),
                    MemorySpace.HOST_SHARED, OUT_BASE + hbm_shift)
                member['pub'] = memory.stage(member['output'], f'publish-stage:{ordinal}', PUB_OFFSET + sram_shift)
            members.append(member)
        publishes = []
        loads = []
        for ordinal, member in enumerate(members):
            if phase.phase == A.PHASE.PREFILL:
                rows = (phase.input_offset + phase.input_bytes) // TOKEN_BYTES + padding_tokens
                if phase.input_offset:
                    prefix = ElementRegion((0, 0), (phase.input_offset // TOKEN_BYTES, HIDDEN), (1, 1))
                    memory.fill(f'input-prefix:{ordinal}', member['in'], bytes(8), region=prefix)
                if padding_tokens:
                    padding = ElementRegion(((phase.input_offset + phase.input_bytes) // TOKEN_BYTES, 0),
                        (padding_tokens, HIDDEN), (1, 1))
                    memory.fill(f'input-padding:{ordinal}', member['in'], bytes(8), region=padding)
                region = ElementRegion((phase.input_offset // TOKEN_BYTES, 0), (phase.input_bytes // TOKEN_BYTES, HIDDEN), (1, 1))
                if input_parts > 1:
                    for part, (source, destination) in enumerate(zip(member['inputs'], member['input_views'])):
                        loads.append(memory.copy(f'load-input:{ordinal}:{part}', source, destination))
                else:
                    loads.append(memory.copy(f'load-input:{ordinal}', member['input'], member['in'], source_region=region, destination_region=region))
                gate_dependencies = tuple(loads) if dynamic_moe and ordinal == len(members) - 1 else ()
                loads.append(memory.copy(f'load-weight:{ordinal}', member['weight'], member['w'], after=gate_dependencies))
        for ordinal, member in enumerate(members):
            after = ()
            if phase.phase == A.PHASE.PREFILL:
                after = (memory.matmul(f'compute:{ordinal}', member['in'], member['w'], member['act']),)
            if phase.phase in (A.PHASE.PREFILL, A.PHASE.DECODE):
                prefix_bytes = phase.kv_before * KV_BYTES_PER_TOKEN
                if prefix_bytes:
                    prefix = ElementRegion((0,), (prefix_bytes,), (1,))
                    memory.copy(f'load-kv:{ordinal}', member['external_kv'], member['kv'], source_region=prefix, destination_region=prefix)
                size = phase.valid_tokens * KV_BYTES_PER_TOKEN
                region = ElementRegion((prefix_bytes,), (size,), (1,))
                memory.fill(f'append-kv:{ordinal}', member['kv'], fill_pattern, region=region, after=after)
                split_count = kv_splits if phase.phase == A.PHASE.PREFILL else 1
                part_bytes = size // split_count
                for part in range(split_count):
                    part_region = ElementRegion((prefix_bytes + part * part_bytes,), (part_bytes,), (1,))
                    memory.copy(f'store-kv:{ordinal}:{part}', member['kv'], member['external_kv'],
                        source_region=part_region, destination_region=part_region)
            else:
                fill = memory.fill(f'publish-fill:{ordinal}', member['pub'], fill_pattern)
                chunks = tuple(ElementRegion((offset,), (min(PUBLISH_CHUNK_BYTES, output_bytes - offset),), (1,))
                    for offset in range(0, output_bytes, PUBLISH_CHUNK_BYTES))
                memory.copy_regions(f'publish-store:{ordinal}', member['pub'], member['output'], tuple((chunk, chunk) for chunk in chunks), max_burst_beats=publish_max_burst_beats)
                publishes.append((member['pub'].object_id, fill.op_id))
        variant = builder.variant(phase.name, phase.name, phase.name, records=memory.records(),
            allocations=memory.allocations, external_backings=memory.external_backings, binding_slots=memory.binding_slots)
        stream = variant.stream(arch.core_ids[0], 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
        stream.control_command(RequestBeginAttrs())
        for operation in memory.effects:
            stream.kernel_command(operation.op_id)
        stream.control_command(RequestEndAttrs())
        stream.control_command(HaltAttrs())
        authored.append((phase, publishes))
    program = builder.build()
    strings = list(program.strings)
    names = {entry.value: index for index, entry in enumerate(strings, 1)}
    for name in ('input', 'output', 'kv'):
        if name not in names:
            strings.append(StringEntry(name))
            names[name] = len(strings)
    instances, bindings, publishes = [], [], []
    ordinal_one = {f'static_{kind}_symbol_id': names.get(f'full_view_ordinal1_static_{kind}_symbol_id', 0)
                   for kind in ('input', 'output', 'kv')}
    for variant, (phase, sources) in zip(program.semantics.variants, authored, strict=True):
        row = _instance_profile(phase.instance_id, (variant.entrypoint_id, variant.profile_id), phase.phase,
            phase.member_count, len(bindings), phase.kv_before, output_bytes, names)
        row = dataclasses.replace(row, request_profile_id=phase.request_profile, path_kind=phase.path,
            valid_tokens_per_member=phase.valid_tokens, host_input_dma_bytes_per_member=phase.input_bytes,
            local_padded_tokens_per_member=padding_tokens if phase.phase == A.PHASE.PREFILL else 0,
            kv_read_bytes_per_member=phase.kv_before * KV_BYTES_PER_TOKEN if phase.phase != A.PHASE.PUBLISH else 0,
            kv_write_bytes_per_member=phase.valid_tokens * KV_BYTES_PER_TOKEN if phase.phase != A.PHASE.PUBLISH else 0)
        instances.append(row)
        for ordinal in range(phase.member_count):
            bindings.append(_member_binding(phase.instance_id, ordinal, phase.phase, names, ordinal_one))
        for ordinal, (object_id, operation_id) in enumerate(sources):
            global_object = variant.membership.objects.first_id + object_id - 1
            backing = next(item.backing for item in program.semantics.object_backings if item.object_id == global_object)
            if type(backing) is not LocalAllocationBacking:
                raise ValueError('PUBLISH producer is not backed by SRAM')
            global_operation = variant.membership.kernel_ops.first_id + operation_id - 1
            command_id = next(item.command_id for item in program.semantics.command_semantics
                if type(item.source) is KernelCommandSource and item.source.kernel_op_id == global_operation)
            events = {item.completion_event for item in program.dma_descriptors if item.command_id == command_id}
            if len(events) != 1:
                raise ValueError('PUBLISH producer has no unique completion event')
            publishes.append(AgentPublishSurrogateBinding(phase.instance_id, ordinal, 0, backing.allocation_id,
                command_id, events.pop(), 1, 1, 0, 0))
    requests = [_request_profile(names, output_tokens, output_bytes)]
    requirements = _requirements(names)
    if repair:
        requests.append(dataclasses.replace(requests[0], profile_id=2, expected_cached_tokens=PREFILL_TOKENS + output_tokens,
            full_input_tokens=full_tokens, input_binding_bytes=input_capacity, full_input_dma_bytes=input_capacity, path_mask=6))
        requirements.extend(dataclasses.replace(item, request_profile_id=2) for item in tuple(requirements))
    program = dataclasses.replace(program, strings=tuple(strings),
        required_features=program.required_features | A.AGENT_SERVING_V1 | A.PROFILE_SCOPED_EXECUTION_V1,
        profile_stream_ranges=semantic_profile_ranges(program.semantics), agent_request_profiles=tuple(requests),
        agent_instance_profiles=tuple(instances), agent_source_core_map=(AgentSourceCoreMap(arch.core_ids[0], 0),),
        agent_instance_member_bindings=tuple(bindings), agent_request_binding_requirements=tuple(requirements),
        agent_publish_surrogate_bindings=tuple(publishes))
    if dynamic_moe:
        program = attach_serving_moe(program, arch)
    mappings = list(project_member_tensor_bindings(program))
    if input_mappings:
        from mesh_ir.generated import agent_abi as AG
        for instance_id, ordinal, part, symbol, size in input_mappings:
            if part:
                instance = next(row for row in instances if row.instance_profile_id == instance_id)
                mappings.append(AgentInstanceTensorBinding(instance_id, ordinal, AG.BINDING_KIND.HOST_INPUT,
                    names[symbol], instance.primary_input_symbol_id, part * size, size))
    program = dataclasses.replace(program, agent_instance_tensor_bindings=tuple(sorted(mappings,
        key=lambda row: (row.instance_profile_id, row.member_ordinal, row.static_symbol_id))))
    return dataclasses.replace(program, semantic_sha256=program.computed_semantic_sha256)


def attach_serving_moe(program, arch):
    from mesh_ir.execution_view import build_execution_views
    from mesh_ir.model import ContentDigest, MoeDynamicRegion, MoeExpertSpec, MoeKernelSpec, MoeLayerSpec
    from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog

    scratch = arch.partition(A.SRAM_PARTITION_KIND.RUNTIME_SCRATCH)
    if scratch is None:
        raise ValueError('serving MoE requires an explicit runtime scratch partition')
    catalog = ProgramBindingCatalog(program)
    views = build_execution_views(program)
    layers, experts, regions, kernels = [], [], [], []
    digests = list(program.content_digests)
    weight_symbol = next(index for index, item in enumerate(program.strings, 1) if item.value == 'weight')
    for instance in program.agent_instance_profiles:
        if instance.phase != A.PHASE.PREFILL:
            continue
        view = views.for_instance(instance.mesh_entrypoint_id, instance.mesh_profile_id)
        commands = [program.commands[index] for index in view.command_indices]
        compute_index = next(index for index, item in enumerate(commands) if item.opcode == A.OPCODE.GEMM)
        insert, resume = commands[compute_index - 1:compute_index + 1]
        binding = catalog.binding(instance.mesh_entrypoint_id, instance.mesh_profile_id, 'weight')
        tensor = program.tensors[binding.tensor.tensor_id - 1]
        digest = ContentDigest(A.CONTENT_DIGEST_OBJECT_KIND['TENSOR'], 0, tensor.tensor_id, tensor.content_sha256)
        if digest not in digests:
            digests.append(digest)
        index, layer = len(layers), len(layers) + 1
        scratch_bytes = 0x10000
        if (index + 1) * scratch_bytes > scratch.bytes:
            raise ValueError('serving MoE layers exceed admitted runtime scratch')
        layers.append(MoeLayerSpec(layer, index, 2 * index, 2, 2, TOKEN_BYTES, KV_BYTES_PER_TOKEN,
            65536, A.MOE_OVERFLOW_POLICY.DROP, A.MOE_TRANSPORT_MODE.VARIABLE_ALL_TO_ALL_V,
            index, 1, 16, 2, 32, 64, 32, 8, 8, 0, 65))
        experts.extend(MoeExpertSpec(layer, expert, 0, insert.core_id, 0, weight_symbol, 0,
            WEIGHT_BYTES, digests.index(digest)) for expert in range(2))
        regions.append(MoeDynamicRegion(1, layer, insert.core_id, insert.stream_id,
            insert.command_id, resume.command_id, insert.signal_event,
            scratch.base + index * scratch_bytes, scratch_bytes, scratch.alignment, 63, 64, 32, 8, 8, 0))
        kernels.append(MoeKernelSpec(layer, A.OPCODE.GEMM, FP16, A.DTYPE.FP32, FP16,
            1, HIDDEN, HIDDEN, 0, A.MOE_COMBINE_KIND.LOCAL_REDUCE, 1, 65536, 4, 2,
            TOKEN_BYTES, KV_BYTES_PER_TOKEN, 0, WEIGHT_BYTES, 64, 16, 2, 1, 0))
    return dataclasses.replace(program, required_features=program.required_features | A.DYNAMIC_MOE_V1,
        content_digests=tuple(digests), moe_layer_specs=tuple(layers), moe_expert_specs=tuple(experts),
        moe_dynamic_regions=tuple(regions), moe_kernel_specs=tuple(kernels))


def _instance_profile(instance_id, selector, phase, member_count,
                      binding_first, kv_tokens_before, output_bytes, symbols):
    entrypoint, profile = selector
    if phase == A.PHASE.PREFILL:
        primary_input = symbols["input"]
        primary_output = 0
        primary_kv = symbols["kv"]
        host_input = INPUT_BINDING_BYTES
        host_output = 0
        kv_read = 0
        kv_write = KV_BYTES
        decode_chunk = 0
        valid_tokens = PREFILL_TOKENS
    elif phase == A.PHASE.DECODE:
        primary_input = 0
        primary_output = 0
        primary_kv = symbols["kv"]
        host_input = 0
        host_output = 0
        kv_read = kv_tokens_before * KV_BYTES_PER_TOKEN
        kv_write = DECODE_CHUNK_TOKENS * KV_BYTES_PER_TOKEN
        decode_chunk = DECODE_CHUNK_TOKENS
        valid_tokens = DECODE_CHUNK_TOKENS
    else:
        primary_input = 0
        primary_output = symbols["output"]
        primary_kv = 0
        host_input = 0
        host_output = output_bytes
        kv_read = 0
        kv_write = 0
        decode_chunk = 0
        valid_tokens = 0
    return AgentInstanceProfile(
        instance_profile_id=instance_id, request_program_id=1,
        request_profile_id=1, path_kind=A.PATH_KIND.INITIAL_PREFILL,
        phase=phase, member_count=member_count,
        decode_chunk_tokens=decode_chunk, mesh_entrypoint_id=entrypoint,
        mesh_profile_id=profile, valid_tokens_per_member=valid_tokens,
        kv_tokens_before=kv_tokens_before, local_padded_members=0,
        local_padded_tokens_per_member=0,
        primary_input_symbol_id=primary_input,
        primary_output_symbol_id=primary_output,
        primary_kv_symbol_id=primary_kv, flags=0,
        member_binding_first=binding_first,
        member_binding_count=member_count,
        host_input_dma_bytes_per_member=host_input,
        host_output_dma_bytes_per_member=host_output,
        kv_read_bytes_per_member=kv_read,
        kv_write_bytes_per_member=kv_write)


def _member_binding(instance_id, ordinal, phase, symbols, ordinal_one):
    fields = {"static_input_symbol_id": 0, "static_output_symbol_id": 0,
              "static_kv_symbol_id": 0}
    if ordinal == 0:
        fields["static_input_symbol_id"] = symbols["in_slot_0"]
        fields["static_output_symbol_id"] = symbols["out_slot_0"]
        fields["static_kv_symbol_id"] = symbols["kv_slot_0"]
    else:
        for field in fields:
            fields[field] = ordinal_one.get(field, 0)
    input_ok, output_ok, kv_ok = {
        A.PHASE.PREFILL: (True, False, True),
        A.PHASE.DECODE: (False, False, True),
        A.PHASE.PUBLISH: (False, True, False)}[phase]
    if not input_ok:
        fields["static_input_symbol_id"] = 0
    if not output_ok:
        fields["static_output_symbol_id"] = 0
    if not kv_ok:
        fields["static_kv_symbol_id"] = 0
    return AgentInstanceMemberBinding(
        instance_profile_id=instance_id, member_ordinal=ordinal, reserved0=0,
        expected_logical_source_rank=0, **fields)


def _request_profile(symbols, output_tokens=1, output_bytes=HOST_OUTPUT_BYTES):
    return AgentRequestProfile(
        program_id=1, profile_id=1, flags=A.AGENT_REQUEST_FLAGS.HAS_KV,
        reserved0=0, requested_profile_key=0,
        delta_input_tokens=PREFILL_TOKENS,
        full_input_tokens=PREFILL_TOKENS, expected_cached_tokens=0,
        output_tokens=output_tokens * DECODE_CHUNK_TOKENS,
        input_binding_bytes=INPUT_BINDING_BYTES,
        delta_input_dma_bytes=INPUT_BINDING_BYTES,
        full_input_dma_bytes=INPUT_BINDING_BYTES,
        host_output_bytes=output_bytes,
        primary_input_symbol_id=symbols["input"],
        primary_output_symbol_id=symbols["output"],
        primary_kv_symbol_id=symbols["kv"], source_rank_count=1,
        source_core_map_begin=0, path_mask=0b001,
        kv_bytes_per_token=KV_BYTES_PER_TOKEN,
        publish_chunk_bytes=PUBLISH_CHUNK_BYTES)


def _requirements(symbols):
    records = [
        AgentRequestBindingRequirement(
            request_program_id=1, request_profile_id=1, binding_kind=1,
            binding_flags=1, symbol_id=symbols["input"], reserved=0),
        AgentRequestBindingRequirement(
            request_program_id=1, request_profile_id=1, binding_kind=4,
            binding_flags=5, symbol_id=symbols["weight"], reserved=0),
        AgentRequestBindingRequirement(
            request_program_id=1, request_profile_id=1, binding_kind=2,
            binding_flags=2, symbol_id=symbols["output"], reserved=0),
        AgentRequestBindingRequirement(
            request_program_id=1, request_profile_id=1, binding_kind=3,
            binding_flags=15, symbol_id=symbols["kv"], reserved=0),
    ]
    return sorted(records, key=lambda record: (
        record.request_program_id, record.request_profile_id,
        record.symbol_id))




def dual_member_ranks_program(arch, ranks):
    from mesh_ir.canonical import semantic_sha256

    if len(ranks) != 2 or any(type(rank) is not int or rank not in (0, 1) for rank in ranks):
        raise ValueError('dual member ranks require two ranks from the source core map')
    from mesh_ir.serving_profiles import member_rank_vector

    program = ranked_full_view_serving_program(arch)
    instances, members = [], []
    for instance in program.agent_instance_profiles:
        if instance.member_count == 2 and member_rank_vector(program, instance) != tuple(ranks):
            continue
        first = len(members)
        members.extend(row
            for row in program.agent_instance_member_bindings[
                instance.member_binding_first:instance.member_binding_first + instance.member_binding_count])
        instances.append(dataclasses.replace(instance, member_binding_first=first))
    ids = {row.instance_profile_id for row in instances}
    program = dataclasses.replace(program,
        agent_request_profiles=(dataclasses.replace(program.agent_request_profiles[0], source_rank_count=2),),
        agent_source_core_map=program.agent_source_core_map,
        agent_instance_profiles=tuple(instances), agent_instance_member_bindings=tuple(members),
        agent_publish_surrogate_bindings=tuple(row for row in program.agent_publish_surrogate_bindings
            if row.instance_profile_id in ids))
    program = dataclasses.replace(program, agent_instance_tensor_bindings=project_member_tensor_bindings(program))
    return dataclasses.replace(program, semantic_sha256=program.computed_semantic_sha256)


def full_view_alias_program(program):
    members = list(program.agent_instance_member_bindings)
    for instance in program.agent_instance_profiles:
        if instance.member_count == 2 and instance.phase == A.PHASE.PREFILL:
            first = instance.member_binding_first
            members[first + 1] = dataclasses.replace(members[first + 1],
                static_input_symbol_id=members[first].static_input_symbol_id)
    program = dataclasses.replace(program, agent_instance_member_bindings=tuple(members))
    return dataclasses.replace(program, agent_instance_tensor_bindings=project_member_tensor_bindings(program))


def full_view_crossed_publish_program(program):
    records = list(program.agent_publish_surrogate_bindings)
    dual = [record for record in records
            if record.instance_profile_id == 113]
    if len(dual) != 2:
        raise ValueError("full view PUBLISH needs two member ordinals")
    crossed = []
    for record in records:
        if record.instance_profile_id != 113:
            crossed.append(record)
            continue
        other = dual[1 - record.member_ordinal]
        crossed.append(dataclasses.replace(
            record, allocation_id=other.allocation_id,
            producer_command_id=other.producer_command_id,
            completion_event_id=other.completion_event_id))
    return dataclasses.replace(program,
                               agent_publish_surrogate_bindings=tuple(crossed))


def ranked_full_view_serving_program(arch, output_tokens=1):
    from mesh_ir.canonical import semantic_sha256

    program = full_view_serving_program(arch, output_tokens)
    instances = list(program.agent_instance_profiles)
    members = list(program.agent_instance_member_bindings)
    publish = list(program.agent_publish_surrogate_bindings)
    for ranks in ((1,), (0, 1), (1, 0), (1, 1)):
        for source in program.agent_instance_profiles:
            if source.member_count != len(ranks):
                continue
            instance_id = 100000 + len(instances)
            first = len(members)
            for ordinal, rank in enumerate(ranks):
                members.append(dataclasses.replace(
                    program.agent_instance_member_bindings[source.member_binding_first + ordinal],
                    instance_profile_id=instance_id, expected_logical_source_rank=rank))
            instances.append(dataclasses.replace(source, instance_profile_id=instance_id,
                                                  member_binding_first=first))
            publish.extend(dataclasses.replace(row, instance_profile_id=instance_id)
                           for row in program.agent_publish_surrogate_bindings
                           if row.instance_profile_id == source.instance_profile_id)
    program = dataclasses.replace(
        program,
        agent_request_profiles=(dataclasses.replace(program.agent_request_profiles[0],
                                                   source_rank_count=2),),
        agent_source_core_map=(AgentSourceCoreMap(core_id=arch.core_ids[0], reserved=0),
                               AgentSourceCoreMap(core_id=arch.core_ids[0], reserved=0)),
        agent_instance_profiles=tuple(instances), agent_instance_member_bindings=tuple(members),
        agent_publish_surrogate_bindings=tuple(publish))
    program = dataclasses.replace(program, agent_instance_tensor_bindings=project_member_tensor_bindings(program))
    return dataclasses.replace(program, semantic_sha256=program.computed_semantic_sha256)
