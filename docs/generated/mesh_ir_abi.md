# Mesh IR ABI Reference

Generated from `util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml` -- do not edit.

- schema_sha256: `ab7a1c14454aee763ad32f901b66ad051bbaae4d17c446064baeedebbb60b78b`
- ABI: 1.3 (min reader minor 0)
- magic: `4d53484200000001`

## Enums

- `section_type`: `STRINGS=1`, `ENTRYPOINTS=2`, `PROFILES=3`, `TENSORS=4`, `SHARDS=5`, `ALLOCATIONS=6`, `STREAMS=7`, `COMMANDS=8`, `COMMAND_WAITS=9`, `COMMAND_OPERANDS=10`, `EVENTS=11`, `DMA_DESCRIPTORS=12`, `OP_ATTRS=13`, `RELOCATIONS=14`, `EXPECTED_TRAFFIC=15`, `PROFILE_STREAM_RANGES=16`, `SOURCE_MAP=101`, `PROFILE_HINTS=102`, `CONTENT_DIGESTS=103`, `MOE_LAYER_SPECS=16384`, `MOE_EXPERT_SPECS=16385`, `MOE_DYNAMIC_REGIONS=16386`, `MOE_KERNEL_SPECS=16387`, `AGENT_REQUEST_PROFILES=16388`, `AGENT_INSTANCE_PROFILES=16389`, `AGENT_SOURCE_CORE_MAP=16390`, `AGENT_INSTANCE_MEMBER_BINDINGS=16391`, `AGENT_REQUEST_BINDING_REQUIREMENTS=16392`, `AGENT_PUBLISH_SURROGATE_BINDINGS=16393`
- `required_sections` (required): STRINGS, ENTRYPOINTS, PROFILES, TENSORS, SHARDS, ALLOCATIONS, STREAMS, COMMANDS, COMMAND_WAITS, COMMAND_OPERANDS, EVENTS, DMA_DESCRIPTORS, OP_ATTRS, RELOCATIONS, EXPECTED_TRAFFIC
- `opcode`: `REQUEST_BEGIN=1`, `REQUEST_END=2`, `HALT=3`, `DMA_LOAD=4`, `DMA_STORE=5`, `DMA_P2P_PUSH=6`, `DMA_PREFETCH=7`, `DMA_FILL=8`, `AXI_FENCE=9`, `GEMM=10`, `BMM=11`, `ELEMENTWISE=12`, `LOCAL_REDUCE=13`, `SOFTMAX=14`, `NORM=15`, `EVENT_WAIT=16`, `EVENT_SIGNAL=17`, `RECV_WAIT=18`, `BARRIER=19`, `REPEAT=20`
- `engine`: `CONTROL=1`, `DMA_READ=2`, `DMA_WRITE=3`, `TENSOR=4`, `VECTOR=5`, `REDUCE=6`
- `opcode_engine_map`: `REQUEST_BEGIN=CONTROL`, `REQUEST_END=CONTROL`, `HALT=CONTROL`, `EVENT_WAIT=CONTROL`, `EVENT_SIGNAL=CONTROL`, `BARRIER=CONTROL`, `AXI_FENCE=CONTROL`, `RECV_WAIT=CONTROL`, `REPEAT=CONTROL`, `DMA_LOAD=DMA_READ`, `DMA_PREFETCH=DMA_READ`, `DMA_STORE=DMA_WRITE`, `DMA_P2P_PUSH=DMA_WRITE`, `DMA_FILL=DMA_WRITE`, `GEMM=TENSOR`, `BMM=TENSOR`, `ELEMENTWISE=VECTOR`, `SOFTMAX=VECTOR`, `NORM=VECTOR`, `LOCAL_REDUCE=REDUCE`
- `memory_space`: `HBM=1`, `HOST_SHARED=2`, `CORE_SRAM=3`, `PEER_SRAM=4`
- `tensor_role`: `INPUT=1`, `OUTPUT=2`, `WEIGHT=3`, `CONSTANT=4`, `ACTIVATION=5`, `KV_CACHE=6`, `STATE=7`
- `dtype`: `FP32=1`, `FP16=2`, `BF16=3`, `INT8=4`, `INT32=5`
- `storage_class`: `EXTERNAL=1`, `HBM=2`, `HOST_SHARED=3`, `CORE_SRAM=4`, `PRE_RESIDENT=5`
- `access_kind`: `READ_ONLY=1`, `READ_WRITE=2`
- `layout_kind`: `CONTIGUOUS_ROW_MAJOR=1`, `TRANSPOSED_2D_VIEW=2`, `BLOCKED_MNK=3`
- `dma_kind`: `LOAD=1`, `STORE=2`, `P2P_PUSH=3`, `PREFETCH=4`, `LOCAL_FILL=5`
- `event_kind`: `NORMAL=1`, `BARRIER=2`
- `attr_kind`: `REPEAT_V1=1`, `GEMM_V1=2`, `BMM_V1=3`, `ELEMENTWISE_V1=4`, `REDUCE_V1=5`, `SOFTMAX_V1=6`, `NORM_V1=7`, `FILL_V1=8`, `BLOCKED_MNK_LAYOUT_V1=9`, `RECV_WAIT_V1=10`, `FENCE_V1=11`
- `relocation_kind`: `REGION_BASE=1`, `TENSOR_BASE=2`
- `vector_algorithm`: `STANDARD=1`
- `fence_scope`: `DMA_READ=1`, `DMA_WRITE=2`, `P2P=3`, `HOST_SHARED_WRITE=4`, `ALL_INSTANCE=5`
- `mesh_object_domain`: `STATIC=0`, `MOE_OVERLAY=1`, `WEIGHT_FILL=2`, `COORDINATOR=3`, `KV_RUNTIME=4`, `INTERNAL=5`
- `mesh_object_kind`: `COMMAND=0`, `EVENT=1`, `DESCRIPTOR=2`, `TRANSFER=3`, `ALLOCATION=4`, `VIEW=5`, `WEIGHT_FILL_OBLIGATION=6`, `CACHE_RESERVATION=7`, `MATERIALIZER_CHECK=8`, `KV_APPEND=9`, `INTERNAL_CHECK=10`
- `moe_error_class`: `WEIGHT_FILL=0`, `CACHE_RESERVATION=1`, `DMA_AXI=2`, `SRAM=3`, `COMMAND=4`, `EVENT=5`, `MATERIALIZE=6`, `INTERNAL=7`
- `weight_fill_failure_site`: `CACHE_FILL_AXI_R=0`, `CACHE_FILL_SRAM_BOUNDS=1`, `CACHE_FILL_SRAM_COMMIT=2`, `CACHE_FILL_SOURCE_VALIDITY=3`
- `cache_slot_state`: `INVALID=0`, `VALID=1`
- `cache_residency_outcome`: `HIT=0`, `ATTACH=1`, `NEW_FILL=2`, `STREAMED=3`
- `cache_subscriber_state`: `WAITING=0`, `WOKEN=1`, `FAIL_NOTIFIED=2`, `TERMINAL_TOMBSTONED=3`, `CANCELLED=4`, `RELEASED=5`
- `cache_obligation_state`: `RESERVED=0`, `ISSUING=1`, `IN_FLIGHT=2`, `FAILED_DRAINING=3`, `FAILED_RETIRED=4`, `RETIRED=5`
- `mesh_core_instance_state`: `PROGRAM_READY=0`, `REQUEST_ARMED=1`, `REQUEST_RUNNING=2`, `REQUEST_DRAINING=3`, `INSTANCE_DONE=4`, `INSTANCE_ERROR_DRAINING=5`, `INSTANCE_OWNED_WORK_DRAINED=6`, `INSTANCE_ERROR_DRAINED=7`
- `mesh_batch_state`: `PROGRAM_READY=0`, `REQUEST_ARMED=1`, `REQUEST_RUNNING=2`, `BATCH_PRESTART_ABORTING=3`, `BATCH_PRESTART_DRAINED=4`, `FINAL_DRAINING=5`, `GLOBAL_QUIESCENT=6`
- `sram_partition_kind`: `STATIC_PROGRAM=0`, `WEIGHT_CACHE=1`, `KV_STAGING_CACHE=2`, `RUNTIME_SCRATCH=3`
- `moe_allocation_kind`: `ROUTE_BUFFER=0`, `STREAMED_WEIGHT=1`, `DISPATCH_REMOTE=2`, `PAD_INPUT=3`, `EXPERT_OUTPUT=4`, `COMBINE_REMOTE=5`, `REDUCE_OUTPUT=6`
- `moe_view_kind`: `ROUTE_METADATA=0`, `MEMBER_INPUT=1`, `DISPATCH_BUFFER=2`, `PAD_BUFFER=3`, `WEIGHT=4`, `EXPERT_OUTPUT=5`, `COMBINE_BUFFER=6`, `REDUCE_ACCUMULATOR=7`, `MEMBER_OUTPUT=8`
- `moe_view_access`: `READ=0`, `WRITE=1`, `READ_WRITE=2`
- `moe_view_backing`: `OVERLAY_ALLOCATION=0`, `STATIC_ALLOCATION=1`, `INSTANCE_MEMBER_BINDING=2`, `WEIGHT_CACHE_SLOT=3`, `KV_RUNTIME_VIEW=4`
- `moe_semantic_owner`: `MEMBER=0`, `EXPERT=1`, `TOKEN=2`, `REGION=3`
- `moe_validity_kind`: `FULL_PREFIX=0`, `ROW_BITMAP=1`
- `moe_traffic_class`: `ACTIVATION=0`, `WEIGHT=1`, `MOE_DISPATCH=2`, `MOE_COMBINE=3`, `PARTIAL_RESULT=4`
- `moe_transfer_phase`: `DISPATCH=0`, `COMBINE=1`
- `moe_descriptor_kind`: `ROUTE_FILL=0`, `STREAMED_WEIGHT=1`, `PAD_FILL=2`, `DISPATCH=3`, `COMBINE=4`, `DROPPED_TOKEN_FILL=5`
- `moe_event_phase`: `ENTRY=0`, `FILL=1`, `DISPATCH=2`, `WEIGHT=3`, `EXPERT=4`, `COMBINE=5`, `REDUCE=6`, `EXIT=7`
- `moe_command_phase`: `ROUTE_FILL=0`, `STREAMED_WEIGHT_LOAD=1`, `DISPATCH_PUSH=2`, `DISPATCH_WAIT=3`, `PAD_FILL=4`, `EXPERT_COMPUTE=5`, `COMBINE_PUSH=6`, `COMBINE_WAIT=7`, `DROPPED_TOKEN_FILL=8`, `COPY_THROUGH=9`, `LOCAL_REDUCE=10`, `EXIT_SIGNAL=11`
- `moe_event_role`: `ENTRY_GATE=0`, `ROUTE_FILL_DONE=1`, `DISPATCH_READY=2`, `WEIGHT_READY=3`, `EXPERT_RESULT=4`, `COMBINE_READY=5`, `TOKEN_OUTPUT_READY=6`, `REGION_TERMINAL=7`, `EMPTY_REGION_TERMINAL=8`, `OVERLAY_EXIT=9`, `PAD_FILL_DONE=10`
- `moe_command_role`: `ROUTE_FILL=0`, `STREAMED_WEIGHT_LOAD=1`, `DISPATCH_SENDER=2`, `DISPATCH_RECEIVER=3`, `PAD_FILL=4`, `EXPERT_COMPUTE=5`, `COMBINE_SENDER=6`, `COMBINE_RECEIVER=7`, `DROPPED_TOKEN_FILL=8`, `COPY_THROUGH=9`, `LOCAL_REDUCE=10`, `REGION_TERMINAL=11`, `EMPTY_REGION_TERMINAL=12`, `GROUP_EXIT=13`
- `route_disposition`: `ACCEPT=0`, `DROP=1`
- `moe_fill_mode`: `FUNCTIONAL_BYTES=0`, `DIGEST_ONLY=1`, `VALIDITY_ONLY=2`
- `moe_overflow_policy`: `DROP=0`, `PAD_TO_CAPACITY=1`, `FAIL=2`
- `moe_transport_mode`: `VARIABLE_ALL_TO_ALL_V=0`
- `moe_combine_kind`: `NONE=0`, `LOCAL_REDUCE=1`
- `stream_flags`: `IS_LIFECYCLE=1`, `IS_LOCAL_CONTROL=2`
- `tensor_flags`: `HAS_CONTENT_SHA256=1`
- `agent_request_flags`: `HAS_KV=1`
- `path_kind`: `INITIAL_PREFILL=0`, `KV_REUSE=1`, `REPREFILL=2`
- `phase`: `PREFILL=1`, `DECODE=2`, `PUBLISH=3`
- `dma_fill_kind`: `CONSTANT_PATTERN=0`, `AGENT_OUTPUT_SURROGATE=1`
- `agent_allocation_role`: `NONE=0`, `PUBLISH_SURROGATE_SOURCE=1`
- `agent_digest_source`: `REQUEST_SEMANTIC_OUTPUT_DIGEST=0`

