from dataclasses import dataclass
from types import MappingProxyType

from mesh_ir.canonical import checked_add_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import agent_abi as AG
from mesh_ir.ir.common import Access, MemorySpace, TensorRole
from mesh_ir.model import AgentInstanceTensorBinding, Program
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog, ProgramObjectBinding


@dataclass(frozen=True)
class ResolvedMemberTensorBinding:
    mapping: AgentInstanceTensorBinding
    binding: ProgramObjectBinding


class ServingTensorBindingCatalog:
    def __init__(self, program: Program):
        self.slots = slots = ProgramBindingCatalog(program)
        instances = {row.instance_profile_id: row for row in program.agent_instance_profiles}
        requests = {(row.program_id, row.profile_id): row for row in program.agent_request_profiles}
        if len(instances) != len(program.agent_instance_profiles) or len(requests) != len(program.agent_request_profiles):
            raise MeshIrError('E_ABI_DUPLICATE', 'tensor mapping profile identity is duplicated')
        roles = {
            AG.BINDING_KIND.HOST_INPUT: ((TensorRole.INPUT,), 'primary_input_symbol_id', 'static_input_symbol_id'),
            AG.BINDING_KIND.HOST_OUTPUT: ((TensorRole.OUTPUT,), 'primary_output_symbol_id', 'static_output_symbol_id'),
            AG.BINDING_KIND.KV_EXTERNAL: ((TensorRole.KV_CACHE,), 'primary_kv_symbol_id', 'static_kv_symbol_id'),
        }
        roles[AG.BINDING_KIND.REQUEST_WORKSPACE] = ((TensorRole.OUTPUT, TensorRole.ACTIVATION, TensorRole.STATE), None, None)
        workspace_sizes = {key: 0 for key in requests}
        for key in requests:
            workspaces = [item for item in program.agent_request_binding_requirements
                if (item.request_program_id, item.request_profile_id) == key and item.binding_kind == AG.BINDING_KIND.REQUEST_WORKSPACE]
            if len(workspaces) > 1 or any(item.binding_flags != AG.BINDING_FLAGS.READ | AG.BINDING_FLAGS.WRITE for item in workspaces):
                raise MeshIrError('E_BINDING_ROLE', 'workspace requires one ephemeral request range')
        by_instance = {identity: [] for identity in instances}
        order = []
        occupied = set()
        for row in program.agent_instance_tensor_bindings:
            instance = instances.get(row.instance_profile_id)
            if instance is None or not 0 <= row.member_ordinal < instance.member_count:
                raise MeshIrError('E_BINDING_ROLE', 'tensor mapping has no instance member')
            request_key = (instance.request_program_id, instance.request_profile_id)
            request = requests.get(request_key)
            if request is None or row.binding_kind not in roles:
                raise MeshIrError('E_BINDING_ROLE', 'tensor mapping has no request role')
            role, primary_field, _ = roles[row.binding_kind]
            if (not row.request_symbol_id or (primary_field is not None and
                    (row.request_symbol_id != getattr(request, primary_field) or
                     row.request_symbol_id != getattr(instance, primary_field)))):
                raise MeshIrError('E_BINDING_ROLE', 'tensor mapping request root differs from its member role')
            requirements = tuple(item for item in program.agent_request_binding_requirements
                if (item.request_program_id, item.request_profile_id, item.symbol_id) == (*request_key, row.request_symbol_id))
            if len(requirements) != 1 or requirements[0].binding_kind != row.binding_kind:
                raise MeshIrError('E_BINDING_ROLE', 'tensor mapping root has no unique request requirement')
            if not 1 <= row.static_symbol_id <= len(program.strings):
                raise MeshIrError('E_BINDING_ROLE', 'tensor mapping static symbol is missing')
            binding = slots.binding(instance.mesh_entrypoint_id, instance.mesh_profile_id,
                                    program.strings[row.static_symbol_id - 1].value)
            identity = (instance.instance_profile_id, binding.slot.slot_id)
            if identity in occupied:
                raise MeshIrError('E_BINDING_ROLE', 'member tensor slots alias within the instance')
            occupied.add(identity)
            if binding.tensor.role not in role:
                raise MeshIrError('E_BINDING_ROLE', 'tensor mapping role differs from its static slot')
            end = checked_add_u64(row.offset_bytes, row.span_bytes, 'member tensor subrange')
            if row.binding_kind == AG.BINDING_KIND.REQUEST_WORKSPACE:
                if (binding.object.memory_space is not MemorySpace.HBM or binding.slot.access is not Access.READ_WRITE or
                        binding.slot.reference_binding.access is not Access.READ_WRITE):
                    raise MeshIrError('E_BINDING_ROLE', 'workspace must bind mutable HBM storage')
                workspace_sizes[request_key] = max(workspace_sizes[request_key], end)
            if row.offset_bytes % binding.slot.required_allocation_alignment_bytes or row.span_bytes < binding.slot.required_allocation_bytes:
                raise MeshIrError('E_BINDING_ROLE', 'tensor mapping subrange violates slot alignment or capacity')
            capacity = {AG.BINDING_KIND.HOST_INPUT: request.input_binding_bytes,
                        AG.BINDING_KIND.HOST_OUTPUT: request.host_output_bytes}.get(row.binding_kind)
            if capacity is not None and end > capacity:
                raise MeshIrError('E_BINDING_ROLE', 'tensor mapping exceeds its request group')
            if row.binding_kind == AG.BINDING_KIND.KV_EXTERNAL and row.offset_bytes:
                raise MeshIrError('E_BINDING_ROLE', 'KV tensor mapping must start at its owned slot')
            order.append((row.instance_profile_id, row.member_ordinal, row.static_symbol_id))
            by_instance[row.instance_profile_id].append(ResolvedMemberTensorBinding(row, binding))
        if order != sorted(order):
            raise MeshIrError('E_ABI_ORDER', 'tensor mappings must be ordered by instance, member and static symbol')
        for identity, instance in instances.items():
            rows = by_instance[identity]
            request_key = (instance.request_program_id, instance.request_profile_id)
            immutable = {program.strings[item.symbol_id - 1].value
                for item in program.agent_request_binding_requirements
                if (item.request_program_id, item.request_profile_id) == request_key and
                item.binding_kind == AG.BINDING_KIND.WEIGHT_EXTERNAL and 1 <= item.symbol_id <= len(program.strings)}
            selected = slots.for_variant(instance.mesh_entrypoint_id, instance.mesh_profile_id)
            if any(not selected[symbol].is_immutable_model_content for symbol in immutable & set(selected)):
                raise MeshIrError('E_BINDING_ROLE', 'persistent model requirement has no admitted immutable HBM content')
            if {item.binding.slot.symbol for item in rows} != set(selected) - immutable:
                raise MeshIrError('E_BINDING_ROLE', 'tensor mappings do not exactly cover mutable and input variant slots')
            first, count = instance.member_binding_first, instance.member_binding_count
            members = program.agent_instance_member_bindings[first:first + count]
            if len(members) != instance.member_count:
                raise MeshIrError('E_BINDING_ROLE', 'tensor mapping member summaries are incomplete')
            for ordinal, member in enumerate(members):
                if member.instance_profile_id != identity or member.member_ordinal != ordinal:
                    raise MeshIrError('E_BINDING_ROLE', 'tensor mapping member summary has a foreign identity')
                for kind, (_, _, field) in roles.items():
                    mapped = [item.mapping for item in rows if item.mapping.member_ordinal == ordinal and item.mapping.binding_kind == kind]
                    if field is not None and getattr(member, field) != min((item.static_symbol_id for item in mapped), default=0):
                        raise MeshIrError('E_BINDING_ROLE', 'member primary summary differs from its tensor mappings')
                    intervals = sorted((item.offset_bytes, item.offset_bytes + item.span_bytes) for item in mapped)
                    if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
                        raise MeshIrError('E_BINDING_ROLE', 'member tensor subranges overlap')
        self.workspace_sizes = MappingProxyType(workspace_sizes)
        self._instances = MappingProxyType({identity: tuple(rows) for identity, rows in by_instance.items()})

    def for_instance(self, instance_profile_id: int) -> tuple[ResolvedMemberTensorBinding, ...]:
        rows = self._instances.get(instance_profile_id)
        if rows is None:
            raise MeshIrError('E_BINDING_ROLE', 'tensor mapping catalog has no selected instance')
        return rows
