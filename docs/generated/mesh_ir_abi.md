# Mesh IR ABI Reference

Generated from `util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml` -- do not edit.

- schema_sha256: `0b725be8d899c429675be49e2f5bccced924850ce9cee7a384c56c56b6909310`
- ABI: 1.2 (min reader minor 0)
- magic: `4d53484200000001`

## Enums

- `section_type`: `STRINGS=1`, `ENTRYPOINTS=2`, `PROFILES=3`, `TENSORS=4`, `SHARDS=5`, `ALLOCATIONS=6`, `STREAMS=7`, `COMMANDS=8`, `COMMAND_WAITS=9`, `COMMAND_OPERANDS=10`, `EVENTS=11`, `DMA_DESCRIPTORS=12`, `OP_ATTRS=13`, `RELOCATIONS=14`, `EXPECTED_TRAFFIC=15`, `SOURCE_MAP=101`, `PROFILE_HINTS=102`, `CONTENT_DIGESTS=103`
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
- `stream_flags`: `IS_LIFECYCLE=1`, `IS_LOCAL_CONTROL=2`
- `tensor_flags`: `HAS_CONTENT_SHA256=1`

## Records

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
