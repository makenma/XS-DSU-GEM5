from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum

from mesh_ir.architecture import ArchManifest, InitiatorEndpoint, TargetEndpoint, validate_arch
from mesh_ir.canonical import canonical_json_bytes, checked_add_u64, checked_mul_u64, checked_u64, semantic_sha256, to_canonical
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, DmaKind, INVALID_CORE_ID, MemorySpace, TensorRole
from mesh_ir.burst_splitter import plan_descriptor
from mesh_ir.generated.semantic_enums import AxiChannel, TrafficAggregateLevel, TrafficDirection

@dataclass(frozen=True)
class SlotAddress:
    slot_id: int


@dataclass(frozen=True)
class DirectAddress:
    offset_bytes: int
    backing_offset_bytes: int
    backing_size_bytes: int
    backing_alignment_bytes: int
    backing_access: Access


@dataclass(frozen=True)
class AddressRef:
    ref_id: int
    memory_space: MemorySpace
    region_id: int
    owner_core: int
    origin: SlotAddress | DirectAddress
    endpoint_addend_bytes: int
    logical_span_bytes: int
    storage_span_bytes: int
    endpoint_alignment_bytes: int
    access: Access


@dataclass(frozen=True)
class Binding:
    slot_id: int
    region_id: int
    owner_core: int
    allocation_offset_bytes: int
    allocation_size_bytes: int
    allocation_alignment_bytes: int
    access: Access


@dataclass(frozen=True)
class BindingSlot:
    slot_id: int
    symbol: str
    memory_space: MemorySpace
    region_id: int
    owner_core: int
    required_allocation_bytes: int
    required_allocation_alignment_bytes: int
    access: Access
    reference_binding: Binding


@dataclass(frozen=True)
class ResolvedBinding:
    slot_id: int
    symbol: str
    memory_space: MemorySpace
    region_id: int
    owner_core: int
    allocation_offset_bytes: int
    allocation_address: int
    allocation_size_bytes: int
    allocation_alignment_bytes: int
    access: Access


@dataclass(frozen=True)
class ResolvedAddress:
    ref_id: int
    memory_space: MemorySpace
    region_id: int
    owner_core: int
    origin: SlotAddress | DirectAddress
    region_offset_bytes: int
    address: int
    endpoint_addend_bytes: int
    logical_span_bytes: int
    storage_span_bytes: int
    endpoint_alignment_bytes: int
    access: Access
    backing_offset_bytes: int
    backing_address: int
    backing_size_bytes: int
    backing_alignment_bytes: int
    backing_access: Access
    physical_address: int
    physical_span_bytes: int
    target_endpoint: str
    target_node: int
    target_router: int


@dataclass(frozen=True)
class ResolvedBindings:
    bindings: tuple[ResolvedBinding, ...]
    addresses: tuple[ResolvedAddress, ...]
    identity_sha256: str


@dataclass(frozen=True)
class DescriptorIdentity:
    entrypoint_id: int
    profile_id: int
    command_id: int
    descriptor_id: int
    issuing_core: int
    peer_core: int
    tensor_id: int
    tensor_role: TensorRole
    direction: TrafficDirection


@dataclass(frozen=True)
class ResolvedDescriptor:
    identity: DescriptorIdentity
    kind: DmaKind
    src: ResolvedAddress
    dst: ResolvedAddress
    rows: int
    row_bytes: int
    src_stride_bytes: int
    dst_stride_bytes: int
    useful_bytes: int
    physical_storage_bytes: int
    max_burst_beats: int


@dataclass(frozen=True)
class DescriptorExecution:
    descriptor: ResolvedDescriptor
    execution_count: int


@dataclass(frozen=True)
class ChannelTraffic:
    channel: AxiChannel
    vnet: int
    source_endpoint: str
    destination_endpoint: str
    source_node: int
    source_port: int
    destination_node: int
    destination_port: int
    source_router: int
    destination_router: int
    messages: int
    packets: int
    flits: int
    wire_bytes: int


@dataclass(frozen=True)
class TrafficChannelTotal:
    channel: AxiChannel
    vnet: int
    messages: int
    packets: int
    flits: int
    wire_bytes: int


@dataclass(frozen=True)
class DescriptorTraffic:
    identity: DescriptorIdentity
    execution_count: int
    src_memory_space: MemorySpace
    dst_memory_space: MemorySpace
    src_endpoint: str
    dst_endpoint: str
    src_router: int
    dst_router: int
    src_address: int
    dst_address: int
    remote_address: int
    rows: int
    row_bytes: int
    remote_stride_bytes: int
    max_burst_beats: int
    hops: int
    useful_bytes: int
    physical_beat_bytes: int
    segments: int
    bursts: int
    channels: tuple[ChannelTraffic, ...]
    packets: int
    flits: int
    wire_bytes: int
    hop_wire_bytes: int


@dataclass(frozen=True)
class TrafficAggregateKey:
    level: TrafficAggregateLevel
    entrypoint_id: int | None
    profile_id: int | None
    core_id: int | None
    peer_core: int | None
    memory_space: MemorySpace | None
    tensor_role: TensorRole | None
    direction: TrafficDirection | None


@dataclass(frozen=True)
class TrafficAggregate:
    key: TrafficAggregateKey
    useful_bytes: int
    physical_beat_bytes: int
    static_descriptors: int
    descriptor_executions: int
    segments: int
    bursts: int
    channels: tuple[TrafficChannelTotal, ...]
    packets: int
    flits: int
    wire_bytes: int
    hop_wire_bytes: int


@dataclass(frozen=True)
class TrafficReport:
    binding_identity_sha256: str
    descriptors: tuple[DescriptorTraffic, ...]
    aggregates: tuple[TrafficAggregate, ...]
    semantic_sha256: str

    def canonical_dict(self) -> dict[str, object]:
        from mesh_ir.abi.semantic import semantic_to_canonical

        return semantic_to_canonical(self)


def _power_of_two(value: int) -> bool:
    return type(value) is int and value > 0 and value & (value - 1) == 0


def _sum_u64(values, field: str) -> int:
    total = 0
    for value in values:
        total = checked_add_u64(total, value, field)
    return total


def _validate_stable_identity(identity: DescriptorIdentity) -> None:
    if type(identity) is not DescriptorIdentity:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor identity record type is invalid")
    for field, value in (
        ("entrypoint_id", identity.entrypoint_id),
        ("profile_id", identity.profile_id),
        ("command_id", identity.command_id),
        ("descriptor_id", identity.descriptor_id),
        ("tensor_id", identity.tensor_id),
    ):
        if type(value) is not int or not 1 <= value <= 0xFFFFFFFF:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor stable identity is outside positive u32", field=field, value=value)
    if type(identity.issuing_core) is not int or type(identity.peer_core) is not int:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor core identity type is invalid")
    if type(identity.tensor_role) is not TensorRole or type(identity.direction) is not TrafficDirection:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor identity enum type is invalid")


