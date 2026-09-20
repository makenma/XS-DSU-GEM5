# Mesh IR ABI Reference

Generated from `util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml` -- do not edit.

- schema_sha256: `87abcecd302eeea330666c2bf11c4e50e1bf52c201a979a78cc87f486f5d544f`
- ABI: 1.3 (min reader minor 3)
- magic: `4d53484200000001`

## Enums

- `section_type`: `STRINGS=1`, `ENTRYPOINTS=2`, `PROFILES=3`, `TENSORS=4`, `SHARDS=5`, `ALLOCATIONS=6`, `STREAMS=7`, `COMMANDS=8`, `COMMAND_WAITS=9`, `COMMAND_OPERANDS=10`, `EVENTS=11`, `DMA_DESCRIPTORS=12`, `OP_ATTRS=13`, `RELOCATIONS=14`, `EXPECTED_TRAFFIC=15`, `PROGRAM_METADATA=16`, `SEMANTIC_STRINGS=17`, `SEMANTIC_U64_VALUES=18`, `SEMANTIC_I64_VALUES=19`, `SEMANTIC_REFERENCES=20`, `SEMANTIC_BYTES=21`, `SEMANTIC_INTEGER_VALUES=22`, `SOURCE_MAP=101`, `PROFILE_HINTS=102`, `CONTENT_DIGESTS=103`, `SEMANTIC_EXECUTION_WORK_PHASE=256`, `SEMANTIC_WORK_ESTIMATE=257`, `SEMANTIC_ADD=258`, `SEMANTIC_CEIL_DIV_BY_CONST=259`, `SEMANTIC_CONST=260`, `SEMANTIC_FLOOR_DIV_BY_CONST=261`, `SEMANTIC_MUL_BY_CONST=262`, `SEMANTIC_SYMBOL=263`, `SEMANTIC_ELEMENTWISE_ATTRS=264`, `SEMANTIC_EMBEDDING_ATTRS=265`, `SEMANTIC_MATMUL_ATTRS=266`, `SEMANTIC_MOVEMENT_ATTRS=267`, `SEMANTIC_NORM_ATTRS=268`, `SEMANTIC_REDUCE_ATTRS=269`, `SEMANTIC_SOFTMAX_ATTRS=270`, `SEMANTIC_VIEW_ATTRS=271`, `SEMANTIC_ALLOC_ATTRS=272`, `SEMANTIC_BARRIER_ATTRS=273`, `SEMANTIC_BLOCKED_MNK_LAYOUT=274`, `SEMANTIC_BUFFER_OBJECT=275`, `SEMANTIC_BUFFER_VIEW=276`, `SEMANTIC_COLLECTIVE_ATTRS=277`, `SEMANTIC_CONTROL_TOKEN=278`, `SEMANTIC_DMA_ATTRS=279`, `SEMANTIC_ELEMENT_REGION=280`, `SEMANTIC_GEMM_KERNEL_ATTRS=281`, `SEMANTIC_KERNEL_COMPUTATION=282`, `SEMANTIC_KERNEL_COST=283`, `SEMANTIC_KERNEL_OP=284`, `SEMANTIC_KERNEL_TENSOR=285`, `SEMANTIC_KERNEL_TILE=286`, `SEMANTIC_LOCAL_COPY_ATTRS=287`, `SEMANTIC_LOCAL_REDUCE_ATTRS=288`, `SEMANTIC_MATRIX_EPILOGUE_KERNEL_ATTRS=289`, `SEMANTIC_MOVEMENT_KERNEL_ATTRS=290`, `SEMANTIC_NORM_KERNEL_ATTRS=291`, `SEMANTIC_OPERAND_ACCESS=292`, `SEMANTIC_PARTIAL_SUM_DEFINITION=293`, `SEMANTIC_PLACEMENT=294`, `SEMANTIC_RECV_WAIT_ATTRS=295`, `SEMANTIC_REDUCTION_KERNEL_ATTRS=296`, `SEMANTIC_SOFTMAX_KERNEL_ATTRS=297`, `SEMANTIC_STATE_TRANSITION=298`, `SEMANTIC_TENSOR_SHARD=299`, `SEMANTIC_TENSOR_STATE=300`, `SEMANTIC_VECTOR_KERNEL_ATTRS=301`, `SEMANTIC_VIEW_DECLARATION_ATTRS=302`, `SEMANTIC_AUTHORED_PROGRAM_ORIGIN=303`, `SEMANTIC_AUTHORED_VARIANT_LINEAGE=304`, `SEMANTIC_AXI_FENCE_ATTRS=305`, `SEMANTIC_BARRIER_ARRIVAL=306`, `SEMANTIC_BARRIER_EXECUTION=307`, `SEMANTIC_BARRIER_GROUP=308`, `SEMANTIC_COMMAND_SEMANTICS=309`, `SEMANTIC_COMPILED_PROGRAM_ORIGIN=310`, `SEMANTIC_COMPILED_VARIANT_LINEAGE=311`, `SEMANTIC_COMPUTE_EXECUTION=312`, `SEMANTIC_CONTROL_COMMAND_SOURCE=313`, `SEMANTIC_CONTROL_EXECUTION=314`, `SEMANTIC_DESCRIPTOR_ENDPOINT_USE=315`, `SEMANTIC_DESCRIPTOR_GROUP=316`, `SEMANTIC_DESCRIPTOR_SOURCE=317`, `SEMANTIC_DMA_EXECUTION=318`, `SEMANTIC_EVENT_SIGNAL_ATTRS=319`, `SEMANTIC_EVENT_WAIT_ATTRS=320`, `SEMANTIC_EXTERNAL_SLOT_BACKING=321`, `SEMANTIC_HALT_ATTRS=322`, `SEMANTIC_ID_SPAN=323`, `SEMANTIC_KERNEL_COMMAND_SOURCE=324`, `SEMANTIC_KERNEL_TOKEN_SOURCE=325`, `SEMANTIC_LIFECYCLE_SOURCE=326`, `SEMANTIC_LOCAL_ALLOCATION_BACKING=327`, `SEMANTIC_OBJECT_BACKING=328`, `SEMANTIC_OBJECT_SOURCE=329`, `SEMANTIC_PROGRAM_SEMANTICS=330`, `SEMANTIC_PROGRAM_VARIANT=331`, `SEMANTIC_READ_ACCESS_USE=332`, `SEMANTIC_RECV_WAIT_EXECUTION=333`, `SEMANTIC_REPEAT_COMMAND_ATTRS=334`, `SEMANTIC_REQUEST_BEGIN_ATTRS=335`, `SEMANTIC_REQUEST_END_ATTRS=336`, `SEMANTIC_RESIDENT_VIEW=337`, `SEMANTIC_SCHEDULED_DEPENDENCY=338`, `SEMANTIC_SCHEDULED_STREAM=339`, `SEMANTIC_STATE_SOURCE=340`, `SEMANTIC_STREAM_ORDER_SOURCE=341`, `SEMANTIC_VARIANT_MEMBERSHIP=342`, `SEMANTIC_WRITE_ACCESS_USE=343`, `SEMANTIC_BINDING=344`, `SEMANTIC_BINDING_SLOT=345`, `SEMANTIC_CHANNEL_TRAFFIC=346`, `SEMANTIC_DESCRIPTOR_IDENTITY=347`, `SEMANTIC_DESCRIPTOR_TRAFFIC=348`, `SEMANTIC_TRAFFIC_AGGREGATE=349`, `SEMANTIC_TRAFFIC_AGGREGATE_KEY=350`, `SEMANTIC_TRAFFIC_CHANNEL_TOTAL=351`, `SEMANTIC_TRAFFIC_REPORT=352`
- `required_sections` (required): STRINGS, ENTRYPOINTS, PROFILES, TENSORS, SHARDS, ALLOCATIONS, STREAMS, COMMANDS, COMMAND_WAITS, COMMAND_OPERANDS, EVENTS, DMA_DESCRIPTORS, OP_ATTRS, RELOCATIONS, EXPECTED_TRAFFIC, PROGRAM_METADATA, SEMANTIC_STRINGS, SEMANTIC_U64_VALUES, SEMANTIC_I64_VALUES, SEMANTIC_REFERENCES, SEMANTIC_BYTES, SEMANTIC_INTEGER_VALUES, SEMANTIC_EXECUTION_WORK_PHASE, SEMANTIC_WORK_ESTIMATE, SEMANTIC_ADD, SEMANTIC_CEIL_DIV_BY_CONST, SEMANTIC_CONST, SEMANTIC_FLOOR_DIV_BY_CONST, SEMANTIC_MUL_BY_CONST, SEMANTIC_SYMBOL, SEMANTIC_ELEMENTWISE_ATTRS, SEMANTIC_EMBEDDING_ATTRS, SEMANTIC_MATMUL_ATTRS, SEMANTIC_MOVEMENT_ATTRS, SEMANTIC_NORM_ATTRS, SEMANTIC_REDUCE_ATTRS, SEMANTIC_SOFTMAX_ATTRS, SEMANTIC_VIEW_ATTRS, SEMANTIC_ALLOC_ATTRS, SEMANTIC_BARRIER_ATTRS, SEMANTIC_BLOCKED_MNK_LAYOUT, SEMANTIC_BUFFER_OBJECT, SEMANTIC_BUFFER_VIEW, SEMANTIC_COLLECTIVE_ATTRS, SEMANTIC_CONTROL_TOKEN, SEMANTIC_DMA_ATTRS, SEMANTIC_ELEMENT_REGION, SEMANTIC_GEMM_KERNEL_ATTRS, SEMANTIC_KERNEL_COMPUTATION, SEMANTIC_KERNEL_COST, SEMANTIC_KERNEL_OP, SEMANTIC_KERNEL_TENSOR, SEMANTIC_KERNEL_TILE, SEMANTIC_LOCAL_COPY_ATTRS, SEMANTIC_LOCAL_REDUCE_ATTRS, SEMANTIC_MATRIX_EPILOGUE_KERNEL_ATTRS, SEMANTIC_MOVEMENT_KERNEL_ATTRS, SEMANTIC_NORM_KERNEL_ATTRS, SEMANTIC_OPERAND_ACCESS, SEMANTIC_PARTIAL_SUM_DEFINITION, SEMANTIC_PLACEMENT, SEMANTIC_RECV_WAIT_ATTRS, SEMANTIC_REDUCTION_KERNEL_ATTRS, SEMANTIC_SOFTMAX_KERNEL_ATTRS, SEMANTIC_STATE_TRANSITION, SEMANTIC_TENSOR_SHARD, SEMANTIC_TENSOR_STATE, SEMANTIC_VECTOR_KERNEL_ATTRS, SEMANTIC_VIEW_DECLARATION_ATTRS, SEMANTIC_AUTHORED_PROGRAM_ORIGIN, SEMANTIC_AUTHORED_VARIANT_LINEAGE, SEMANTIC_AXI_FENCE_ATTRS, SEMANTIC_BARRIER_ARRIVAL, SEMANTIC_BARRIER_EXECUTION, SEMANTIC_BARRIER_GROUP, SEMANTIC_COMMAND_SEMANTICS, SEMANTIC_COMPILED_PROGRAM_ORIGIN, SEMANTIC_COMPILED_VARIANT_LINEAGE, SEMANTIC_COMPUTE_EXECUTION, SEMANTIC_CONTROL_COMMAND_SOURCE, SEMANTIC_CONTROL_EXECUTION, SEMANTIC_DESCRIPTOR_ENDPOINT_USE, SEMANTIC_DESCRIPTOR_GROUP, SEMANTIC_DESCRIPTOR_SOURCE, SEMANTIC_DMA_EXECUTION, SEMANTIC_EVENT_SIGNAL_ATTRS, SEMANTIC_EVENT_WAIT_ATTRS, SEMANTIC_EXTERNAL_SLOT_BACKING, SEMANTIC_HALT_ATTRS, SEMANTIC_ID_SPAN, SEMANTIC_KERNEL_COMMAND_SOURCE, SEMANTIC_KERNEL_TOKEN_SOURCE, SEMANTIC_LIFECYCLE_SOURCE, SEMANTIC_LOCAL_ALLOCATION_BACKING, SEMANTIC_OBJECT_BACKING, SEMANTIC_OBJECT_SOURCE, SEMANTIC_PROGRAM_SEMANTICS, SEMANTIC_PROGRAM_VARIANT, SEMANTIC_READ_ACCESS_USE, SEMANTIC_RECV_WAIT_EXECUTION, SEMANTIC_REPEAT_COMMAND_ATTRS, SEMANTIC_REQUEST_BEGIN_ATTRS, SEMANTIC_REQUEST_END_ATTRS, SEMANTIC_RESIDENT_VIEW, SEMANTIC_SCHEDULED_DEPENDENCY, SEMANTIC_SCHEDULED_STREAM, SEMANTIC_STATE_SOURCE, SEMANTIC_STREAM_ORDER_SOURCE, SEMANTIC_VARIANT_MEMBERSHIP, SEMANTIC_WRITE_ACCESS_USE, SEMANTIC_BINDING, SEMANTIC_BINDING_SLOT, SEMANTIC_CHANNEL_TRAFFIC, SEMANTIC_DESCRIPTOR_IDENTITY, SEMANTIC_DESCRIPTOR_TRAFFIC, SEMANTIC_TRAFFIC_AGGREGATE, SEMANTIC_TRAFFIC_AGGREGATE_KEY, SEMANTIC_TRAFFIC_CHANNEL_TOTAL, SEMANTIC_TRAFFIC_REPORT
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
- `scalar_kind`: `BOOL=1`, `I64=2`, `U64=3`, `F64=4`
- `content_digest_object_kind`: `TENSOR=1`, `SHARD=2`, `ALLOCATION=3`, `KERNEL_OBJECT=4`

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