## Records

### WEIGHT_CACHE_BASE_KEYS (12 B)

| field | type | offset |
|---|---|---:|
| `core_id` | u16 | 0 |
| `cache_partition_id` | u16 | 2 |
| `weight_tag_index` | u32 | 4 |
| `cache_generation` | u32 | 8 |

### WEIGHT_FILL_KEYS (16 B)

| field | type | offset |
|---|---|---:|
| `core_id` | u16 | 0 |
| `cache_partition_id` | u16 | 2 |
| `weight_tag_index` | u32 | 4 |
| `cache_generation` | u32 | 8 |
| `fill_incarnation` | u32 | 12 |

### CACHE_DMA_DESCRIPTOR_KEYS (20 B)

| field | type | offset |
|---|---|---:|
| `weight_fill_key` | bytes16 | 0 |
| `segment_ordinal` | u32 | 16 |

### ERROR_SOURCE_KEYS (40 B)

| field | type | offset |
|---|---|---:|
| `error_class` | u16 | 0 |
| `core_id_or_ffff` | u16 | 2 |
| `domain` | u8 | 4 |
| `object_kind` | u8 | 5 |
| `reserved` | u16 | 6 |
| `region_group_id` | u32 | 8 |
| `region_id` | u32 | 12 |
| `ordinal` | u32 | 16 |
| `generation` | u32 | 20 |
| `aux_key` | bytes16 | 24 |