def _validate_positive_u32(value: int, field: str, code: str) -> None:
    if type(value) is not int or not 1 <= value <= 0xFFFFFFFF:
        raise MeshIrError(code, "identity is outside positive u32", field=field, value=value)


def _validate_address_origin(origin: SlotAddress | DirectAddress, ref_id: int, code: str) -> None:
    if type(origin) is SlotAddress:
        _validate_positive_u32(origin.slot_id, "slot_id", code)
        return
    if type(origin) is not DirectAddress:
        raise MeshIrError(code, "address origin type is invalid", ref_id=ref_id)
    for field, value in (
        ("offset_bytes", origin.offset_bytes),
        ("backing_offset_bytes", origin.backing_offset_bytes),
        ("backing_size_bytes", origin.backing_size_bytes),
        ("backing_alignment_bytes", origin.backing_alignment_bytes),
    ):
        checked_u64(value, field)
    if type(origin.backing_access) is not Access or not _power_of_two(origin.backing_alignment_bytes):
        raise MeshIrError(code, "direct backing type or alignment is invalid", ref_id=ref_id)


def _region_base(arch: ArchManifest, region_id: int, owner_core: int) -> tuple[int, int]:
    if type(region_id) is not int or not 0 <= region_id < len(arch.regions):
        raise MeshIrError("E_RELOCATION", "address references an unknown region", region_id=region_id)
    region = arch.regions[region_id]
    if region.kind == "CORE_SRAM_APERTURE":
        if type(owner_core) is not int or owner_core not in arch.core_ids:
            raise MeshIrError("E_RELOCATION", "SRAM address references an unknown core", owner_core=owner_core)
        displacement = checked_mul_u64(owner_core, region.tile_stride, "core aperture displacement")
        return checked_add_u64(region.base, displacement, "core aperture base"), region.tile_bytes
    if owner_core != INVALID_CORE_ID:
        raise MeshIrError("E_RELOCATION", "non-per-core region requires the invalid owner identity", owner_core=owner_core)
    return region.base, region.bytes


def _validate_axi_address_interval(arch: ArchManifest, begin: int, size: int) -> None:
    limit = 1 << arch.axi_address_bits
    if begin >= limit or size and checked_add_u64(begin, size - 1, "AXI address interval end") >= limit:
        raise MeshIrError("E_RELOCATION", "resolved interval exceeds the declared AXI address width", address=begin, size=size)


def _memory_space(arch: ArchManifest, region_id: int, owner_core: int, issuing_core: int) -> MemorySpace:
    region = arch.regions[region_id]
    if region.kind == "HBM":
        return MemorySpace.HBM
    if region.kind == "HOST_SHARED":
        return MemorySpace.HOST_SHARED
    return MemorySpace.CORE_SRAM if owner_core == issuing_core else MemorySpace.PEER_SRAM


def _target_for_range(
    arch: ArchManifest,
    region_id: int,
    owner_core: int,
    begin: int,
    size: int,
    access: Access,
) -> TargetEndpoint:
    end = checked_add_u64(begin, size, "target lookup end")
    matches = []
    for target in arch.fabric.targets:
        for address_range in target.ranges:
            range_end = checked_add_u64(address_range.offset_bytes, address_range.size_bytes, "target range end")
            permission = access == Access.READ_ONLY or address_range.access == Access.READ_WRITE
            point_match = size == 0 and address_range.offset_bytes <= begin < range_end
            interval_match = size > 0 and address_range.offset_bytes <= begin and end <= range_end
            if address_range.region_id == region_id and address_range.owner_core == owner_core and permission and (point_match or interval_match):
                matches.append(target)
    if len(matches) != 1:
        raise MeshIrError("E_RELOCATION", "address does not resolve to exactly one permitted target", region_id=region_id, owner_core=owner_core)
    return matches[0]


def _validate_binding(arch: ArchManifest, slot: BindingSlot, binding: Binding) -> ResolvedBinding:
    if not isinstance(binding, Binding):
        raise MeshIrError("E_RELOCATION", "binding record type is invalid", slot_id=slot.slot_id)
    for field, value in (
        ("slot_id", binding.slot_id),
        ("region_id", binding.region_id),
        ("owner_core", binding.owner_core),
        ("allocation_offset_bytes", binding.allocation_offset_bytes),
        ("allocation_size_bytes", binding.allocation_size_bytes),
        ("allocation_alignment_bytes", binding.allocation_alignment_bytes),
    ):
        checked_u64(value, field)
    if binding.slot_id != slot.slot_id or binding.region_id != slot.region_id or binding.owner_core != slot.owner_core or binding.access != slot.access:
        raise MeshIrError("E_RELOCATION", "binding overrides an immutable slot constraint", slot_id=slot.slot_id)
    if type(binding.access) is not Access or not _power_of_two(binding.allocation_alignment_bytes):
        raise MeshIrError("E_RELOCATION", "binding access or alignment is invalid", slot_id=slot.slot_id)
    if binding.allocation_size_bytes < slot.required_allocation_bytes or binding.allocation_alignment_bytes < slot.required_allocation_alignment_bytes or binding.allocation_alignment_bytes % slot.required_allocation_alignment_bytes:
        raise MeshIrError("E_RELOCATION", "binding does not satisfy slot size or alignment", slot_id=slot.slot_id)
    region_base, region_limit = _region_base(arch, binding.region_id, binding.owner_core)
    allocation_address = checked_add_u64(region_base, binding.allocation_offset_bytes, "binding allocation address")
    _validate_axi_address_interval(arch, allocation_address, binding.allocation_size_bytes)
    if allocation_address % binding.allocation_alignment_bytes:
        raise MeshIrError("E_RELOCATION", "binding allocation base violates its alignment", slot_id=slot.slot_id)
    if binding.allocation_offset_bytes > region_limit - binding.allocation_size_bytes:
        raise MeshIrError("E_RELOCATION", "binding allocation exceeds its region", slot_id=slot.slot_id)
    return ResolvedBinding(
        binding.slot_id,
        slot.symbol,
        slot.memory_space,
        binding.region_id,
        binding.owner_core,
        binding.allocation_offset_bytes,
        allocation_address,
        binding.allocation_size_bytes,
        binding.allocation_alignment_bytes,
        binding.access,
    )


