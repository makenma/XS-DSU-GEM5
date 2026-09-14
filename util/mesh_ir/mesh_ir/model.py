"""Scheduled Mesh IR data model, canonical JSON and stable error codes.

Record dataclasses mirror util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml exactly;
the correspondence is asserted at import time against the generated ABI so
the schema cannot drift without a loud failure.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from mesh_ir.generated import abi as A

ERROR_CODES = (
    "E_ABI_MAGIC",
    "E_ABI_VERSION",
    "E_ABI_CHECKSUM",
    "E_ABI_SECTION_RANGE",
    "E_ABI_ENUM",
    "E_ABI_RESERVED",
    "E_ABI_FEATURE",
    "E_ABI_DUPLICATE",
    "E_ABI_ORDER",
    "E_ABI_BOUNDS",
    "E_ABI_OVERFLOW",
    "E_ABI_CORRUPT",
    "E_ARCH_DIGEST",
    "E_ENGINE_MISMATCH",
    "E_EVENT_NO_PRODUCER",
    "E_EVENT_MULTIPLE_PRODUCERS",
    "E_DEPENDENCY_CYCLE",
    "E_DMA_RANGE",
    "E_P2P_UNMATCHED",
    "E_STREAM_CONTRACT",
    "E_LIFECYCLE",
    "E_RELOCATION",
    "E_TRAFFIC_MISMATCH",
    "E_SRAM_OOM",
    "E_CAPABILITY_MISMATCH",
    "E_ARCH_PARTITION",
    "E_CAPACITY_PLAN",
    "E_MOE_UID",
    "E_MOE_RNG",
    "E_MOE_PROVIDER",
    "E_MOE_ROUTE_REPLAY",
    "E_MOE_TOPK_DUP",
    "E_MOE_PROFILE",
    "E_MOE_KEY_COLLISION",
    "E_MOE_CACHE",
    "E_MOE_TRAFFIC",
    "E_MOE_CAPACITY",
    "E_MOE_ROUTE_DIGEST",
    "E_MOE_SELECTION_V",
    "E_MOE_MATERIALIZATION_V",
    "E_MOE_MATERIALIZE_CAPACITY",
    "E_MOE_DISPATCH_BYTES",
    "E_MOE_COMBINE_BYTES",
    "E_REQUEST_PROFILE",
    "E_REQUEST_PROFILE_KEY",
    "E_BINDING_ROLE",
    "E_HOST_IO_SIZE_MISMATCH",
    "E_OUTPUT_CHUNK_MISMATCH",
    "E_KV_TOKEN_MISMATCH",
    "E_KV_CONTRACT_MISMATCH",
    "E_KV_FLAG_COMBINATION",
    "E_AGENT_PROTOCOL_FATAL",
)


class MeshIrError(Exception):
    def __init__(self, code: str, message: str, **context):
        assert code in ERROR_CODES, f"unregistered error code {code}"
        self.code = code
        self.message = message
        self.context = {k: v for k, v in context.items() if v is not None}
        super().__init__(f"{code}: {message} {self.context}".rstrip())


@dataclass
class ArchRegion:
    name: str
    kind: str
    base: int
    bytes: int
    tile_stride: int = 0
    tile_bytes: int = 0


@dataclass
class ArchPartition:
    kind: int
    base: int
    bytes: int
    alignment: int
    metadata_entries: int
    max_pinned_entries: int = 0
    replacement: str = "LRU"


@dataclass
class ArchManifest:
    schema_version: str
    arch_name: str
    clock_hz: int
    core_ids: tuple
    mesh_rows: int
    mesh_cols: int
    mesh_routing: str
    mesh_endpoint_order: str
    command_rom_entries: int
    event_visibility_cycles: int
    decode_width: int
    admit_window: int
    sram_bytes: int
    sram_banks: int
    sram_read_ports_per_bank: int
    sram_write_ports_per_bank: int
    sram_bank_queue_depth: int
    sram_read_bytes_per_cycle_per_bank: int
    sram_write_bytes_per_cycle_per_bank: int
    sram_base_alignment_bytes: int
    tensor_queue_depth: int
    tensor_setup_cycles: int
    tensor_pipeline_flush_cycles: int
    tensor_macs_per_cycle: dict
    vector_queue_depth: int
    vector_elements_per_cycle: dict
    reduce_queue_depth: int
    reduce_setup_cycles: int
    reduce_flush_cycles: int
    reduce_ops_per_cycle: dict
    dma_read_engines: int
    dma_write_engines: int
    dma_descriptor_queue_depth: int
    dma_segment_queue_depth: int
    dma_read_outstanding: int
    dma_write_outstanding: int
    dma_setup_cycles: int
    dma_burst_base_latency: int
    axi_data_bytes: int
    axi_max_burst_beats: int
    axi_address_bits: int
    axi_id_bits: int
    axi_max_outstanding_per_id: int
    axi_enforce_4k_boundary: bool
    axi_qos_default: int
    regions: tuple
    sram_partitions: tuple = ()
    sram_weight_cache_slot_bytes: int = 0

    def region_by_id(self, region_id: int) -> ArchRegion:
        return self.regions[region_id]

    def partition(self, kind: int) -> Optional[ArchPartition]:
        for candidate in self.sram_partitions:
            if candidate.kind == kind:
                return candidate
        return None

    def canonical_dict(self) -> dict:
        document = dataclasses.asdict(self)
        if not self.sram_partitions:
            document.pop("sram_partitions")
            document.pop("sram_weight_cache_slot_bytes")
        return document

    def digest(self) -> bytes:
        return hashlib.sha256(
            canonical_json_bytes(self.canonical_dict())
        ).digest()


@dataclass
class StringEntry:
    value: str


@dataclass
class Entrypoint:
    entrypoint_id: int
    name_sid: int
    profile_begin: int
    profile_count: int
    lifecycle_core_id: int
    lifecycle_stream_id: int
    flags: int = 0
    reserved: int = 0


@dataclass
class Profile:
    profile_id: int
    entrypoint_id: int
    name_sid: int
    rank: int
    reserved: int = 0
    dims: tuple = (0,) * 8


@dataclass
class Tensor:
    tensor_id: int
    name_sid: int
    role: int
    dtype: int
    storage_class: int
    access: int
    rank: int
    layout: int
    layout_attr: int = 0
    placement_id: int = 0
    sharding_id: int = 0
    flags: int = 0
    reserved: int = 0
    dims: tuple = (0,) * 8
    content_sha256: bytes = bytes(32)


@dataclass
class Shard:
    shard_id: int
    tensor_id: int
    sharding_id: int
    owner_core: int
    rank: int
    reserved: int = 0
    flags: int = 0
    reserved0: int = 0
    global_origin: tuple = (0,) * 8
    local_shape: tuple = (0,) * 8
    valid_shape: tuple = (0,) * 8
    allocation_id: int = 0
    reserved2: int = 0
    allocation_offset: int = 0
    span_bytes: int = 0


@dataclass
class Allocation:
    allocation_id: int
    owner_core: int
    memory_space: int
    offset_bytes: int
    size_bytes: int
    alignment_bytes: int
    flags: int = 0


@dataclass
class Stream:
    core_id: int
    stream_id: int
    command_begin: int
    command_count: int
    flags: int = 0
    reserved: int = 0


@dataclass
class Command:
    command_id: int
    source_op_id: int
    core_id: int
    stream_id: int
    engine: int
    opcode: int
    wait_begin: int = 0
    wait_count: int = 0
    operand_count: int = 0
    operand_begin: int = 0
    signal_event: int = 0
    attr_index: int = 0
    debug_loc_id: int = 0


@dataclass
class CommandWait:
    event_id: int


@dataclass
class CommandOperand:
    tensor_id: int
    shard_id: int
    allocation_id: int
    access: int
    reserved: int = 0


@dataclass
class Event:
    event_id: int
    kind: int
    reserved: int = 0
    producer_command_id: int = 0
    expected_arrivals: int = 0
    reserved2: int = 0


@dataclass
class DmaEndpoint:
    memory_space: int
    region_id: int
    owner_core: int
    tensor_id: int
    shard_id: int
    reserved: int = 0
    offset_bytes: int = 0


@dataclass
class DmaDescriptor:
    descriptor_id: int
    command_id: int
    transfer_id: int
    owner_core: int
    kind: int
    src: DmaEndpoint
    dst: DmaEndpoint
    rows: int
    row_bytes: int
    src_stride_bytes: int
    dst_stride_bytes: int
    useful_bytes: int
    physical_storage_bytes: int
    axi_id: int
    qos: int
    reserved: int
    max_burst_beats: int
    reserved2: int
    completion_event: int


@dataclass
class OpAttr:
    kind: int
    reserved: int = 0
    payload: tuple = ()
    payload_fields: tuple = ()

    def payload_dict(self) -> dict:
        return dict(zip(self.payload_fields, self.payload))


@dataclass
class Relocation:
    relocation_id: int
    symbol_sid: int
    kind: int
    region_id: int
    tensor_id: int
    reserved: int
    offset_bytes: int
    reserved2: int


@dataclass
class ExpectedTrafficRow:
    entrypoint_id: int
    profile_id: int
    command_id: int
    descriptor_id: int
    kind: int
    reserved: int
    useful_bytes: int
    physical_beat_bytes: int
    segments: int
    bursts: int
    ar_count: int
    r_beats: int
    aw_count: int
    w_beats: int
    b_count: int
    min_flits: int = 0
    reserved2: int = 0


@dataclass
class ContentDigest:
    object_kind: int
    reserved: int
    object_id: int
    digest: bytes


@dataclass
class MoeLayerSpec:
    layer_id: int
    kernel_spec_index: int
    expert_first: int
    expert_count: int
    top_k: int
    token_bytes: int
    output_token_bytes: int
    capacity_factor_q16: int
    overflow_policy: int
    transport_mode: int
    dynamic_region_first: int
    dynamic_region_count: int
    max_tokens_per_frozen_batch: int
    max_requests_per_batch: int
    max_routes: int
    max_materialized_commands: int
    max_materialized_descriptors: int
    max_materialized_transfers: int
    max_dynamic_allocations: int
    flags: int
    max_materialized_events: int
    reserved1: int = 0


@dataclass
class MoeExpertSpec:
    layer_id: int
    expert_id: int
    flags: int
    core_id: int
    reserved_core: int
    weight_symbol_id: int
    weight_region_offset: int
    weight_bytes: int
    weight_digest_index: int
    reserved: int = 0


@dataclass
class MoeDynamicRegion:
    region_id: int
    layer_id: int
    core_id: int
    stream_id: int
    insert_after_command_id: int
    resume_before_command_id: int
    entry_event_id: int
    scratch_offset: int
    scratch_bytes: int
    scratch_alignment: int
    max_overlay_commands: int
    max_overlay_events: int
    max_overlay_descriptors: int
    max_overlay_transfers: int
    max_overlay_allocations: int
    flags: int
    reserved: int = 0


@dataclass
class MoeKernelSpec:
    layer_id: int
    expert_opcode: int
    input_dtype: int
    accum_dtype: int
    output_dtype: int
    batch: int
    n: int
    k: int
    transpose_flags: int
    combine_kind: int
    algorithm_id: int
    efficiency_q16: int
    tensor_setup_cycles: int
    tensor_flush_cycles: int
    input_token_bytes: int
    output_token_bytes: int
    reserved0: int
    weight_operand_bytes: int
    expert_result_alignment: int
    max_m: int
    combine_setup_cycles: int
    combine_flush_cycles: int
    flags: int
    reserved1: int = 0


@dataclass
class AgentRequestProfile:
    program_id: int
    profile_id: int
    flags: int
    reserved0: int
    requested_profile_key: int
    delta_input_tokens: int
    full_input_tokens: int
    expected_cached_tokens: int
    output_tokens: int
    input_binding_bytes: int
    delta_input_dma_bytes: int
    full_input_dma_bytes: int
    host_output_bytes: int
    primary_input_symbol_id: int
    primary_output_symbol_id: int
    primary_kv_symbol_id: int
    source_rank_count: int
    source_core_map_begin: int
    path_mask: int
    kv_bytes_per_token: int
    publish_chunk_bytes: int


@dataclass
class AgentInstanceProfile:
    instance_profile_id: int
    request_program_id: int
    request_profile_id: int
    path_kind: int
    phase: int
    member_count: int
    decode_chunk_tokens: int
    mesh_entrypoint_id: int
    mesh_profile_id: int
    valid_tokens_per_member: int
    kv_tokens_before: int
    local_padded_members: int
    local_padded_tokens_per_member: int
    primary_input_symbol_id: int
    primary_output_symbol_id: int
    primary_kv_symbol_id: int
    flags: int
    member_binding_first: int
    member_binding_count: int
    host_input_dma_bytes_per_member: int
    host_output_dma_bytes_per_member: int
    kv_read_bytes_per_member: int
    kv_write_bytes_per_member: int


@dataclass
class AgentSourceCoreMap:
    core_id: int
    reserved: int = 0


@dataclass
class AgentInstanceMemberBinding:
    instance_profile_id: int
    member_ordinal: int
    reserved0: int
    static_input_symbol_id: int
    static_output_symbol_id: int
    static_kv_symbol_id: int
    expected_logical_source_rank: int


@dataclass
class AgentRequestBindingRequirement:
    request_program_id: int
    request_profile_id: int
    binding_kind: int
    binding_flags: int
    symbol_id: int
    reserved: int = 0


@dataclass
class AgentPublishSurrogateBinding:
    instance_profile_id: int
    member_ordinal: int
    reserved0: int
    allocation_id: int
    producer_command_id: int
    completion_event_id: int
    fill_kind: int
    allocation_role: int
    digest_source: int
    reserved1: int = 0


@dataclass
class Program:
    abi_major: int
    abi_minor: int
    arch_digest: bytes
    strings: list
    entrypoints: list
    profiles: list
    tensors: list
    shards: list
    allocations: list
    streams: list
    commands: list
    command_waits: list
    command_operands: list
    events: list
    dma_descriptors: list
    op_attrs: list
    relocations: list
    expected_traffic: list
    required_features: int = 0
    moe_layer_specs: list = field(default_factory=list)
    moe_expert_specs: list = field(default_factory=list)
    moe_dynamic_regions: list = field(default_factory=list)
    moe_kernel_specs: list = field(default_factory=list)
    content_digests: list = field(default_factory=list)
    agent_request_profiles: list = field(default_factory=list)
    agent_instance_profiles: list = field(default_factory=list)
    agent_source_core_map: list = field(default_factory=list)
    agent_instance_member_bindings: list = field(default_factory=list)
    agent_request_binding_requirements: list = field(default_factory=list)
    agent_publish_surrogate_bindings: list = field(default_factory=list)

    def canonical_dict(self) -> dict:
        abi = {"major": self.abi_major, "minor": self.abi_minor}
        sections = {
                "STRINGS": [s.value for s in self.strings],
                "ENTRYPOINTS": [canonical(rec) for rec in self.entrypoints],
                "PROFILES": [canonical(rec) for rec in self.profiles],
                "TENSORS": [canonical(rec) for rec in self.tensors],
                "SHARDS": [canonical(rec) for rec in self.shards],
                "ALLOCATIONS": [canonical(rec) for rec in self.allocations],
                "STREAMS": [canonical(rec) for rec in self.streams],
                "COMMANDS": [canonical(rec) for rec in self.commands],
                "COMMAND_WAITS": [canonical(rec) for rec in self.command_waits],
                "COMMAND_OPERANDS": [canonical(rec) for rec in self.command_operands],
                "EVENTS": [canonical(rec) for rec in self.events],
                "DMA_DESCRIPTORS": [canonical(rec) for rec in self.dma_descriptors],
                "OP_ATTRS": [self._attr_dict(rec) for rec in self.op_attrs],
                "RELOCATIONS": [canonical(rec) for rec in self.relocations],
                "EXPECTED_TRAFFIC": [canonical(rec) for rec in self.expected_traffic],
        }
        if self.required_features:
            abi["required_features"] = self.required_features
        if self.required_features & A.DYNAMIC_MOE_V1:
            sections["CONTENT_DIGESTS"] = [
                canonical(rec) for rec in self.content_digests]
            sections["MOE_LAYER_SPECS"] = [
                canonical(rec) for rec in self.moe_layer_specs]
            sections["MOE_EXPERT_SPECS"] = [
                canonical(rec) for rec in self.moe_expert_specs]
            sections["MOE_DYNAMIC_REGIONS"] = [
                canonical(rec) for rec in self.moe_dynamic_regions]
            sections["MOE_KERNEL_SPECS"] = [
                canonical(rec) for rec in self.moe_kernel_specs]
        if self.required_features & A.AGENT_SERVING_V1:
            sections["AGENT_REQUEST_PROFILES"] = [
                canonical(rec) for rec in self.agent_request_profiles]
            sections["AGENT_INSTANCE_PROFILES"] = [
                canonical(rec) for rec in self.agent_instance_profiles]
            sections["AGENT_SOURCE_CORE_MAP"] = [
                canonical(rec) for rec in self.agent_source_core_map]
            sections["AGENT_INSTANCE_MEMBER_BINDINGS"] = [
                canonical(rec) for rec in self.agent_instance_member_bindings]
            sections["AGENT_REQUEST_BINDING_REQUIREMENTS"] = [
                canonical(rec) for rec in self.agent_request_binding_requirements]
            sections["AGENT_PUBLISH_SURROGATE_BINDINGS"] = [
                canonical(rec) for rec in self.agent_publish_surrogate_bindings]
        return {"abi": abi, "arch_digest": self.arch_digest.hex(),
                "sections": sections}

    @staticmethod
    def _attr_dict(attr: OpAttr) -> dict:
        return {"kind": attr.kind, **{k: _plain(v) for k, v in attr.payload_dict().items()}}

    def semantic_sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.canonical_dict())).hexdigest()

    def shard_of(self, tensor_role: int, owner_core: int) -> Optional[Shard]:
        roles = {tensor.tensor_id: tensor.role for tensor in self.tensors}
        matches = [shard for shard in self.shards
                   if shard.owner_core == owner_core and
                   roles.get(shard.tensor_id) == tensor_role]
        if len(matches) > 1:
            raise MeshIrError("E_ABI_DUPLICATE",
                              "several shards carry one tensor role",
                              tensor_role=tensor_role, owner_core=owner_core)
        return matches[0] if matches else None


def _plain(value):
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if hasattr(value, "payload_dict"):
        return {k: _plain(v) for k, v in value.payload_dict().items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return canonical(value)
    return value


def canonical(record) -> dict:
    out = {}
    for f in dataclasses.fields(record):
        out[f.name] = _plain(getattr(record, f.name))
    return out


def canonical_json_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


RECORD_CLASSES = {
    "ENTRYPOINTS": Entrypoint,
    "PROFILES": Profile,
    "TENSORS": Tensor,
    "SHARDS": Shard,
    "ALLOCATIONS": Allocation,
    "STREAMS": Stream,
    "COMMANDS": Command,
    "COMMAND_WAITS": CommandWait,
    "COMMAND_OPERANDS": CommandOperand,
    "EVENTS": Event,
    "DMA_DESCRIPTORS": DmaDescriptor,
    "RELOCATIONS": Relocation,
    "EXPECTED_TRAFFIC": ExpectedTrafficRow,
    "CONTENT_DIGESTS": ContentDigest,
    "MOE_LAYER_SPECS": MoeLayerSpec,
    "MOE_EXPERT_SPECS": MoeExpertSpec,
    "MOE_DYNAMIC_REGIONS": MoeDynamicRegion,
    "MOE_KERNEL_SPECS": MoeKernelSpec,
    "AGENT_REQUEST_PROFILES": AgentRequestProfile,
    "AGENT_INSTANCE_PROFILES": AgentInstanceProfile,
    "AGENT_SOURCE_CORE_MAP": AgentSourceCoreMap,
    "AGENT_INSTANCE_MEMBER_BINDINGS": AgentInstanceMemberBinding,
    "AGENT_REQUEST_BINDING_REQUIREMENTS": AgentRequestBindingRequirement,
    "AGENT_PUBLISH_SURROGATE_BINDINGS": AgentPublishSurrogateBinding,
}


def _assert_model_matches_schema() -> None:
    for name, cls in RECORD_CLASSES.items():
        schema_names = tuple(f["name"] for f in getattr(A, f"{name}_FIELDS"))
        model_names = tuple(f.name for f in dataclasses.fields(cls))
        assert schema_names == model_names, (
            f"{name} model fields {model_names} != schema fields {schema_names}; "
            "regenerate abi.py and update model.py"
        )


_assert_model_matches_schema()