### ENTRYPOINTS (24 B)

| field | type | offset |
|---|---|---:|
| `entrypoint_id` | u32 | 0 |
| `name_sid` | u32 | 4 |
| `profile_begin` | u32 | 8 |
| `profile_count` | u16 | 12 |
| `lifecycle_core_id` | u16 | 14 |
| `lifecycle_stream_id` | u16 | 16 |
| `flags` | u16 | 18 |
| `reserved` | u32 | 20 |

### PROFILES (80 B)

| field | type | offset |
|---|---|---:|
| `profile_id` | u32 | 0 |
| `entrypoint_id` | u32 | 4 |
| `name_sid` | u32 | 8 |
| `rank` | u16 | 12 |
| `reserved` | u16 | 14 |
| `dims` | u64x8 | 16 |

### TENSORS (136 B)

| field | type | offset |
|---|---|---:|
| `tensor_id` | u32 | 0 |
| `name_sid` | u32 | 4 |
| `role` | u16 | 8 |
| `dtype` | u16 | 10 |
| `storage_class` | u16 | 12 |
| `access` | u16 | 14 |
| `rank` | u16 | 16 |
| `layout` | u16 | 18 |
| `layout_attr` | u32 | 20 |
| `placement_id` | u32 | 24 |
| `sharding_id` | u32 | 28 |
| `flags` | u32 | 32 |
| `reserved` | u32 | 36 |
| `dims` | u64x8 | 40 |
| `content_sha256` | bytes32 | 104 |