def _validate_binding_overlaps(bindings: tuple[ResolvedBinding, ...], label: str) -> None:
    for index, left in enumerate(bindings):
        left_end = checked_add_u64(left.allocation_address, left.allocation_size_bytes, "binding allocation end")
        for right in bindings[index + 1 :]:
            if left.region_id != right.region_id or left.owner_core != right.owner_core:
                continue
            right_end = checked_add_u64(right.allocation_address, right.allocation_size_bytes, "binding allocation end")
            if max(left.allocation_address, right.allocation_address) < min(left_end, right_end) and (left.access == Access.READ_WRITE or right.access == Access.READ_WRITE):
                raise MeshIrError("E_RELOCATION", f"{label} writable bindings overlap", first=left.slot_id, second=right.slot_id)


def _resolve_address(
    arch: ArchManifest,
    ref: AddressRef,
    slots_by_id: dict[int, BindingSlot],
    resolved_by_slot: dict[int, ResolvedBinding],
    issuing_core: int,
) -> ResolvedAddress:
    _validate_positive_u32(ref.ref_id, "ref_id", "E_RELOCATION")
    _validate_address_origin(ref.origin, ref.ref_id, "E_RELOCATION")
    for field, value in (
        ("region_id", ref.region_id),
        ("owner_core", ref.owner_core),
        ("endpoint_addend_bytes", ref.endpoint_addend_bytes),
        ("logical_span_bytes", ref.logical_span_bytes),
        ("storage_span_bytes", ref.storage_span_bytes),
        ("endpoint_alignment_bytes", ref.endpoint_alignment_bytes),
    ):
        checked_u64(value, field)
    if type(ref.memory_space) is not MemorySpace or type(ref.access) is not Access or not _power_of_two(ref.endpoint_alignment_bytes):
        raise MeshIrError("E_RELOCATION", "address ref type or alignment is invalid", ref_id=ref.ref_id)
    if ref.storage_span_bytes < ref.logical_span_bytes:
        raise MeshIrError("E_RELOCATION", "address storage span is smaller than logical span", ref_id=ref.ref_id)
    region_base, region_limit = _region_base(arch, ref.region_id, ref.owner_core)
    expected_space = _memory_space(arch, ref.region_id, ref.owner_core, issuing_core)
    if ref.memory_space != expected_space:
        raise MeshIrError("E_RELOCATION", "address memory-space assertion is false", ref_id=ref.ref_id)
    if type(ref.origin) is SlotAddress:
        if ref.origin.slot_id not in slots_by_id:
            raise MeshIrError("E_RELOCATION", "address references an unknown binding slot", ref_id=ref.ref_id)
        slot = slots_by_id[ref.origin.slot_id]
        binding = resolved_by_slot[ref.origin.slot_id]
        if (ref.region_id, ref.owner_core, ref.memory_space) != (slot.region_id, slot.owner_core, slot.memory_space) or (ref.access == Access.READ_WRITE and slot.access != Access.READ_WRITE):
            raise MeshIrError("E_RELOCATION", "address assertions contradict its binding slot", ref_id=ref.ref_id)
        origin_offset = binding.allocation_offset_bytes
        backing_offset = binding.allocation_offset_bytes
        backing_size = binding.allocation_size_bytes
        backing_alignment = binding.allocation_alignment_bytes
        backing_access = binding.access
    elif type(ref.origin) is DirectAddress:
        origin_offset = ref.origin.offset_bytes
        backing_offset = ref.origin.backing_offset_bytes
        backing_size = ref.origin.backing_size_bytes
        backing_alignment = ref.origin.backing_alignment_bytes
        backing_access = ref.origin.backing_access
    else:
        raise MeshIrError("E_RELOCATION", "address origin type is invalid", ref_id=ref.ref_id)
    if ref.access == Access.READ_WRITE and backing_access != Access.READ_WRITE:
        raise MeshIrError("E_RELOCATION", "address requests write access from read-only backing", ref_id=ref.ref_id)
    if backing_offset > region_limit - backing_size:
        raise MeshIrError("E_RELOCATION", "address backing exceeds its region", ref_id=ref.ref_id)
    region_offset = checked_add_u64(origin_offset, ref.endpoint_addend_bytes, "endpoint region offset")
    storage_end = checked_add_u64(region_offset, ref.storage_span_bytes, "endpoint storage end")
    backing_end = checked_add_u64(backing_offset, backing_size, "address backing end")
    if not backing_offset <= region_offset or storage_end > backing_end or storage_end > region_limit:
        raise MeshIrError("E_RELOCATION", "address logical or storage span exceeds its backing", ref_id=ref.ref_id)
    address = checked_add_u64(region_base, region_offset, "resolved endpoint address")
    backing_address = checked_add_u64(region_base, backing_offset, "resolved backing address")
    _validate_axi_address_interval(arch, backing_address, backing_size)
    _validate_axi_address_interval(arch, address, ref.logical_span_bytes)
    if backing_address % backing_alignment:
        raise MeshIrError("E_RELOCATION", "address backing base violates its alignment", ref_id=ref.ref_id)
    if address % ref.endpoint_alignment_bytes:
        raise MeshIrError("E_RELOCATION", "resolved endpoint violates its alignment", ref_id=ref.ref_id)
    if ref.logical_span_bytes:
        logical_end = checked_add_u64(address, ref.logical_span_bytes, "logical endpoint end")
        physical_address = address & ~(arch.axi_data_bytes - 1)
        physical_end = checked_add_u64(logical_end, arch.axi_data_bytes - 1, "physical endpoint rounding") & ~(arch.axi_data_bytes - 1)
        physical_span = physical_end - physical_address
        backing_address_end = checked_add_u64(backing_address, backing_size, "resolved backing end")
        if physical_address < backing_address or physical_end > backing_address_end:
            raise MeshIrError("E_RELOCATION", "physical AXI beats exceed the declared backing", ref_id=ref.ref_id)
    else:
        physical_address = address
        physical_span = 0
    _validate_axi_address_interval(arch, physical_address, physical_span)
    physical_region_offset = physical_address - region_base
    target = _target_for_range(arch, ref.region_id, ref.owner_core, physical_region_offset, physical_span, ref.access)
    return ResolvedAddress(
        ref.ref_id, ref.memory_space, ref.region_id, ref.owner_core, ref.origin,
        region_offset, address, ref.endpoint_addend_bytes, ref.logical_span_bytes,
        ref.storage_span_bytes, ref.endpoint_alignment_bytes, ref.access,
        backing_offset, backing_address, backing_size, backing_alignment,
        backing_access, physical_address, physical_span, target.name,
        target.dst_node, target.router_id,
    )


