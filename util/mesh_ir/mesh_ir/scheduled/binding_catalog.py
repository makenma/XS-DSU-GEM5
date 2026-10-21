from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.kernel_ir import BufferObject, KernelTensor
from mesh_ir.model import Program
from mesh_ir.scheduled.model import EndpointSide, ExternalSlotBacking
from mesh_ir.scheduled.addressing import descriptor_endpoint_span
from mesh_ir.traffic import BindingSlot


@dataclass(frozen=True)
class ProgramObjectBinding:
    slot: BindingSlot
    object: BufferObject
    tensor: KernelTensor

    @property
    def is_immutable_model_content(self) -> bool:
        return (self.tensor.role in (TensorRole.WEIGHT, TensorRole.CONSTANT, TensorRole.STATE) and
            self.tensor.storage_class is StorageClass.EXTERNAL and self.tensor.access is Access.READ_ONLY and
            bool(self.tensor.content_sha256) and self.object.memory_space is MemorySpace.HBM and
            self.slot.memory_space is MemorySpace.HBM and self.slot.access is Access.READ_ONLY and
            self.slot.reference_binding.access is Access.READ_ONLY)


class ProgramBindingCatalog:
    def __init__(self, program: Program):
        semantics = program.semantics
        objects = {item.object_id: item for item in semantics.objects}
        tensors = {item.tensor_id: item for item in semantics.kernel_tensors}
        slots = {item.slot_id: item for item in semantics.binding_slots}
        bindings = {}
        by_object = {}
        self._facts = semantics
        endpoint_uses = {}
        for item in semantics.endpoint_uses:
            key = (item.descriptor_id, item.side)
            if key in endpoint_uses:
                raise MeshIrError("E_DMA_RANGE", "descriptor endpoint is duplicated", descriptor_id=item.descriptor_id)
            endpoint_uses[key] = item
        self._endpoint_uses = MappingProxyType(endpoint_uses)
        for variant in semantics.variants:
            membership = variant.membership
            owned_slots = range(membership.binding_slots.first_id, membership.binding_slots.first_id + membership.binding_slots.count)
            owned_objects = range(membership.objects.first_id, membership.objects.first_id + membership.objects.count)
            owned_tensors = range(membership.kernel_tensors.first_id, membership.kernel_tensors.first_id + membership.kernel_tensors.count)
            covered = set()
            for backing in semantics.object_backings:
                if backing.object_id not in owned_objects or type(backing.backing) is not ExternalSlotBacking:
                    continue
                slot_id = backing.backing.slot_id
                if slot_id not in owned_slots or slot_id not in slots or slot_id in covered:
                    raise MeshIrError('E_RELOCATION', 'external binding has no unique slot in its variant', object_id=backing.object_id, slot_id=slot_id)
                obj = objects.get(backing.object_id)
                tensor = tensors.get(obj.storage_tensor_id) if obj is not None else None
                if tensor is None or tensor.tensor_id not in owned_tensors:
                    raise MeshIrError('E_RELOCATION', 'external binding tensor escapes its variant', object_id=backing.object_id)
                slot = slots[slot_id]
                key = (variant.entrypoint_id, variant.profile_id, slot.symbol)
                if key in bindings:
                    raise MeshIrError('E_RELOCATION', 'binding symbol is ambiguous in its variant', symbol=slot.symbol)
                bindings[key] = ProgramObjectBinding(slot, obj, tensor)
                by_object[obj.object_id] = bindings[key]
                covered.add(slot_id)
            if covered != set(owned_slots):
                raise MeshIrError('E_RELOCATION', 'variant binding slots do not have exact object ownership', variant_id=variant.variant_id)
        self._variants = MappingProxyType({
            (variant.entrypoint_id, variant.profile_id): MappingProxyType({
                symbol: item for (entrypoint, profile, symbol), item in bindings.items()
                if (entrypoint, profile) == (variant.entrypoint_id, variant.profile_id)})
            for variant in semantics.variants})
        self._by_object = MappingProxyType(by_object)

    def for_variant(self, entrypoint_id: int, profile_id: int) -> Mapping[str, ProgramObjectBinding]:
        result = self._variants.get((entrypoint_id, profile_id))
        if result is None:
            raise MeshIrError('E_RELOCATION', 'binding catalog has no selected variant', entrypoint_id=entrypoint_id, profile_id=profile_id)
        return result

    def binding(self, entrypoint_id: int, profile_id: int, symbol: str) -> ProgramObjectBinding:
        result = self.for_variant(entrypoint_id, profile_id).get(symbol)
        if result is None:
            raise MeshIrError('E_RELOCATION', 'binding symbol has no owner in the selected variant', entrypoint_id=entrypoint_id, profile_id=profile_id, symbol=symbol)
        return result

    def descriptor_binding(self, descriptor_id: int, side: EndpointSide) -> ProgramObjectBinding | None:
        use = self._endpoint_uses.get((descriptor_id, side))
        if use is None:
            raise MeshIrError("E_DMA_RANGE", "descriptor endpoint has no admitted access", descriptor_id=descriptor_id)
        obj = descriptor_endpoint_span(self._facts, use)[3]
        return self._by_object.get(obj.object_id)
