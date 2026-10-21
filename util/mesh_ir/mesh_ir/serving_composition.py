from dataclasses import replace
from math import prod

from mesh_ir.authoring import AuthoredMemory
from mesh_ir.builder import ProgramBuilder
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as AG
from mesh_ir.execution_view import semantic_profile_ranges
from mesh_ir.ir.common import MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.kernel_ir import StateOrigin
from mesh_ir.model import AgentInstanceMemberBinding, AgentInstanceProfile, AgentInstanceTensorBinding, AgentRequestBindingRequirement, AgentRequestProfile, AgentSourceCoreMap, Program, StringEntry
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog, ProgramObjectBinding
from mesh_ir.scheduled.model import AuthoredProgramOrigin, ComposedProgramOrigin, HaltAttrs, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.serving_profiles import HOST_LOAD, ServingInstanceIo, apply_request_profile_keys
from mesh_ir.tensor_payload import PackedTensorInput


def build_publication_program(arch, output: ProgramObjectBinding, *, staging_bytes: int, core_id: int | None = None):
    if type(staging_bytes) is not int or not 0 < staging_bytes <= arch.sram_bytes:
        raise MeshIrError('E_SRAM_OOM', 'publication staging must fit the physical SRAM')
    tensor = output.tensor
    elements = prod(tensor.shape)
    contiguous = tuple(prod(tensor.shape[index + 1:]) for index in range(len(tensor.shape)))
    if (tensor.role is not TensorRole.OUTPUT or output.object.memory_space is not MemorySpace.HBM or
            tensor.strides != contiguous or tensor.storage_offset_elements != 0 or
            tensor.logical_extent_bytes != elements * tensor.dtype.byte_width or
            output.object.footprint_bytes != tensor.logical_extent_bytes):
        raise MeshIrError('E_BINDING_ROLE', 'publication requires a complete contiguous HBM output storage tensor')
    if staging_bytes % tensor.dtype.byte_width:
        raise MeshIrError('E_EXPORT_LAYOUT', 'publication staging must contain complete elements')
    core_id = arch.core_ids[0] if core_id is None else core_id
    memory = AuthoredMemory(arch, core_id)
    workspace = memory.buffer('workspace', TensorRole.OUTPUT, tensor.dtype, tensor.shape, MemorySpace.HBM, 0,
        storage_class=StorageClass.EXTERNAL, initial_state=StateOrigin.EXTERNAL)
    destination = memory.stage(workspace, 'output', 0, space=MemorySpace.HOST_SHARED)
    tiles = []
    tile_elements = staging_bytes // tensor.dtype.byte_width
    for offset in range(0, elements, tile_elements):
        shape = (min(tile_elements, elements - offset),)
        source = memory.view(workspace, f'workspace:{offset}', offset, shape)
        local = memory.stage(source, f'stage:{offset}', 0)
        target = memory.view(destination, f'output:{offset}', offset, shape)
        tiles.append((offset, source, local, target))
    after = ()
    for offset, source, local, target in tiles:
        memory.copy(f'read:{offset}', source, local, after=after)
        after = (memory.copy(f'publish:{offset}', local, target),)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin('serving', 'bounded-publication', 1))
    variant = builder.variant('publish', 'publish', 'static', records=memory.records(), allocations=memory.allocations,
        external_backings=memory.external_backings, binding_slots=memory.binding_slots)
    stream = variant.stream(core_id, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    for operation in memory.effects:
        stream.kernel_command(operation.op_id)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def attach_prefill_request(program: Program, arch, inputs: tuple[PackedTensorInput, ...], *,
                           input_tokens: int, publish_chunk_bytes: int) -> Program:
    verify_program(program, arch)
    if (type(program.semantics.origin) is not ComposedProgramOrigin or len(program.semantics.variants) != 2 or
            program.required_features != A.REQUIRED_FEATURES):
        raise MeshIrError('E_REQUEST_PROFILE', 'prefill request requires composed compute and publication variants')
    if (type(input_tokens) is not int or input_tokens < 1 or type(publish_chunk_bytes) is not int or
            publish_chunk_bytes < 1 or type(inputs) is not tuple or not inputs or
            any(type(row) is not PackedTensorInput for row in inputs)):
        raise MeshIrError('E_REQUEST_PROFILE', 'prefill request dimensions and packed input records are invalid')
    prefill, publish = program.semantics.variants
    catalog = ProgramBindingCatalog(program)
    source = catalog.for_variant(prefill.entrypoint_id, prefill.profile_id)
    destination = catalog.for_variant(publish.entrypoint_id, publish.profile_id)
    source_inputs = {name: row for name, row in source.items() if row.tensor.role is TensorRole.INPUT}
    outputs = [row for row in source.values() if row.tensor.role is TensorRole.OUTPUT]
    if (len(outputs) != 1 or len({row.symbol for row in inputs}) != len(inputs) or
            set(source_inputs) != {row.symbol for row in inputs} or set(destination) != {'workspace', 'output'}):
        raise MeshIrError('E_BINDING_ROLE', 'prefill request requires exact inputs and one output workspace')
    for row in inputs:
        binding = source_inputs[row.symbol]
        if (binding.object.memory_space is not MemorySpace.HOST_SHARED or
                (binding.tensor.name, binding.tensor.dtype, binding.tensor.shape, binding.object.footprint_bytes) !=
                (row.name, row.dtype, row.shape, row.size)):
            raise MeshIrError('E_BINDING_ROLE', 'packed input differs from its admitted Host tensor', symbol=row.symbol)
    model = tuple(row for row in source.values() if row.is_immutable_model_content)
    if len(source_inputs) + len(outputs) + len(model) != len(source):
        raise MeshIrError('E_BINDING_ROLE', 'prefill request contains unclassified external storage')
    output_bytes = outputs[0].object.footprint_bytes
    input_bytes = max(row.offset + row.size for row in inputs)
    input_capacity = max(row.offset + source_inputs[row.symbol].slot.required_allocation_bytes for row in inputs)
    strings = list(program.strings)
    symbols = {row.value: index for index, row in enumerate(strings, 1)}
    for name in ('request.input', 'request.output', 'request.workspace'):
        if name not in symbols:
            strings.append(StringEntry(name))
            symbols[name] = len(strings)
    request = AgentRequestProfile(program_id=1, profile_id=1, flags=0, reserved0=0, requested_profile_key=0,
        delta_input_tokens=input_tokens, full_input_tokens=input_tokens, expected_cached_tokens=0, output_tokens=0,
        input_binding_bytes=input_capacity, delta_input_dma_bytes=input_bytes, full_input_dma_bytes=input_bytes,
        host_output_bytes=output_bytes, primary_input_symbol_id=symbols['request.input'],
        primary_output_symbol_id=symbols['request.output'], primary_kv_symbol_id=0, source_rank_count=1,
        source_core_map_begin=0, path_mask=1, kv_bytes_per_token=0, publish_chunk_bytes=publish_chunk_bytes)
    instances, members, mappings = [], [], []
    for index, variant in enumerate((prefill, publish), 1):
        computing = index == 1
        instances.append(AgentInstanceProfile(instance_profile_id=index, request_program_id=1, request_profile_id=1,
            path_kind=A.PATH_KIND.INITIAL_PREFILL, phase=A.PHASE.PREFILL if computing else A.PHASE.PUBLISH,
            member_count=1, decode_chunk_tokens=0, mesh_entrypoint_id=variant.entrypoint_id,
            mesh_profile_id=variant.profile_id, valid_tokens_per_member=input_tokens if computing else 0,
            kv_tokens_before=0, local_padded_members=0, local_padded_tokens_per_member=0,
            primary_input_symbol_id=request.primary_input_symbol_id if computing else 0,
            primary_output_symbol_id=0 if computing else request.primary_output_symbol_id, primary_kv_symbol_id=0,
            flags=0, member_binding_first=index - 1, member_binding_count=1,
            host_input_dma_bytes_per_member=0,
            host_output_dma_bytes_per_member=0 if computing else output_bytes,
            kv_read_bytes_per_member=0, kv_write_bytes_per_member=0))
        members.append(AgentInstanceMemberBinding(index, 0, 0,
            min(symbols[row.symbol] for row in inputs) if computing else 0,
            0 if computing else symbols['output'], 0, 0))
        if computing:
            mappings.extend(AgentInstanceTensorBinding(index, 0, AG.BINDING_KIND.HOST_INPUT,
                symbols[row.symbol], request.primary_input_symbol_id, row.offset,
                source_inputs[row.symbol].slot.required_allocation_bytes) for row in inputs)
        else:
            mappings.append(AgentInstanceTensorBinding(index, 0, AG.BINDING_KIND.HOST_OUTPUT,
                symbols['output'], request.primary_output_symbol_id, 0, output_bytes))
        workspace = outputs[0] if computing else destination['workspace']
        mappings.append(AgentInstanceTensorBinding(index, 0, AG.BINDING_KIND.REQUEST_WORKSPACE,
            symbols[workspace.slot.symbol], symbols['request.workspace'], 0, output_bytes))
    requirements = [AgentRequestBindingRequirement(1, 1, kind, flags, symbols[name], 0) for kind, flags, name in (
        (AG.BINDING_KIND.HOST_INPUT, AG.BINDING_FLAGS.READ, 'request.input'),
        (AG.BINDING_KIND.HOST_OUTPUT, AG.BINDING_FLAGS.WRITE, 'request.output'),
        (AG.BINDING_KIND.REQUEST_WORKSPACE, AG.BINDING_FLAGS.READ | AG.BINDING_FLAGS.WRITE, 'request.workspace'))]
    requirements.extend(AgentRequestBindingRequirement(1, 1, AG.BINDING_KIND.WEIGHT_EXTERNAL,
        AG.BINDING_FLAGS.READ | AG.BINDING_FLAGS.PERSISTENT, symbols[row.slot.symbol], 0) for row in model)
    result = replace(program, strings=tuple(strings),
        required_features=program.required_features | A.AGENT_SERVING_V1 | A.PROFILE_SCOPED_EXECUTION_V1,
        profile_stream_ranges=semantic_profile_ranges(program.semantics), agent_request_profiles=(request,),
        agent_instance_profiles=tuple(instances), agent_instance_member_bindings=tuple(members),
        agent_source_core_map=(AgentSourceCoreMap(arch.core_ids[0], 0),),
        agent_instance_tensor_bindings=tuple(sorted(mappings, key=lambda row: (row.instance_profile_id, row.member_ordinal, row.static_symbol_id))),
        agent_request_binding_requirements=tuple(sorted(requirements, key=lambda row: row.symbol_id)))
    reads = ServingInstanceIo(result, request, instances[0]).member_io(0)['intervals'][HOST_LOAD]
    instances[0] = replace(instances[0], host_input_dma_bytes_per_member=sum(end - start for start, end in reads))
    result = apply_request_profile_keys(replace(result, agent_instance_profiles=tuple(instances)))
    verify_program(result, arch)
    return result