### SHARDS (240 B)

| field | type | offset |
|---|---|---:|
| `shard_id` | u32 | 0 |
| `tensor_id` | u32 | 4 |
| `sharding_id` | u32 | 8 |
| `owner_core` | u16 | 12 |
| `rank` | u16 | 14 |
| `reserved` | u16 | 16 |
| `flags` | u16 | 18 |
| `reserved0` | u32 | 20 |
| `global_origin` | u64x8 | 24 |
| `local_shape` | u64x8 | 88 |
| `valid_shape` | u64x8 | 152 |
| `allocation_id` | u32 | 216 |
| `reserved2` | u32 | 220 |
| `allocation_offset` | u64 | 224 |
| `span_bytes` | u64 | 232 |

### ALLOCATIONS (32 B)

| field | type | offset |
|---|---|---:|
| `allocation_id` | u32 | 0 |
| `owner_core` | u16 | 4 |
| `memory_space` | u16 | 6 |
| `offset_bytes` | u64 | 8 |
| `size_bytes` | u64 | 16 |
| `alignment_bytes` | u32 | 24 |
| `flags` | u32 | 28 |

### STREAMS (16 B)

| field | type | offset |
|---|---|---:|
| `core_id` | u16 | 0 |
| `stream_id` | u16 | 2 |
| `command_begin` | u32 | 4 |
| `command_count` | u32 | 8 |
| `flags` | u16 | 12 |
| `reserved` | u16 | 14 |