def resolve_addresses(
    arch: ArchManifest,
    slots: tuple[BindingSlot, ...],
    bindings: tuple[Binding, ...],
    refs: tuple[AddressRef, ...],
    *,
    issuing_core: int,
) -> ResolvedBindings:
    validate_arch(arch)
    if type(slots) is not tuple or type(bindings) is not tuple or type(refs) is not tuple:
        raise MeshIrError("E_RELOCATION", "address inputs must be immutable tuples")
    if type(issuing_core) is not int or issuing_core not in arch.core_ids:
        raise MeshIrError("E_RELOCATION", "issuing core is unknown", issuing_core=issuing_core)
    if any(not isinstance(slot, BindingSlot) for slot in slots) or any(not isinstance(binding, Binding) for binding in bindings) or any(not isinstance(ref, AddressRef) for ref in refs):
        raise MeshIrError("E_RELOCATION", "address input record type is invalid")
    slot_ids = [slot.slot_id for slot in slots]
    binding_ids = [binding.slot_id for binding in bindings]
    if any(type(slot_id) is not int or not 1 <= slot_id <= 0xFFFFFFFF for slot_id in slot_ids) or len(slot_ids) != len(set(slot_ids)):
        raise MeshIrError("E_RELOCATION", "binding slot identities must be unique positive u32 values")
    if any(type(binding_id) is not int or not 1 <= binding_id <= 0xFFFFFFFF for binding_id in binding_ids):
        raise MeshIrError("E_RELOCATION", "binding identities must be positive u32 values")
    if any(type(slot.symbol) is not str or not slot.symbol for slot in slots) or len({slot.symbol for slot in slots}) != len(slots):
        raise MeshIrError("E_RELOCATION", "binding slot symbols must be unique and nonempty")
    if len(binding_ids) != len(set(binding_ids)) or set(binding_ids) != set(slot_ids):
        raise MeshIrError("E_RELOCATION", "bindings must cover every slot exactly once")
    slots_by_id = {slot.slot_id: slot for slot in slots}
    bindings_by_id = {binding.slot_id: binding for binding in bindings}
    resolved_bindings = []
    resolved_reference_bindings = []
    for slot_id in sorted(slot_ids):
        slot = slots_by_id[slot_id]
        for field, value in (
            ("region_id", slot.region_id),
            ("owner_core", slot.owner_core),
            ("required_allocation_bytes", slot.required_allocation_bytes),
            ("required_allocation_alignment_bytes", slot.required_allocation_alignment_bytes),
        ):
            checked_u64(value, field)
        if type(slot.memory_space) is not MemorySpace or type(slot.access) is not Access or not _power_of_two(slot.required_allocation_alignment_bytes):
            raise MeshIrError("E_RELOCATION", "binding slot type or alignment is invalid", slot_id=slot.slot_id)
        _region_base(arch, slot.region_id, slot.owner_core)
        expected_space = _memory_space(arch, slot.region_id, slot.owner_core, issuing_core)
        if slot.memory_space != expected_space:
            raise MeshIrError("E_RELOCATION", "binding slot memory-space assertion is false", slot_id=slot.slot_id)
        resolved_reference_bindings.append(_validate_binding(arch, slot, slot.reference_binding))
        resolved_bindings.append(_validate_binding(arch, slot, bindings_by_id[slot_id]))
    ordered_bindings = tuple(resolved_bindings)
    _validate_binding_overlaps(tuple(resolved_reference_bindings), "reference")
    _validate_binding_overlaps(ordered_bindings, "dispatch")
    ref_ids = [ref.ref_id for ref in refs]
    if any(type(ref_id) is not int or not 1 <= ref_id <= 0xFFFFFFFF for ref_id in ref_ids) or len(ref_ids) != len(set(ref_ids)):
        raise MeshIrError("E_RELOCATION", "address ref identities must be unique positive u32 values")
    reference_by_slot = {binding.slot_id: binding for binding in resolved_reference_bindings}
    resolved_by_slot = {binding.slot_id: binding for binding in ordered_bindings}
    ordered_refs = tuple(sorted(refs, key=lambda item: item.ref_id))
    for ref in ordered_refs:
        if type(ref.origin) is SlotAddress:
            _resolve_address(arch, ref, slots_by_id, reference_by_slot, issuing_core)
    address_tuple = tuple(_resolve_address(arch, ref, slots_by_id, resolved_by_slot, issuing_core) for ref in ordered_refs)
    identity = semantic_sha256({"bindings": ordered_bindings, "addresses": address_tuple})
    return ResolvedBindings(ordered_bindings, address_tuple, identity)


