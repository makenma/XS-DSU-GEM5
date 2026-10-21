from mesh_ir.acceptance import ContractError
from mesh_ir.burst_splitter import plan_descriptor
from mesh_ir.generated import abi as A
from mesh_ir.execution_view import build_execution_views
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.scheduled.addressing import descriptor_endpoint_span
from mesh_ir.scheduled.model import EndpointSide


def validate_publish_segments(program, profile_id, instance_id, members, width, observations):
    profile = next((row for row in program.agent_instance_profiles
                    if row.instance_profile_id == profile_id), None)
    if profile is None or profile.phase != A.PHASE.PUBLISH:
        raise ContractError('PUBLISH evidence requires a frozen PUBLISH profile')
    mappings = [row for row in program.agent_instance_member_bindings
                if row.instance_profile_id == profile_id]
    if set(members) != {row.member_ordinal for row in mappings}:
        raise ContractError('PUBLISH member population differs from the frozen profile')
    command_ids = build_execution_views(program).for_instance(profile.mesh_entrypoint_id, profile.mesh_profile_id).command_ids
    catalog = ProgramBindingCatalog(program)
    endpoint_uses = {(row.descriptor_id, row.side): row for row in program.semantics.endpoint_uses}
    plans = {}
    ranges = {}
    prefixes = {}
    for mapping in mappings:
        request, address = members[mapping.member_ordinal]
        slot = catalog.binding(profile.mesh_entrypoint_id, profile.mesh_profile_id,
            program.strings[mapping.static_output_symbol_id - 1].value)
        prefixes[request] = 0
        for descriptor in program.dma_descriptors:
            if descriptor.command_id not in command_ids or descriptor.kind != A.DMA_KIND.STORE:
                continue
            if catalog.descriptor_binding(descriptor.descriptor_id, EndpointSide.DST) != slot:
                continue
            *_, offset, end = descriptor_endpoint_span(program.semantics,
                endpoint_uses[descriptor.descriptor_id, EndpointSide.DST])
            if end > profile.host_output_dma_bytes_per_member:
                raise ContractError('PUBLISH descriptor leaves its admitted output window')
            plan = plan_descriptor(descriptor.row_bytes, descriptor.rows, address + offset,
                                   descriptor.dst_stride_bytes, width, descriptor.max_burst_beats)
            key = descriptor.descriptor_id
            if key in plans:
                raise ContractError('PUBLISH descriptor belongs to more than one member')
            plans[key] = [(burst.logical_start - address, burst.useful_bytes) for burst in plan.bursts]
            ranges[key] = (request, offset, descriptor.useful_bytes)
    inflight = {}
    next_segment = {key: 0 for key in plans}
    stopped = set()
    last_commit = {}
    last_tick = -1
    for fact in observations:
        descriptor = fact['descriptor']
        if descriptor not in plans or fact['instance'] != instance_id or fact['tick'] < last_tick:
            raise ContractError('PUBLISH fact has an unknown descriptor, instance or reversed edge')
        last_tick = fact['tick']
        request, begin, size = ranges[descriptor]
        if fact['request'] != request:
            raise ContractError('PUBLISH fact belongs to another member')
        kind = fact['kind']
        if kind == 3:
            if (next_segment[descriptor] or descriptor in stopped or request in inflight or
                    (fact['offset'], fact['bytes']) != (begin, size)):
                raise ContractError('PUBLISH suppression overlaps issued work')
            stopped.add(descriptor)
            continue
        index = next_segment[descriptor]
        if descriptor in stopped or index >= len(plans[descriptor]):
            raise ContractError('PUBLISH continued after a descriptor terminal')
        if (fact['offset'], fact['bytes']) != plans[descriptor][index]:
            raise ContractError('PUBLISH segment differs from the frozen slot and AXI splitter')
        if kind == 0:
            if (request in inflight or fact['offset'] != prefixes[request] or
                    fact['tick'] <= last_commit.get(request, -1)):
                raise ContractError('PUBLISH issue bypassed its successful predecessor B')
            inflight[request] = (descriptor, fact['offset'], fact['bytes'], fact['tick'])
        elif kind in (1, 2):
            accepted = inflight.get(request)
            if (accepted is None or accepted[:3] != (descriptor, fact['offset'], fact['bytes']) or
                    fact['tick'] < accepted[3]):
                raise ContractError('PUBLISH response has no matching accepted segment')
            del inflight[request]
            next_segment[descriptor] += 1
            last_commit[request] = fact['tick']
            if kind == 1:
                prefixes[request] += fact['bytes']
            else:
                stopped.add(descriptor)
        else:
            raise ContractError('PUBLISH fact has an unknown terminal kind')
    if inflight:
        raise ContractError('PUBLISH accepted segment has no terminal response')
    return prefixes