### COMMANDS (40 B)

| field | type | offset |
|---|---|---:|
| `command_id` | u32 | 0 |
| `source_op_id` | u32 | 4 |
| `core_id` | u16 | 8 |
| `stream_id` | u16 | 10 |
| `engine` | u16 | 12 |
| `opcode` | u16 | 14 |
| `wait_begin` | u32 | 16 |
| `wait_count` | u16 | 20 |
| `operand_count` | u16 | 22 |
| `operand_begin` | u32 | 24 |
| `signal_event` | u32 | 28 |
| `attr_index` | u32 | 32 |
| `debug_loc_id` | u32 | 36 |

### COMMAND_WAITS (4 B)

| field | type | offset |
|---|---|---:|
| `event_id` | u32 | 0 |

### COMMAND_OPERANDS (16 B)

| field | type | offset |
|---|---|---:|
| `tensor_id` | u32 | 0 |
| `shard_id` | u32 | 4 |
| `allocation_id` | u32 | 8 |
| `access` | u16 | 12 |
| `reserved` | u16 | 14 |

### EVENTS (20 B)

| field | type | offset |
|---|---|---:|
| `event_id` | u32 | 0 |
| `kind` | u16 | 4 |
| `reserved` | u16 | 6 |
| `producer_command_id` | u32 | 8 |
| `expected_arrivals` | u32 | 12 |
| `reserved2` | u32 | 16 |

### DMA_ENDPOINT (24 B)

| field | type | offset |
|---|---|---:|
| `memory_space` | u16 | 0 |
| `region_id` | u16 | 2 |
| `owner_core` | u16 | 4 |
| `tensor_id` | u32 | 6 |
| `shard_id` | u32 | 10 |
| `reserved` | u16 | 14 |
| `offset_bytes` | u64 | 16 |

### DMA_DESCRIPTORS (120 B)

| field | type | offset |
|---|---|---:|
| `descriptor_id` | u32 | 0 |
| `command_id` | u32 | 4 |
| `transfer_id` | u32 | 8 |
| `owner_core` | u16 | 12 |
| `kind` | u16 | 14 |
| `src` | record_ref | 16 |
| `dst` | record_ref | 40 |
| `rows` | u32 | 64 |
| `row_bytes` | u64 | 68 |
| `src_stride_bytes` | u64 | 76 |
| `dst_stride_bytes` | u64 | 84 |
| `useful_bytes` | u64 | 92 |
| `physical_storage_bytes` | u64 | 100 |
| `axi_id` | u16 | 108 |
| `qos` | u8 | 110 |
| `reserved` | u8 | 111 |
| `max_burst_beats` | u16 | 112 |
| `reserved2` | u16 | 114 |
| `completion_event` | u32 | 116 |

### OP_ATTRS (32 B)

| field | type | offset |
|---|---|---:|
| `kind` | u16 | 0 |
| `reserved` | u16 | 2 |
| `payload` | bytes28 | 4 |

### RELOCATIONS (32 B)