### PROGRAM_METADATA (48 B)

| field | type | offset |
|---|---|---:|
| `min_reader_minor` | u16 | 0 |
| `flags` | u16 | 2 |
| `reserved` | u32 | 4 |
| `semantics` | semantic_ref | 8 |
| `semantic_sha256` | bytes32 | 16 |

### SEMANTIC_U64_VALUES (8 B)

| field | type | offset |
|---|---|---:|
| `value` | u64 | 0 |

### SEMANTIC_I64_VALUES (8 B)

| field | type | offset |
|---|---|---:|
| `value` | i64 | 0 |

### SEMANTIC_REFERENCES (8 B)

| field | type | offset |
|---|---|---:|
| `value` | semantic_ref | 0 |

### SEMANTIC_BYTES (1 B)

| field | type | offset |
|---|---|---:|
| `value` | u8 | 0 |

### SEMANTIC_INTEGER_VALUES (16 B)

| field | type | offset |
|---|---|---:|
| `kind` | u16 | 0 |
| `reserved` | bytes6 | 2 |
| `payload` | u64 | 8 |

### PROFILE_HINTS (16 B)

| field | type | offset |
|---|---|---:|
| `entrypoint_id` | u32 | 0 |
| `profile_id` | u32 | 4 |
| `name_sid` | u32 | 8 |
| `value_sid` | u32 | 12 |

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

