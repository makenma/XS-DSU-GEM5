from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileStreamRange:
    profile_id: int
    core_id: int
    stream_id: int
    command_begin: int
    command_count: int


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class AgentSourceCoreMap:
    core_id: int
    reserved: int = 0


@dataclass(frozen=True)
class AgentInstanceMemberBinding:
    instance_profile_id: int
    member_ordinal: int
    reserved0: int
    static_input_symbol_id: int
    static_output_symbol_id: int
    static_kv_symbol_id: int
    expected_logical_source_rank: int


@dataclass(frozen=True)
class AgentInstanceTensorBinding:
    instance_profile_id: int
    member_ordinal: int
    binding_kind: int
    static_symbol_id: int
    request_symbol_id: int
    offset_bytes: int
    span_bytes: int


@dataclass(frozen=True)
class AgentRequestBindingRequirement:
    request_program_id: int
    request_profile_id: int
    binding_kind: int
    binding_flags: int
    symbol_id: int
    reserved: int = 0


@dataclass(frozen=True)
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