| field | type | offset |
|---|---|---:|
| `relocation_id` | u32 | 0 |
| `symbol_sid` | u32 | 4 |
| `kind` | u16 | 8 |
| `region_id` | u16 | 10 |
| `tensor_id` | u32 | 12 |
| `reserved` | u32 | 16 |
| `offset_bytes` | u64 | 20 |
| `reserved2` | u32 | 28 |

### EXPECTED_TRAFFIC (72 B)

| field | type | offset |
|---|---|---:|
| `entrypoint_id` | u32 | 0 |
| `profile_id` | u32 | 4 |
| `command_id` | u32 | 8 |
| `descriptor_id` | u32 | 12 |
| `kind` | u16 | 16 |
| `reserved` | u16 | 18 |
| `useful_bytes` | u64 | 20 |
| `physical_beat_bytes` | u64 | 28 |
| `segments` | u32 | 36 |
| `bursts` | u32 | 40 |
| `ar_count` | u32 | 44 |
| `r_beats` | u32 | 48 |
| `aw_count` | u32 | 52 |
| `w_beats` | u32 | 56 |
| `b_count` | u32 | 60 |
| `min_flits` | u32 | 64 |
| `reserved2` | u32 | 68 |

### PROFILE_STREAM_RANGES (16 B)

| field | type | offset |
|---|---|---:|
| `profile_id` | u32 | 0 |
| `core_id` | u16 | 4 |
| `stream_id` | u16 | 6 |
| `command_begin` | u32 | 8 |
| `command_count` | u32 | 12 |

### SOURCE_MAP (16 B)

| field | type | offset |
|---|---|---:|
| `loc_id` | u32 | 0 |
| `file_sid` | u32 | 4 |
| `line` | u32 | 8 |
| `column` | u32 | 12 |

### CONTENT_DIGESTS (40 B)

| field | type | offset |
|---|---|---:|
| `object_kind` | u16 | 0 |
| `reserved` | u16 | 2 |
| `object_id` | u32 | 4 |
| `digest` | bytes32 | 8 |

### MOE_RUNTIME_ROUTE_ENTRY (64 B)

| field | type | offset |
|---|---|---:|
| `semantic_token_uid` | bytes32 | 0 |
| `member_request_id` | u64 | 32 |
| `source_rank` | u32 | 40 |
| `token_ordinal` | u32 | 44 |
| `topk_slot` | u16 | 48 |
| `selected_expert_id` | u16 | 50 |
| `assigned_expert_id` | u16 | 52 |
| `destination_core` | u16 | 54 |
| `disposition` | u8 | 56 |
| `reserved` | bytes7 | 57 |

### MOE_LAYER_SPECS (80 B)

| field | type | offset |
|---|---|---:|
| `layer_id` | u32 | 0 |
| `kernel_spec_index` | u32 | 4 |
| `expert_first` | u32 | 8 |
| `expert_count` | u16 | 12 |
| `top_k` | u16 | 14 |
| `token_bytes` | u32 | 16 |
| `output_token_bytes` | u32 | 20 |
| `capacity_factor_q16` | u32 | 24 |
| `overflow_policy` | u16 | 28 |
| `transport_mode` | u16 | 30 |
| `dynamic_region_first` | u32 | 32 |
| `dynamic_region_count` | u32 | 36 |
| `max_tokens_per_frozen_batch` | u32 | 40 |
| `max_requests_per_batch` | u32 | 44 |
| `max_routes` | u32 | 48 |
| `max_materialized_commands` | u32 | 52 |
| `max_materialized_descriptors` | u32 | 56 |
| `max_materialized_transfers` | u32 | 60 |
| `max_dynamic_allocations` | u32 | 64 |
| `flags` | u32 | 68 |
| `max_materialized_events` | u32 | 72 |
| `reserved1` | u32 | 76 |

### MOE_EXPERT_SPECS (40 B)

| field | type | offset |
|---|---|---:|
| `layer_id` | u32 | 0 |
| `expert_id` | u16 | 4 |
| `flags` | u16 | 6 |
| `core_id` | u16 | 8 |
| `reserved_core` | u16 | 10 |
| `weight_symbol_id` | u32 | 12 |
| `weight_region_offset` | u64 | 16 |
| `weight_bytes` | u64 | 24 |
| `weight_digest_index` | u32 | 32 |
| `reserved` | u32 | 36 |