def _validate_resolved_address(arch: ArchManifest, address: ResolvedAddress, issuing_core: int) -> None:
    if type(address) is not ResolvedAddress:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor endpoint is not a resolved address")
    try:
        _validate_positive_u32(address.ref_id, "ref_id", "E_TRAFFIC_MISMATCH")
        _validate_address_origin(address.origin, address.ref_id, "E_TRAFFIC_MISMATCH")
        for field, value in (
            ("ref_id", address.ref_id),
            ("region_id", address.region_id),
            ("owner_core", address.owner_core),
            ("region_offset_bytes", address.region_offset_bytes),
            ("address", address.address),
            ("endpoint_addend_bytes", address.endpoint_addend_bytes),
            ("logical_span_bytes", address.logical_span_bytes),
            ("storage_span_bytes", address.storage_span_bytes),
            ("endpoint_alignment_bytes", address.endpoint_alignment_bytes),
            ("backing_offset_bytes", address.backing_offset_bytes),
            ("backing_address", address.backing_address),
            ("backing_size_bytes", address.backing_size_bytes),
            ("backing_alignment_bytes", address.backing_alignment_bytes),
            ("physical_address", address.physical_address),
            ("physical_span_bytes", address.physical_span_bytes),
            ("target_node", address.target_node),
            ("target_router", address.target_router),
        ):
            checked_u64(value, field)
        if type(address.memory_space) is not MemorySpace or type(address.access) is not Access or type(address.backing_access) is not Access:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint enum type is invalid")
        if not _power_of_two(address.endpoint_alignment_bytes) or not _power_of_two(address.backing_alignment_bytes):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint alignment is invalid")
        if address.storage_span_bytes < address.logical_span_bytes:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint storage span is contradictory")
        if address.access == Access.READ_WRITE and address.backing_access != Access.READ_WRITE:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint backing permission is contradictory")
        region_base, region_limit = _region_base(arch, address.region_id, address.owner_core)
        if address.memory_space != _memory_space(arch, address.region_id, address.owner_core, issuing_core):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint memory space is contradictory")
        if address.address != checked_add_u64(region_base, address.region_offset_bytes, "resolved address check"):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint address is contradictory")
        if address.backing_address != checked_add_u64(region_base, address.backing_offset_bytes, "resolved backing check"):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint backing address is contradictory")
        _validate_axi_address_interval(arch, address.backing_address, address.backing_size_bytes)
        _validate_axi_address_interval(arch, address.address, address.logical_span_bytes)
        if address.backing_address % address.backing_alignment_bytes or address.address % address.endpoint_alignment_bytes:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint alignment assertion is false")
        if address.backing_offset_bytes > region_limit - address.backing_size_bytes or address.region_offset_bytes > region_limit - address.storage_span_bytes:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint span exceeds its region")
        if not address.backing_offset_bytes <= address.region_offset_bytes or checked_add_u64(address.region_offset_bytes, address.storage_span_bytes, "resolved storage end check") > checked_add_u64(address.backing_offset_bytes, address.backing_size_bytes, "resolved backing interval end"):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint storage exceeds its backing")
        if type(address.origin) is DirectAddress:
            if (address.origin.backing_offset_bytes, address.origin.backing_size_bytes, address.origin.backing_alignment_bytes, address.origin.backing_access) != (address.backing_offset_bytes, address.backing_size_bytes, address.backing_alignment_bytes, address.backing_access):
                raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved direct backing contradicts its origin")
            expected_offset = checked_add_u64(address.origin.offset_bytes, address.endpoint_addend_bytes, "direct endpoint offset check")
        elif type(address.origin) is SlotAddress:
            expected_offset = checked_add_u64(address.backing_offset_bytes, address.endpoint_addend_bytes, "slot endpoint offset check")
        else:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint origin is invalid")
        if address.region_offset_bytes != expected_offset:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint addend is contradictory")
        if address.logical_span_bytes:
            logical_end = checked_add_u64(address.address, address.logical_span_bytes, "resolved logical end check")
            physical_address = address.address & ~(arch.axi_data_bytes - 1)
            physical_end = checked_add_u64(logical_end, arch.axi_data_bytes - 1, "resolved physical end check") & ~(arch.axi_data_bytes - 1)
            if (address.physical_address, address.physical_span_bytes) != (physical_address, physical_end - physical_address):
                raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint physical span is contradictory")
            if physical_address < address.backing_address or physical_end > checked_add_u64(address.backing_address, address.backing_size_bytes, "resolved backing end check"):
                raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint physical span exceeds backing")
        elif (address.physical_address, address.physical_span_bytes) != (address.address, 0):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "empty resolved endpoint physical span is contradictory")
        _validate_axi_address_interval(arch, address.physical_address, address.physical_span_bytes)
        target = _target_for_range(arch, address.region_id, address.owner_core, address.physical_address - region_base, address.physical_span_bytes, address.access)
        if (address.target_endpoint, address.target_node, address.target_router) != (target.name, target.dst_node, target.router_id):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint target identity is contradictory")
    except MeshIrError as error:
        if error.code == "E_TRAFFIC_MISMATCH":
            raise
        raise MeshIrError("E_TRAFFIC_MISMATCH", "resolved endpoint validation failed", detail=str(error)) from error


def _initiator_for_core(arch: ArchManifest, core_id: int) -> InitiatorEndpoint:
    matches = [endpoint for endpoint in arch.fabric.initiators if endpoint.core_id == core_id]
    if len(matches) != 1:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "issuing core must have exactly one initiator endpoint", core_id=core_id)
    return matches[0]


def calculate_descriptor_traffic(arch: ArchManifest, execution: DescriptorExecution) -> DescriptorTraffic:
    validate_arch(arch)
    return _calculate_descriptor_traffic(arch, execution)


