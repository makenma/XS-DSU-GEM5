from dataclasses import replace

from mesh_ir.generated import agent_abi as AG
from mesh_ir.ir.common import Access, TensorRole


def build_workspace_program(arch=None, *, overwrite_phase=None, partial=False, output_rows=4, publish_rows=4,
                            model_role=TensorRole.WEIGHT, model_access=Access.READ_ONLY, model_has_content=True):
    from pathlib import Path
    from mesh_ir.architecture import load_arch
    from mesh_ir.authoring import AuthoredMemory
    from mesh_ir.builder import ProgramBuilder
    from mesh_ir.generated import abi as A
    from mesh_ir.ir.common import DType, MemorySpace, StorageClass
    from mesh_ir.ir.kernel_ir import StateOrigin
    from mesh_ir.ir.kernel_ir import ElementRegion
    from mesh_ir.model import AgentInstanceMemberBinding, AgentInstanceProfile, AgentInstanceTensorBinding, AgentRequestBindingRequirement, AgentRequestProfile, AgentSourceCoreMap, StringEntry
    from mesh_ir.moe_programs import synthetic_weight_digest
    from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
    from mesh_ir.scheduled.model import AuthoredProgramOrigin, HaltAttrs, RequestBeginAttrs, RequestEndAttrs
    from mesh_ir.scheduled.verify import verify_program
    from mesh_ir.execution_view import semantic_profile_ranges
    from mesh_ir.serving_profiles import apply_request_profile_keys
    from tests.golden.support.records import record

    if type(output_rows) is not int or output_rows < 1:
        raise ValueError('output_rows must be positive')
    if type(publish_rows) is not int or not 1 <= publish_rows <= output_rows:
        raise ValueError('publish_rows must be within the output row count')
    root = Path(__file__).resolve().parents[5]
    arch = arch or load_arch(root / 'configs/example/ai_mesh/arch/mesh_1x2.yaml')
    builder = ProgramBuilder(arch, AuthoredProgramOrigin('serving', 'request-workspace', 1))
    for phase in ('prefill', 'publish'):
        memory = AuthoredMemory(arch, arch.core_ids[0])
        if phase == 'prefill':
            source = memory.buffer('input', TensorRole.INPUT, DType.FP16, (output_rows, 8), MemorySpace.HOST_SHARED, 0)
            local = memory.stage(source, 'input-stage', 0)
            weight = memory.buffer('weight', model_role, DType.FP16, (8, 8), MemorySpace.HBM, 0x1000,
                access=model_access, content_sha256=synthetic_weight_digest().hex() if model_has_content else None,
                initial_state=StateOrigin.EXTERNAL)
            weights = memory.stage(weight, 'weight-stage', 0x1000)
            output = memory.buffer('computed', TensorRole.OUTPUT, DType.FP16, (output_rows, 8), MemorySpace.CORE_SRAM, 0x2000)
            workspace = memory.stage(output, 'workspace', 0x2000, space=MemorySpace.HBM)
            memory.copy('input', source, local)
            memory.copy('weight', weight, weights)
            memory.matmul('compute', local, weights, output)
            if overwrite_phase == phase:
                memory.fill('overwrite', output, b'\x01' * 8,
                    region=ElementRegion((0, 0), (2, 8), (1, 1)) if partial else None)
            memory.copy('save', output, workspace)
        else:
            workspace = memory.buffer('workspace', TensorRole.OUTPUT, DType.FP16, (output_rows, 8), MemorySpace.HBM, 0x2000,
                storage_class=StorageClass.EXTERNAL, initial_state=StateOrigin.EXTERNAL)
            output = memory.stage(workspace, 'output', 0x1000, space=MemorySpace.HOST_SHARED)
            tiles = []
            for offset in range(0, output_rows, publish_rows):
                shape = (min(publish_rows, output_rows - offset), 8)
                source = memory.view(workspace, f'workspace-tile:{offset}', offset * 8, shape)
                local = memory.stage(source, f'publish-stage:{offset}', 0)
                destination = memory.view(output, f'output-tile:{offset}', offset * 8, shape)
                tiles.append((offset, shape, source, local, destination))
            after = ()
            for offset, shape, source, local, destination in tiles:
                memory.copy(f'restore:{offset}', source, local, after=after)
                if overwrite_phase == phase:
                    memory.fill(f'overwrite:{offset}', local, b'\x02' * 8,
                        region=ElementRegion((0, 0), (min(2, shape[0]), 8), (1, 1)) if partial else None)
                after = (memory.copy(f'publish:{offset}', local, destination),)
        records = memory.records()
        variant = builder.variant(phase, phase, phase, records=records, allocations=memory.allocations,
            external_backings=memory.external_backings, binding_slots=memory.binding_slots)
        stream = variant.stream(arch.core_ids[0], 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
        stream.control_command(RequestBeginAttrs())
        for operation in memory.effects:
            stream.kernel_command(operation.op_id)
        stream.control_command(RequestEndAttrs())
        stream.control_command(HaltAttrs())
    program = builder.build()
    verify_program(program, arch)
    strings = (*program.strings, *(StringEntry('request.' + role) for role in ('input', 'output', 'workspace')))
    symbols = {row.value: index for index, row in enumerate(strings, 1)}
    request = record(AgentRequestProfile, program_id=1, profile_id=1, path_mask=1,
        input_binding_bytes=output_rows * 16, full_input_dma_bytes=output_rows * 16, delta_input_dma_bytes=output_rows * 16,
        host_output_bytes=output_rows * 16, publish_chunk_bytes=publish_rows * 16,
        full_input_tokens=output_rows, delta_input_tokens=output_rows,
        source_rank_count=1, primary_input_symbol_id=symbols['request.input'], primary_output_symbol_id=symbols['request.output'])
    instances, members, mappings = [], [], []
    catalog = ProgramBindingCatalog(program)
    for index, variant in enumerate(program.semantics.variants, 1):
        prefill = index == 1
        instances.append(record(AgentInstanceProfile, instance_profile_id=index, request_program_id=1,
            request_profile_id=1, phase=A.PHASE.PREFILL if prefill else A.PHASE.PUBLISH,
            member_count=1, member_binding_count=1, member_binding_first=index - 1,
            valid_tokens_per_member=output_rows if prefill else 0, mesh_entrypoint_id=variant.entrypoint_id,
            mesh_profile_id=variant.profile_id, primary_input_symbol_id=request.primary_input_symbol_id if prefill else 0,
            primary_output_symbol_id=0 if prefill else request.primary_output_symbol_id,
            host_input_dma_bytes_per_member=output_rows * 16 if prefill else 0,
            host_output_dma_bytes_per_member=0 if prefill else output_rows * 16))
        members.append(AgentInstanceMemberBinding(index, 0, 0, symbols['input'] if prefill else 0,
            0 if prefill else symbols['output'], 0, 0))
        for name, kind in (('workspace', AG.BINDING_KIND.REQUEST_WORKSPACE),
                ('input' if prefill else 'output', AG.BINDING_KIND.HOST_INPUT if prefill else AG.BINDING_KIND.HOST_OUTPUT)):
            binding = catalog.binding(variant.entrypoint_id, variant.profile_id, name)
            mappings.append(AgentInstanceTensorBinding(index, 0, kind, symbols[name], symbols['request.' + name],
                0, binding.slot.required_allocation_bytes))
    requirements = tuple(AgentRequestBindingRequirement(1, 1, kind, flags, symbols[name]) for kind, flags, name in (
        (AG.BINDING_KIND.HOST_INPUT, AG.BINDING_FLAGS.READ, 'request.input'),
        (AG.BINDING_KIND.HOST_OUTPUT, AG.BINDING_FLAGS.WRITE, 'request.output'),
        (AG.BINDING_KIND.REQUEST_WORKSPACE, AG.BINDING_FLAGS.READ | AG.BINDING_FLAGS.WRITE, 'request.workspace'),
        (AG.BINDING_KIND.WEIGHT_EXTERNAL, AG.BINDING_FLAGS.READ | AG.BINDING_FLAGS.PERSISTENT, 'weight')))
    program = replace(program, strings=strings, agent_request_profiles=(request,), agent_instance_profiles=tuple(instances),
        required_features=program.required_features | A.AGENT_SERVING_V1 | A.PROFILE_SCOPED_EXECUTION_V1,
        profile_stream_ranges=semantic_profile_ranges(program.semantics),
        agent_instance_member_bindings=tuple(members), agent_source_core_map=(AgentSourceCoreMap(arch.core_ids[0], 0),),
        agent_instance_tensor_bindings=tuple(sorted(mappings, key=lambda row: (row.instance_profile_id, row.member_ordinal, row.static_symbol_id))),
        agent_request_binding_requirements=tuple(sorted(requirements, key=lambda row: row.symbol_id)))
    return apply_request_profile_keys(program), arch


def workspace_workload_document(program):
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[5]
    document = json.loads((root / 'tests/gem5/ai_mesh/fixtures/gate6/serving_workload_plan.json').read_text())
    profile, = program.agent_request_profiles
    task = document['users'][0]['tasks'][0]
    task.update(kv_handle=0, initial_kv_generation=0)
    round_, = task['rounds']
    round_.update(prompt_tokens=profile.full_input_tokens, prompt_bytes=profile.input_binding_bytes,
        full_context_tokens=profile.full_input_tokens, full_context_bytes=profile.input_binding_bytes,
        output_tokens=0, generated_code_bytes=profile.host_output_bytes, output_capacity_bytes=profile.host_output_bytes,
        kv_required_tokens_after_round=0, requested_profile_key=hex(profile.requested_profile_key))
    round_['compile']['local_io']['read_bytes'] = max(round_['compile']['local_io']['read_bytes'], profile.host_output_bytes)
    return document