### MOE_DYNAMIC_REGIONS (72 B)

| field | type | offset |
|---|---|---:|
| `region_id` | u32 | 0 |
| `layer_id` | u32 | 4 |
| `core_id` | u16 | 8 |
| `stream_id` | u16 | 10 |
| `insert_after_command_id` | u32 | 12 |
| `resume_before_command_id` | u32 | 16 |
| `entry_event_id` | u32 | 20 |
| `scratch_offset` | u64 | 24 |
| `scratch_bytes` | u64 | 32 |
| `scratch_alignment` | u32 | 40 |
| `max_overlay_commands` | u32 | 44 |
| `max_overlay_events` | u32 | 48 |
| `max_overlay_descriptors` | u32 | 52 |
| `max_overlay_transfers` | u32 | 56 |
| `max_overlay_allocations` | u32 | 60 |
| `flags` | u32 | 64 |
| `reserved` | u32 | 68 |

### MOE_KERNEL_SPECS (88 B)

| field | type | offset |
|---|---|---:|
| `layer_id` | u32 | 0 |
| `expert_opcode` | u16 | 4 |
| `input_dtype` | u16 | 6 |
| `accum_dtype` | u16 | 8 |
| `output_dtype` | u16 | 10 |
| `batch` | u32 | 12 |
| `n` | u32 | 16 |
| `k` | u32 | 20 |
| `transpose_flags` | u16 | 24 |
| `combine_kind` | u16 | 26 |
| `algorithm_id` | u32 | 28 |
| `efficiency_q16` | u32 | 32 |
| `tensor_setup_cycles` | u32 | 36 |
| `tensor_flush_cycles` | u32 | 40 |
| `input_token_bytes` | u32 | 44 |
| `output_token_bytes` | u32 | 48 |
| `reserved0` | u32 | 52 |
| `weight_operand_bytes` | u64 | 56 |
| `expert_result_alignment` | u32 | 64 |
| `max_m` | u32 | 68 |
| `combine_setup_cycles` | u32 | 72 |
| `combine_flush_cycles` | u32 | 76 |
| `flags` | u32 | 80 |
| `reserved1` | u32 | 84 |

### AGENT_REQUEST_PROFILES (96 B)

| field | type | offset |
|---|---|---:|
| `program_id` | u16 | 0 |
| `profile_id` | u16 | 2 |
| `flags` | u16 | 4 |
| `reserved0` | u16 | 6 |
| `requested_profile_key` | u64 | 8 |
| `delta_input_tokens` | u32 | 16 |
| `full_input_tokens` | u32 | 20 |
| `expected_cached_tokens` | u32 | 24 |
| `output_tokens` | u32 | 28 |
| `input_binding_bytes` | u64 | 32 |
| `delta_input_dma_bytes` | u64 | 40 |
| `full_input_dma_bytes` | u64 | 48 |
| `host_output_bytes` | u64 | 56 |
| `primary_input_symbol_id` | u32 | 64 |
| `primary_output_symbol_id` | u32 | 68 |
| `primary_kv_symbol_id` | u32 | 72 |
| `source_rank_count` | u32 | 76 |
| `source_core_map_begin` | u32 | 80 |
| `path_mask` | u32 | 84 |
| `kv_bytes_per_token` | u32 | 88 |
| `publish_chunk_bytes` | u32 | 92 |

### AGENT_INSTANCE_PROFILES (96 B)