def _calculate_descriptor_traffic(arch: ArchManifest, execution: DescriptorExecution) -> DescriptorTraffic:
    if type(execution) is not DescriptorExecution:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor execution record type is invalid")
    if type(execution.execution_count) is not int or not 1 <= execution.execution_count <= 0xFFFFFFFF:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor execution count is outside positive u32")
    descriptor = execution.descriptor
    execution_count = execution.execution_count
    if type(descriptor) is not ResolvedDescriptor:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic descriptor record type is invalid")
    identity = descriptor.identity
    _validate_stable_identity(identity)
    for field, value in (
        ("issuing_core", identity.issuing_core),
        ("peer_core", identity.peer_core),
        ("rows", descriptor.rows),
        ("row_bytes", descriptor.row_bytes),
        ("src_stride_bytes", descriptor.src_stride_bytes),
        ("dst_stride_bytes", descriptor.dst_stride_bytes),
        ("useful_bytes", descriptor.useful_bytes),
        ("physical_storage_bytes", descriptor.physical_storage_bytes),
        ("max_burst_beats", descriptor.max_burst_beats),
    ):
        checked_u64(value, field)
    if identity.issuing_core not in arch.core_ids or type(descriptor.kind) is not DmaKind:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor identity or kind is invalid")
    if identity.peer_core != INVALID_CORE_ID and identity.peer_core not in arch.core_ids:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor peer core is unknown")
    if descriptor.rows > 0xFFFFFFFF or not 1 <= descriptor.max_burst_beats <= arch.axi_max_burst_beats:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor row or burst bound is invalid")
    useful = checked_mul_u64(descriptor.rows, descriptor.row_bytes, "descriptor useful bytes")
    _validate_resolved_address(arch, descriptor.src, identity.issuing_core)
    _validate_resolved_address(arch, descriptor.dst, identity.issuing_core)
    if descriptor.useful_bytes != useful or descriptor.physical_storage_bytes < useful or descriptor.physical_storage_bytes > max(descriptor.src.backing_size_bytes, descriptor.dst.backing_size_bytes):
        raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor byte counts contradict its shape")
    if descriptor.rows > 1 and (descriptor.src_stride_bytes < descriptor.row_bytes or descriptor.dst_stride_bytes < descriptor.row_bytes):
        raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor stride would overlap rows")
    if descriptor.rows and descriptor.row_bytes:
        src_span = checked_add_u64(checked_mul_u64(descriptor.rows - 1, descriptor.src_stride_bytes, "source row displacement"), descriptor.row_bytes, "source descriptor span")
        dst_span = checked_add_u64(checked_mul_u64(descriptor.rows - 1, descriptor.dst_stride_bytes, "destination row displacement"), descriptor.row_bytes, "destination descriptor span")
        if descriptor.physical_storage_bytes < max(src_span, dst_span):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor physical storage is smaller than an endpoint traversal")
        if src_span > descriptor.src.logical_span_bytes or src_span > descriptor.src.backing_size_bytes or dst_span > descriptor.dst.logical_span_bytes or dst_span > descriptor.dst.backing_size_bytes:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor rows exceed a resolved endpoint span")
    elif descriptor.physical_storage_bytes != 0:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "empty descriptor has nonzero physical storage")
    local_src = descriptor.src.memory_space == MemorySpace.CORE_SRAM and descriptor.src.owner_core == identity.issuing_core
    local_dst = descriptor.dst.memory_space == MemorySpace.CORE_SRAM and descriptor.dst.owner_core == identity.issuing_core
    if descriptor.kind in (DmaKind.LOAD, DmaKind.PREFETCH):
        if identity.direction != TrafficDirection.READ or descriptor.src.memory_space not in (MemorySpace.HBM, MemorySpace.HOST_SHARED) or not local_dst or identity.peer_core != INVALID_CORE_ID or descriptor.dst.access != Access.READ_WRITE or descriptor.dst.backing_access != Access.READ_WRITE:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "read descriptor endpoint or direction is contradictory")
        remote = descriptor.src
        stride = descriptor.src_stride_bytes
        is_write = False
    elif descriptor.kind == DmaKind.STORE:
        if identity.direction != TrafficDirection.WRITE or not local_src or descriptor.dst.memory_space not in (MemorySpace.HBM, MemorySpace.HOST_SHARED) or identity.peer_core != INVALID_CORE_ID or descriptor.dst.access != Access.READ_WRITE or descriptor.dst.backing_access != Access.READ_WRITE:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "store descriptor endpoint or direction is contradictory")
        remote = descriptor.dst
        stride = descriptor.dst_stride_bytes
        is_write = True
    elif descriptor.kind == DmaKind.P2P_PUSH:
        if identity.direction != TrafficDirection.P2P or not local_src or descriptor.dst.memory_space != MemorySpace.PEER_SRAM or descriptor.dst.owner_core != identity.peer_core or descriptor.dst.access != Access.READ_WRITE or descriptor.dst.backing_access != Access.READ_WRITE:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "P2P descriptor endpoint or direction is contradictory")
        remote = descriptor.dst
        stride = descriptor.dst_stride_bytes
        is_write = True
    else:
        if identity.direction != TrafficDirection.LOCAL or not local_src or not local_dst or identity.peer_core != INVALID_CORE_ID or descriptor.dst.access != Access.READ_WRITE or descriptor.dst.backing_access != Access.READ_WRITE:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "local fill descriptor endpoint or direction is contradictory")
        remote = descriptor.dst
        stride = descriptor.dst_stride_bytes
        is_write = True
    initiator = _initiator_for_core(arch, identity.issuing_core)
    target = next((item for item in arch.fabric.targets if item.name == remote.target_endpoint), None)
    if target is None:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor remote target is unknown")
    source_row, source_col = divmod(initiator.router_id, arch.fabric.network.router_cols)
    target_row, target_col = divmod(target.router_id, arch.fabric.network.router_cols)
    hops = abs(source_row - target_row) + abs(source_col - target_col)
    if descriptor.kind == DmaKind.LOCAL_FILL or useful == 0:
        plan_segments, plan_bursts, beat_bytes, beats = 0, 0, 0, 0
    else:
        plan = plan_descriptor(
            descriptor.row_bytes,
            descriptor.rows,
            remote.address,
            stride,
            arch.axi_data_bytes,
            min(descriptor.max_burst_beats, arch.axi_max_burst_beats),
        )
        plan_segments, plan_bursts, beat_bytes = plan.segments, len(plan.bursts), plan.beat_bytes
        beats = beat_bytes // arch.axi_data_bytes
    counts = {
        AxiChannel.AW: plan_bursts if is_write and descriptor.kind != DmaKind.LOCAL_FILL else 0,
        AxiChannel.W: beats if is_write and descriptor.kind != DmaKind.LOCAL_FILL else 0,
        AxiChannel.B: plan_bursts if is_write and descriptor.kind != DmaKind.LOCAL_FILL else 0,
        AxiChannel.AR: plan_bursts if not is_write else 0,
        AxiChannel.R: beats if not is_write else 0,
    }
    channels = []
    single_channel_messages = []
    single_channel_flits = []
    single_channel_wire_bytes = []
    for channel in AxiChannel:
        forward = channel in (AxiChannel.AW, AxiChannel.W, AxiChannel.AR)
        message_bytes = arch.fabric.axi.wire_header_bytes[channel]
        if channel in (AxiChannel.W, AxiChannel.R):
            message_bytes = arch.axi_data_bytes + (0 if arch.fabric.axi.data_header_sideband else message_bytes)
        single_messages = counts[channel]
        single_wire_bytes = checked_mul_u64(single_messages, message_bytes, "single-execution channel wire bytes")
        flits_per_message = (message_bytes + arch.fabric.network.flit_bytes - 1) // arch.fabric.network.flit_bytes
        single_flits = checked_mul_u64(single_messages, flits_per_message, "single-execution channel flits")
        single_channel_messages.append(single_messages)
        single_channel_flits.append(single_flits)
        single_channel_wire_bytes.append(single_wire_bytes)
        messages = checked_mul_u64(single_messages, execution_count, "channel messages")
        wire_bytes = checked_mul_u64(single_wire_bytes, execution_count, "channel wire bytes")
        flits = checked_mul_u64(single_flits, execution_count, "channel flits")
        if forward:
            source_endpoint, destination_endpoint = initiator.name, target.name
            source_node, source_port = initiator.src_node, initiator.src_port
            destination_node, destination_port = target.dst_node, 0
            source_router, destination_router = initiator.router_id, target.router_id
        else:
            source_endpoint, destination_endpoint = target.name, initiator.name
            source_node, source_port = target.dst_node, 0
            destination_node, destination_port = initiator.src_node, initiator.src_port
            source_router, destination_router = target.router_id, initiator.router_id
        channels.append(ChannelTraffic(
            channel, int(channel), source_endpoint, destination_endpoint,
            source_node, source_port, destination_node, destination_port,
            source_router, destination_router, messages, messages, flits, wire_bytes,
        ))
    channel_tuple = tuple(channels)
    single_packets = _sum_u64(single_channel_messages, "single-execution descriptor packets")
    single_flits = _sum_u64(single_channel_flits, "single-execution descriptor flits")
    single_wire_bytes = _sum_u64(single_channel_wire_bytes, "single-execution descriptor wire bytes")
    packets = checked_mul_u64(single_packets, execution_count, "descriptor packets across executions")
    flits = checked_mul_u64(single_flits, execution_count, "descriptor flits across executions")
    wire_bytes = checked_mul_u64(single_wire_bytes, execution_count, "descriptor wire bytes across executions")
    single_hop_wire_bytes = checked_mul_u64(single_wire_bytes, hops, "single-execution descriptor hop wire bytes")
    return DescriptorTraffic(
        identity,
        execution_count,
        descriptor.src.memory_space,
        descriptor.dst.memory_space,
        descriptor.src.target_endpoint,
        descriptor.dst.target_endpoint,
        descriptor.src.target_router,
        descriptor.dst.target_router,
        descriptor.src.address,
        descriptor.dst.address,
        remote.address,
        descriptor.rows,
        descriptor.row_bytes,
        stride,
        min(descriptor.max_burst_beats, arch.axi_max_burst_beats),
        hops,
        checked_mul_u64(useful, execution_count, "descriptor useful bytes across executions"),
        checked_mul_u64(beat_bytes, execution_count, "descriptor physical beat bytes across executions"),
        checked_mul_u64(plan_segments, execution_count, "descriptor segments across executions"),
        checked_mul_u64(plan_bursts, execution_count, "descriptor bursts across executions"),
        channel_tuple,
        packets,
        flits,
        wire_bytes,
        checked_mul_u64(single_hop_wire_bytes, execution_count, "descriptor hop wire bytes across executions"),
    )