## Required features

- `SCHEDULED_SEMANTICS`: bit 1, minimum reader minor 3

## Semantic enums

- `mesh_ir.analysis.cost.WorkUnit`: MAC=1, ADD=2, SUB=3, MUL=4, DIV=5, MAX=6, EXP=7, ERF=8, TANH=9, NEGATE=10, RSQRT=11, COPY=12, GATHER=13, PREDICATE=14, LOGICAL_AND=15, SELECT=16, CAST=17
- `mesh_ir.ir.common.Access`: READ_ONLY=1, READ_WRITE=2
- `mesh_ir.ir.common.DType`: FP32=1, FP16=2, BF16=3, INT8=4, INT32=5
- `mesh_ir.ir.common.DmaKind`: LOAD=1, STORE=2, P2P_PUSH=3, PREFETCH=4, LOCAL_FILL=5
- `mesh_ir.ir.common.Engine`: CONTROL=1, DMA_READ=2, DMA_WRITE=3, TENSOR=4, VECTOR=5, REDUCE=6
- `mesh_ir.ir.common.Layout`: CONTIGUOUS_ROW_MAJOR=1, TRANSPOSED_2D_VIEW=2, BLOCKED_MNK=3
- `mesh_ir.ir.common.MemorySpace`: HBM=1, HOST_SHARED=2, CORE_SRAM=3, PEER_SRAM=4
- `mesh_ir.ir.common.StorageClass`: EXTERNAL=1, HBM=2, HOST_SHARED=3, CORE_SRAM=4, PRE_RESIDENT=5
- `mesh_ir.ir.common.TensorRole`: INPUT=1, OUTPUT=2, WEIGHT=3, CONSTANT=4, ACTIVATION=5, KV_CACHE=6, STATE=7
- `mesh_ir.ir.graph_ir.OpCode`: MATMUL=1, BMM=2, LINEAR_BIAS=3, RESHAPE_VIEW=4, TRANSPOSE_VIEW=5, PERMUTE_VIEW=6, SLICE_VIEW=7, EXPAND_VIEW=8, CONTIGUOUS_COPY=9, CONCAT=10, GATHER_ROWS=11, ADD=12, SUB=13, MUL=14, DIV=15, RELU=16, GELU=17, SILU=18, EXP=19, RSQRT=20, REDUCE_SUM=21, REDUCE_MAX=22, REDUCE_MEAN=23, LAYERNORM=24, RMSNORM=25, SOFTMAX=26, EMBEDDING_LOOKUP=27
- `mesh_ir.ir.kernel_ir.CollectiveAlgorithm`: AUTO=1, RING=2, TREE=3
- `mesh_ir.ir.kernel_ir.CollectiveKind`: ALL_REDUCE=1
- `mesh_ir.ir.kernel_ir.DistributionKind`: PARTITIONED=1, REPLICATED=2, PARTIAL_SUM=3
- `mesh_ir.ir.kernel_ir.KernelOpcode`: ALLOC=1, VIEW=2, DMA=3, GEMM=4, BMM=5, MATRIX_EPILOGUE=6, VECTOR=7, DATA_MOVEMENT=8, REDUCE=9, SOFTMAX=10, NORM=11, COLLECTIVE=12, LOCAL_REDUCE=13, LOCAL_COPY=14, RECV_WAIT=15, BARRIER=16
- `mesh_ir.ir.kernel_ir.MatrixEpilogueAlgorithm`: VECTOR_ACCUMULATION=1
- `mesh_ir.ir.kernel_ir.MatrixPhase`: DIRECT=1, ACCUMULATE_ONLY=2, ACCUMULATE_FIRST=3, ACCUMULATE_CONTINUE=4, ACCUMULATE_FINAL=5
- `mesh_ir.ir.kernel_ir.MovementAlgorithm`: STRIDED_COPY=1, CONCAT=2, GATHER_ROWS=3
- `mesh_ir.ir.kernel_ir.NormAlgorithm`: LAYER_NORM=1, RMS_NORM=2
- `mesh_ir.ir.kernel_ir.OperandAccessMode`: READ=1, WRITE=2
- `mesh_ir.ir.kernel_ir.ReduceKind`: SUM=1
- `mesh_ir.ir.kernel_ir.ReductionAlgorithm`: LEFT_TO_RIGHT=1
- `mesh_ir.ir.kernel_ir.SoftmaxAlgorithm`: STABLE_MAX_SUM=1
- `mesh_ir.ir.kernel_ir.StateOrigin`: EMPTY=1, EXTERNAL=2, PRE_RESIDENT=3, PRODUCED=4
- `mesh_ir.ir.kernel_ir.SynthesizedTensorPurpose`: PADDING=1, COPY=2, ACCUMULATION=3, PARTIAL_SUM=4, REDUCTION=5, DATA_MOVEMENT=6
- `mesh_ir.ir.kernel_ir.VectorAlgorithm`: ELEMENTWISE=1, EMBEDDING_GATHER=2
- `mesh_ir.scheduled.model.EndpointSide`: SRC=1, DST=2
- `mesh_ir.scheduled.model.FenceScope`: DMA_READ=1, DMA_WRITE=2, P2P=3, HOST_SHARED_WRITE=4, ALL_INSTANCE=5
- `mesh_ir.scheduled.model.ScheduledDependencyKind`: KERNEL_CONTROL=1, KERNEL_STATE=2, RAW=3, WAR=4, WAW=5, DMA_PIN=6, DMA_COMPLETION=7, SRAM_REUSE=8, STREAM_ORDER=9, LIFECYCLE=10
- `mesh_ir.traffic.AxiChannel`: AW=0, W=1, B=2, AR=3, R=4
- `mesh_ir.traffic.TrafficAggregateLevel`: PROGRAM=1, ENTRYPOINT=2, PROFILE=3, DETAIL=4
- `mesh_ir.traffic.TrafficDirection`: READ=1, WRITE=2, P2P=3, LOCAL=4