| field | type | offset |
|---|---|---:|
| `instance_profile_id` | u32 | 0 |
| `request_program_id` | u16 | 4 |
| `request_profile_id` | u16 | 6 |
| `path_kind` | u16 | 8 |
| `phase` | u16 | 10 |
| `member_count` | u16 | 12 |
| `decode_chunk_tokens` | u16 | 14 |
| `mesh_entrypoint_id` | u32 | 16 |
| `mesh_profile_id` | u32 | 20 |
| `valid_tokens_per_member` | u32 | 24 |
| `kv_tokens_before` | u32 | 28 |
| `local_padded_members` | u32 | 32 |
| `local_padded_tokens_per_member` | u32 | 36 |
| `primary_input_symbol_id` | u32 | 40 |
| `primary_output_symbol_id` | u32 | 44 |
| `primary_kv_symbol_id` | u32 | 48 |
| `flags` | u32 | 52 |
| `member_binding_first` | u32 | 56 |
| `member_binding_count` | u32 | 60 |
| `host_input_dma_bytes_per_member` | u64 | 64 |
| `host_output_dma_bytes_per_member` | u64 | 72 |
| `kv_read_bytes_per_member` | u64 | 80 |
| `kv_write_bytes_per_member` | u64 | 88 |

### AGENT_SOURCE_CORE_MAP (4 B)

| field | type | offset |
|---|---|---:|
| `core_id` | u16 | 0 |
| `reserved` | u16 | 2 |

### AGENT_INSTANCE_MEMBER_BINDINGS (24 B)

| field | type | offset |
|---|---|---:|
| `instance_profile_id` | u32 | 0 |
| `member_ordinal` | u16 | 4 |
| `reserved0` | u16 | 6 |
| `static_input_symbol_id` | u32 | 8 |
| `static_output_symbol_id` | u32 | 12 |
| `static_kv_symbol_id` | u32 | 16 |
| `expected_logical_source_rank` | u32 | 20 |

### AGENT_REQUEST_BINDING_REQUIREMENTS (16 B)

| field | type | offset |
|---|---|---:|
| `request_program_id` | u16 | 0 |
| `request_profile_id` | u16 | 2 |
| `binding_kind` | u16 | 4 |
| `binding_flags` | u16 | 6 |
| `symbol_id` | u32 | 8 |
| `reserved` | u32 | 12 |

### AGENT_PUBLISH_SURROGATE_BINDINGS (28 B)

| field | type | offset |
|---|---|---:|
| `instance_profile_id` | u32 | 0 |
| `member_ordinal` | u16 | 4 |
| `reserved0` | u16 | 6 |
| `allocation_id` | u32 | 8 |
| `producer_command_id` | u32 | 12 |
| `completion_event_id` | u32 | 16 |
| `fill_kind` | u16 | 20 |
| `allocation_role` | u16 | 22 |
| `digest_source` | u16 | 24 |
| `reserved1` | u16 | 26 |

## Attr payloads

- `REPEAT_V1` (16 B): `subrange_begin_stream_ordinal`@0, `subrange_command_count`@4, `repeat_count`@8, `flags`@12
- `GEMM_V1` (28 B): `batch`@0, `m`@4, `n`@8, `k`@12, `a_transpose`@16, `b_transpose`@17, `dtype`@18, `accum_dtype`@20, `epilogue`@22, `efficiency_q16`@24
- `BMM_V1` (28 B): `batch`@0, `m`@4, `n`@8, `k`@12, `a_transpose`@16, `b_transpose`@17, `dtype`@18, `accum_dtype`@20, `epilogue`@22, `efficiency_q16`@24
- `ELEMENTWISE_V1` (16 B): `element_count`@0, `dtype`@8, `op`@10, `ops_per_element`@12, `reserved`@14
- `REDUCE_V1` (16 B): `element_count`@0, `dtype`@8, `accum_dtype`@10, `op`@12, `fan_in`@14
- `SOFTMAX_V1` (16 B): `axis_size`@0, `dtype`@8, `algorithm`@10, `reserved`@12
- `NORM_V1` (16 B): `element_count`@0, `dtype`@8, `algorithm`@10, `reserved`@12
- `FILL_V1` (16 B): `pattern`@0, `reserved`@8
- `BLOCKED_MNK_LAYOUT_V1` (16 B): `block_m`@0, `block_n`@4, `block_k`@8, `minor_to_major`@12, `reserved`@14
- `RECV_WAIT_V1` (16 B): `transfer_id`@0, `reserved0`@4, `reserved1`@8, `reserved2`@12
- `FENCE_V1` (16 B): `fence_scope`@0, `reserved`@2, `reserved0`@4, `reserved1`@8, `reserved2`@12