def _aggregate(key: TrafficAggregateKey, rows: tuple[DescriptorTraffic, ...]) -> TrafficAggregate:
    channel_totals = tuple(TrafficChannelTotal(
        channel,
        int(channel),
        _sum_u64((row.channels[channel].messages for row in rows), "aggregate channel messages"),
        _sum_u64((row.channels[channel].packets for row in rows), "aggregate channel packets"),
        _sum_u64((row.channels[channel].flits for row in rows), "aggregate channel flits"),
        _sum_u64((row.channels[channel].wire_bytes for row in rows), "aggregate channel wire bytes"),
    ) for channel in AxiChannel)
    return TrafficAggregate(
        key,
        _sum_u64((row.useful_bytes for row in rows), "aggregate useful bytes"),
        _sum_u64((row.physical_beat_bytes for row in rows), "aggregate physical beat bytes"),
        checked_u64(len(rows), "aggregate static descriptors"),
        _sum_u64((row.execution_count for row in rows), "aggregate descriptor executions"),
        _sum_u64((row.segments for row in rows), "aggregate segments"),
        _sum_u64((row.bursts for row in rows), "aggregate bursts"),
        channel_totals,
        _sum_u64((row.packets for row in rows), "aggregate packets"),
        _sum_u64((row.flits for row in rows), "aggregate flits"),
        _sum_u64((row.wire_bytes for row in rows), "aggregate wire bytes"),
        _sum_u64((row.hop_wire_bytes for row in rows), "aggregate hop wire bytes"),
    )


def _traffic_payload(
    binding_identity_sha256: str,
    descriptors: tuple[DescriptorTraffic, ...],
    aggregates: tuple[TrafficAggregate, ...],
) -> dict[str, object]:
    from mesh_ir.abi.semantic import semantic_to_canonical

    return {
        "binding_identity_sha256": binding_identity_sha256,
        "descriptors": [semantic_to_canonical(value) for value in descriptors],
        "aggregates": [semantic_to_canonical(value) for value in aggregates],
    }


def _validate_report_shape(report: TrafficReport) -> None:
    for field, value in (
        ("binding_identity_sha256", report.binding_identity_sha256),
        ("semantic_sha256", report.semantic_sha256),
    ):
        if type(value) is not str or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic report hash is not canonical SHA-256", field=field)
    if type(report.descriptors) is not tuple or type(report.aggregates) is not tuple:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic report rows must be immutable tuples")
    for row in report.descriptors:
        if type(row) is not DescriptorTraffic:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic descriptor report row type is invalid")
        _validate_stable_identity(row.identity)
        if type(row.execution_count) is not int or not 1 <= row.execution_count <= 0xFFFFFFFF:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic descriptor execution count is outside positive u32")
        if type(row.src_memory_space) is not MemorySpace or type(row.dst_memory_space) is not MemorySpace:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic descriptor memory-space type is invalid")
        if any(type(value) is not str or not value for value in (row.src_endpoint, row.dst_endpoint)):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic descriptor endpoint identity is invalid")
        for field, value in (
            ("src_router", row.src_router),
            ("dst_router", row.dst_router),
            ("src_address", row.src_address),
            ("dst_address", row.dst_address),
            ("remote_address", row.remote_address),
            ("rows", row.rows),
            ("row_bytes", row.row_bytes),
            ("remote_stride_bytes", row.remote_stride_bytes),
            ("max_burst_beats", row.max_burst_beats),
            ("hops", row.hops),
            ("useful_bytes", row.useful_bytes),
            ("physical_beat_bytes", row.physical_beat_bytes),
            ("segments", row.segments),
            ("bursts", row.bursts),
            ("packets", row.packets),
            ("flits", row.flits),
            ("wire_bytes", row.wire_bytes),
            ("hop_wire_bytes", row.hop_wire_bytes),
        ):
            checked_u64(value, field)
        if type(row.channels) is not tuple or len(row.channels) != len(AxiChannel):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic descriptor channel rows are incomplete")
        for channel, expected_channel in zip(row.channels, AxiChannel):
            if type(channel) is not ChannelTraffic or type(channel.channel) is not AxiChannel or channel.channel != expected_channel or type(channel.vnet) is not int or channel.vnet != int(expected_channel):
                raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic descriptor channel identity is invalid")
            if any(type(value) is not str or not value for value in (channel.source_endpoint, channel.destination_endpoint)):
                raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic channel endpoint identity is invalid")
            for field, value in (
                ("source_node", channel.source_node),
                ("source_port", channel.source_port),
                ("destination_node", channel.destination_node),
                ("destination_port", channel.destination_port),
                ("source_router", channel.source_router),
                ("destination_router", channel.destination_router),
                ("messages", channel.messages),
                ("packets", channel.packets),
                ("flits", channel.flits),
                ("wire_bytes", channel.wire_bytes),
            ):
                checked_u64(value, field)
    for aggregate in report.aggregates:
        if type(aggregate) is not TrafficAggregate or type(aggregate.key) is not TrafficAggregateKey:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic aggregate row or key type is invalid")
        key = aggregate.key
        if type(key.level) is not TrafficAggregateLevel:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic aggregate level type is invalid")
        for field, value in (
            ("entrypoint_id", key.entrypoint_id),
            ("profile_id", key.profile_id),
            ("core_id", key.core_id),
            ("peer_core", key.peer_core),
        ):
            if value is not None:
                checked_u64(value, field)
        if key.memory_space is not None and type(key.memory_space) is not MemorySpace:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic aggregate memory-space type is invalid")
        if key.tensor_role is not None and type(key.tensor_role) is not TensorRole:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic aggregate tensor-role type is invalid")
        if key.direction is not None and type(key.direction) is not TrafficDirection:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic aggregate direction type is invalid")
        for field, value in (
            ("useful_bytes", aggregate.useful_bytes),
            ("physical_beat_bytes", aggregate.physical_beat_bytes),
            ("static_descriptors", aggregate.static_descriptors),
            ("descriptor_executions", aggregate.descriptor_executions),
            ("segments", aggregate.segments),
            ("bursts", aggregate.bursts),
            ("packets", aggregate.packets),
            ("flits", aggregate.flits),
            ("wire_bytes", aggregate.wire_bytes),
            ("hop_wire_bytes", aggregate.hop_wire_bytes),
        ):
            checked_u64(value, field)
        if type(aggregate.channels) is not tuple or len(aggregate.channels) != len(AxiChannel):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic aggregate channel rows are incomplete")
        for channel, expected_channel in zip(aggregate.channels, AxiChannel):
            if type(channel) is not TrafficChannelTotal or type(channel.channel) is not AxiChannel or channel.channel != expected_channel or type(channel.vnet) is not int or channel.vnet != int(expected_channel):
                raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic aggregate channel identity is invalid")
            for field, value in (
                ("messages", channel.messages),
                ("packets", channel.packets),
                ("flits", channel.flits),
                ("wire_bytes", channel.wire_bytes),
            ):
                checked_u64(value, field)