## Semantic records

- `256` `mesh_ir.analysis.cost.ExecutionWorkPhase`: 16 bytes, `ExecutionWorkPhase`
- `257` `mesh_ir.analysis.cost.WorkEstimate`: 40 bytes, `WorkEstimate`
- `258` `mesh_ir.ir.common.Add`: 24 bytes, `Add`
- `259` `mesh_ir.ir.common.CeilDivByConst`: 24 bytes, `CeilDivByConst`
- `260` `mesh_ir.ir.common.Const`: 16 bytes, `Const`
- `261` `mesh_ir.ir.common.FloorDivByConst`: 24 bytes, `FloorDivByConst`
- `262` `mesh_ir.ir.common.MulByConst`: 24 bytes, `MulByConst`
- `263` `mesh_ir.ir.common.Symbol`: 48 bytes, `Symbol`
- `264` `mesh_ir.ir.graph_ir.ElementwiseAttrs`: 48 bytes, `ElementwiseAttrs`
- `265` `mesh_ir.ir.graph_ir.EmbeddingAttrs`: 48 bytes, `EmbeddingAttrs`
- `266` `mesh_ir.ir.graph_ir.MatmulAttrs`: 72 bytes, `MatmulAttrs`
- `267` `mesh_ir.ir.graph_ir.MovementAttrs`: 24 bytes, `MovementAttrs`
- `268` `mesh_ir.ir.graph_ir.NormAttrs`: 40 bytes, `NormAttrs`
- `269` `mesh_ir.ir.graph_ir.ReduceAttrs`: 48 bytes, `ReduceAttrs`
- `270` `mesh_ir.ir.graph_ir.SoftmaxAttrs`: 32 bytes, `SoftmaxAttrs`
- `271` `mesh_ir.ir.graph_ir.ViewAttrs`: 64 bytes, `ViewAttrs`
- `272` `mesh_ir.ir.kernel_ir.AllocAttrs`: 16 bytes, `AllocAttrs`
- `273` `mesh_ir.ir.kernel_ir.BarrierAttrs`: 16 bytes, `BarrierAttrs`
- `274` `mesh_ir.ir.kernel_ir.BlockedMnkLayout`: 40 bytes, `BlockedMnkLayout`
- `275` `mesh_ir.ir.kernel_ir.BufferObject`: 88 bytes, `BufferObject`
- `276` `mesh_ir.ir.kernel_ir.BufferView`: 96 bytes, `BufferView`
- `277` `mesh_ir.ir.kernel_ir.CollectiveAttrs`: 56 bytes, `CollectiveAttrs`
- `278` `mesh_ir.ir.kernel_ir.ControlToken`: 24 bytes, `ControlToken`
- `279` `mesh_ir.ir.kernel_ir.DmaAttrs`: 64 bytes, `DmaAttrs`
- `280` `mesh_ir.ir.kernel_ir.ElementRegion`: 32 bytes, `ElementRegion`
- `281` `mesh_ir.ir.kernel_ir.GemmKernelAttrs`: 56 bytes, `GemmKernelAttrs`
- `282` `mesh_ir.ir.kernel_ir.KernelComputation`: 48 bytes, `KernelComputation`
- `283` `mesh_ir.ir.kernel_ir.KernelCost`: 56 bytes, `KernelCost`
- `284` `mesh_ir.ir.kernel_ir.KernelOp`: 96 bytes, `KernelOp`
- `285` `mesh_ir.ir.kernel_ir.KernelTensor`: 128 bytes, `KernelTensor`
- `286` `mesh_ir.ir.kernel_ir.KernelTile`: 104 bytes, `KernelTile`
- `287` `mesh_ir.ir.kernel_ir.LocalCopyAttrs`: 8 bytes, `LocalCopyAttrs`
- `288` `mesh_ir.ir.kernel_ir.LocalReduceAttrs`: 40 bytes, `LocalReduceAttrs`
- `289` `mesh_ir.ir.kernel_ir.MatrixEpilogueKernelAttrs`: 48 bytes, `MatrixEpilogueKernelAttrs`
- `290` `mesh_ir.ir.kernel_ir.MovementKernelAttrs`: 48 bytes, `MovementKernelAttrs`
- `291` `mesh_ir.ir.kernel_ir.NormKernelAttrs`: 48 bytes, `NormKernelAttrs`
- `292` `mesh_ir.ir.kernel_ir.OperandAccess`: 40 bytes, `OperandAccess`
- `293` `mesh_ir.ir.kernel_ir.PartialSumDefinition`: 48 bytes, `PartialSumDefinition`
- `294` `mesh_ir.ir.kernel_ir.Placement`: 24 bytes, `Placement`
- `295` `mesh_ir.ir.kernel_ir.RecvWaitAttrs`: 40 bytes, `RecvWaitAttrs`
- `296` `mesh_ir.ir.kernel_ir.ReductionKernelAttrs`: 48 bytes, `ReductionKernelAttrs`
- `297` `mesh_ir.ir.kernel_ir.SoftmaxKernelAttrs`: 40 bytes, `SoftmaxKernelAttrs`
- `298` `mesh_ir.ir.kernel_ir.StateTransition`: 48 bytes, `StateTransition`
- `299` `mesh_ir.ir.kernel_ir.TensorShard`: 80 bytes, `TensorShard`
- `300` `mesh_ir.ir.kernel_ir.TensorState`: 48 bytes, `TensorState`
- `301` `mesh_ir.ir.kernel_ir.VectorKernelAttrs`: 48 bytes, `VectorKernelAttrs`
- `302` `mesh_ir.ir.kernel_ir.ViewDeclarationAttrs`: 16 bytes, `ViewDeclarationAttrs`
- `303` `mesh_ir.scheduled.model.AuthoredProgramOrigin`: 32 bytes, `AuthoredProgramOrigin`
- `304` `mesh_ir.scheduled.model.AuthoredVariantLineage`: 16 bytes, `AuthoredVariantLineage`
- `305` `mesh_ir.scheduled.model.AxiFenceAttrs`: 16 bytes, `AxiFenceAttrs`
- `306` `mesh_ir.scheduled.model.BarrierArrival`: 40 bytes, `BarrierArrival`
- `307` `mesh_ir.scheduled.model.BarrierExecution`: 16 bytes, `BarrierExecution`
- `308` `mesh_ir.scheduled.model.BarrierGroup`: 40 bytes, `BarrierGroup`
- `309` `mesh_ir.scheduled.model.CommandSemantics`: 32 bytes, `CommandSemantics`
- `310` `mesh_ir.scheduled.model.CompiledProgramOrigin`: 16 bytes, `CompiledProgramOrigin`
- `311` `mesh_ir.scheduled.model.CompiledVariantLineage`: 24 bytes, `CompiledVariantLineage`
- `312` `mesh_ir.scheduled.model.ComputeExecution`: 16 bytes, `ComputeExecution`
- `313` `mesh_ir.scheduled.model.ControlCommandSource`: 16 bytes, `ControlCommandSource`
- `314` `mesh_ir.scheduled.model.ControlExecution`: 8 bytes, `ControlExecution`
- `315` `mesh_ir.scheduled.model.DescriptorEndpointUse`: 40 bytes, `DescriptorEndpointUse`
- `316` `mesh_ir.scheduled.model.DescriptorGroup`: 48 bytes, `DescriptorGroup`
- `317` `mesh_ir.scheduled.model.DescriptorSource`: 16 bytes, `DescriptorSource`
- `318` `mesh_ir.scheduled.model.DmaExecution`: 16 bytes, `DmaExecution`
- `319` `mesh_ir.scheduled.model.EventSignalAttrs`: 16 bytes, `EventSignalAttrs`
- `320` `mesh_ir.scheduled.model.EventWaitAttrs`: 16 bytes, `EventWaitAttrs`
- `321` `mesh_ir.scheduled.model.ExternalSlotBacking`: 16 bytes, `ExternalSlotBacking`
- `322` `mesh_ir.scheduled.model.HaltAttrs`: 8 bytes, `HaltAttrs`
- `323` `mesh_ir.scheduled.model.IdSpan`: 24 bytes, `IdSpan`
- `324` `mesh_ir.scheduled.model.KernelCommandSource`: 16 bytes, `KernelCommandSource`
- `325` `mesh_ir.scheduled.model.KernelTokenSource`: 16 bytes, `KernelTokenSource`
- `326` `mesh_ir.scheduled.model.LifecycleSource`: 16 bytes, `LifecycleSource`
- `327` `mesh_ir.scheduled.model.LocalAllocationBacking`: 16 bytes, `LocalAllocationBacking`
- `328` `mesh_ir.scheduled.model.ObjectBacking`: 24 bytes, `ObjectBacking`
- `329` `mesh_ir.scheduled.model.ObjectSource`: 16 bytes, `ObjectSource`
- `330` `mesh_ir.scheduled.model.ProgramSemantics`: 200 bytes, `ProgramSemantics`
- `331` `mesh_ir.scheduled.model.ProgramVariant`: 56 bytes, `ProgramVariant`
- `332` `mesh_ir.scheduled.model.ReadAccessUse`: 32 bytes, `ReadAccessUse`
- `333` `mesh_ir.scheduled.model.RecvWaitExecution`: 16 bytes, `RecvWaitExecution`
- `334` `mesh_ir.scheduled.model.RepeatCommandAttrs`: 32 bytes, `RepeatCommandAttrs`
- `335` `mesh_ir.scheduled.model.RequestBeginAttrs`: 8 bytes, `RequestBeginAttrs`
- `336` `mesh_ir.scheduled.model.RequestEndAttrs`: 8 bytes, `RequestEndAttrs`
- `337` `mesh_ir.scheduled.model.ResidentView`: 24 bytes, `ResidentView`
- `338` `mesh_ir.scheduled.model.ScheduledDependency`: 48 bytes, `ScheduledDependency`
- `339` `mesh_ir.scheduled.model.ScheduledStream`: 56 bytes, `ScheduledStream`
- `340` `mesh_ir.scheduled.model.StateSource`: 16 bytes, `StateSource`
- `341` `mesh_ir.scheduled.model.StreamOrderSource`: 16 bytes, `StreamOrderSource`
- `342` `mesh_ir.scheduled.model.VariantMembership`: 200 bytes, `VariantMembership`
- `343` `mesh_ir.scheduled.model.WriteAccessUse`: 32 bytes, `WriteAccessUse`
- `344` `mesh_ir.traffic.Binding`: 64 bytes, `Binding`
- `345` `mesh_ir.traffic.BindingSlot`: 80 bytes, `BindingSlot`
- `346` `mesh_ir.traffic.ChannelTraffic`: 120 bytes, `ChannelTraffic`
- `347` `mesh_ir.traffic.DescriptorIdentity`: 80 bytes, `DescriptorIdentity`
- `348` `mesh_ir.traffic.DescriptorTraffic`: 208 bytes, `DescriptorTraffic`
- `349` `mesh_ir.traffic.TrafficAggregate`: 104 bytes, `TrafficAggregate`
- `350` `mesh_ir.traffic.TrafficAggregateKey`: 72 bytes, `TrafficAggregateKey`
- `351` `mesh_ir.traffic.TrafficChannelTotal`: 56 bytes, `TrafficChannelTotal`
- `352` `mesh_ir.traffic.TrafficReport`: 40 bytes, `TrafficReport`