def calculate_traffic(
    arch: ArchManifest,
    binding_identity_sha256: str,
    executions: tuple[DescriptorExecution, ...],
) -> TrafficReport:
    validate_arch(arch)
    if type(binding_identity_sha256) is not str or len(binding_identity_sha256) != 64 or any(character not in "0123456789abcdef" for character in binding_identity_sha256):
        raise MeshIrError("E_TRAFFIC_MISMATCH", "binding identity must be lowercase SHA-256")
    if type(executions) is not tuple:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "descriptor executions must be an immutable tuple")
    rows = tuple(sorted(
        (_calculate_descriptor_traffic(arch, execution) for execution in executions),
        key=lambda row: (row.identity.entrypoint_id, row.identity.profile_id, row.identity.command_id, row.identity.descriptor_id),
    ))
    identities = [(row.identity.entrypoint_id, row.identity.profile_id, row.identity.command_id, row.identity.descriptor_id) for row in rows]
    if len(identities) != len(set(identities)):
        raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic descriptor identities must be unique")
    groups: list[tuple[TrafficAggregateKey, tuple[DescriptorTraffic, ...]]] = []
    groups.append((TrafficAggregateKey(TrafficAggregateLevel.PROGRAM, None, None, None, None, None, None, None), rows))
    for entrypoint_id in sorted({row.identity.entrypoint_id for row in rows}):
        selected = tuple(row for row in rows if row.identity.entrypoint_id == entrypoint_id)
        groups.append((TrafficAggregateKey(TrafficAggregateLevel.ENTRYPOINT, entrypoint_id, None, None, None, None, None, None), selected))
    for entrypoint_id, profile_id in sorted({(row.identity.entrypoint_id, row.identity.profile_id) for row in rows}):
        selected = tuple(row for row in rows if (row.identity.entrypoint_id, row.identity.profile_id) == (entrypoint_id, profile_id))
        groups.append((TrafficAggregateKey(TrafficAggregateLevel.PROFILE, entrypoint_id, profile_id, None, None, None, None, None), selected))
    detail_keys = sorted({
        (
            row.identity.entrypoint_id,
            row.identity.profile_id,
            row.identity.issuing_core,
            row.identity.peer_core,
            row.src_memory_space if row.identity.direction == TrafficDirection.READ else row.dst_memory_space,
            row.identity.tensor_role,
            row.identity.direction,
        )
        for row in rows
    }, key=lambda item: tuple(int(value) if isinstance(value, IntEnum) else value.value if isinstance(value, StrEnum) else value for value in item))
    for detail in detail_keys:
        selected = tuple(row for row in rows if (
            row.identity.entrypoint_id,
            row.identity.profile_id,
            row.identity.issuing_core,
            row.identity.peer_core,
            row.src_memory_space if row.identity.direction == TrafficDirection.READ else row.dst_memory_space,
            row.identity.tensor_role,
            row.identity.direction,
        ) == detail)
        groups.append((TrafficAggregateKey(TrafficAggregateLevel.DETAIL, *detail), selected))
    aggregates = tuple(_aggregate(key, selected) for key, selected in groups)
    payload = _traffic_payload(binding_identity_sha256, rows, aggregates)
    return TrafficReport(binding_identity_sha256, rows, aggregates, semantic_sha256(payload))


def verify_traffic_report(
    arch: ArchManifest,
    binding_identity_sha256: str,
    executions: tuple[DescriptorExecution, ...],
    stored: TrafficReport,
) -> None:
    if type(stored) is not TrafficReport:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "stored traffic report type is invalid")
    expected = calculate_traffic(arch, binding_identity_sha256, executions)
    try:
        _validate_report_shape(stored)
        stored_payload = _traffic_payload(stored.binding_identity_sha256, stored.descriptors, stored.aggregates)
        if type(stored.semantic_sha256) is not str or semantic_sha256(stored_payload) != stored.semantic_sha256 or canonical_json_bytes(stored.canonical_dict()) != canonical_json_bytes(expected.canonical_dict()):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "stored traffic report does not match independent descriptor calculation")
    except MeshIrError as error:
        if error.code == "E_TRAFFIC_MISMATCH":
            raise
        raise MeshIrError("E_TRAFFIC_MISMATCH", "stored traffic report has invalid typed canonical data", detail=str(error)) from error
