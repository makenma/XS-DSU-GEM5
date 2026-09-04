# Agent Protocol ABI Reference

Generated from `util/mesh_ir/mesh_ir/abi/agent_protocol_abi.yaml` -- do not edit.

- schema_sha256: `d8233715d66e9946e642da667c9f3f5d6a4c109cd2b63f6e9e6324e7c83259c7`
- ABI: 1.0

## Enums

- `sq_opcode`: GENERATE=0x1, RELEASE_SESSION=0x2, CANCEL=0x3
- `sq_flags`: REQUIRE_KV_REUSE=0x1, ALLOW_REPREFILL=0x2, BATCH_REPLAY=0x4
- `cq_flags`: METADATA_VALID=0x1, OUTPUT_BYTES_EXTENDED=0x2, PARTIAL_OUTPUT=0x4, SQ_SEQ_ONLY_ERROR=0x8, CONTROL_COMMAND=0x10, SQ_IDENTITY_ERROR=0x20, DETAIL_IN_CQ=0x40, SQ_ABI_ERROR=0x80
- `cq_status`: SUCCESS=0x0, CANCELLED=0x1, NOT_FOUND=0x2, ALREADY_TERMINAL=0x3, BUSY=0x4, STALE_GENERATION=0x5, SQ_CRC=0x101, PARAM_ERROR=0x102, PROFILE_ERROR=0x103, PROGRAM_ERROR=0x201, AXI_ERROR=0x202, INTERNAL_ERROR=0x2ff
- `semantic_phase`: PREFILL=0x1, DECODE=0x2, PUBLISH=0x3
- `tlv_type`: INPUT_DIGEST=0x1, DEADLINE=0x2, OUTPUT_CHUNK_BYTES=0x3, WORKLOAD_ID_DIGEST=0x4
- `output_tlv_type`: DIAGNOSTIC=0x1001, TIMING_BREAKDOWN=0x1002, ROUTE_DIGESTS=0x1003
- `tlv_flags`: REQUIRED=0x1
- `binding_kind`: HOST_INPUT=0x1, HOST_OUTPUT=0x2, KV_EXTERNAL=0x3, WEIGHT_EXTERNAL=0x4
- `binding_flags`: READ=0x1, WRITE=0x2, PERSISTENT=0x4, RESOLVE_BY_HANDLE=0x8
- `diagnostic_component`: FRONTEND=0x1, MESH_CORE=0x2, DMA_AXI=0x3, MOE_CACHE=0x4, SERVING_KV=0x5
- `run_exit_reason`: QUIESCENT_SUCCESS=0x0, INFRA_FATAL=0x14, WATCHDOG=0x15, TEST_ASSERTION_FAILED=0x16, CONFIG_ERROR=0x17, INTERNAL_ABORT=0x18

## Detail codes

- `E_OK` = 0x00000000
- `E_DCORE_COMMAND_STATE` = 0x00010001
- `E_DCORE_DEADLOCK` = 0x00010002
- `E_DCORE_ENGINE` = 0x00010003
- `E_DCORE_EVENT` = 0x00010004
- `E_DCORE_EVENT_NAMESPACE` = 0x00010005
- `E_DCORE_OPCODE` = 0x00010006
- `E_DCORE_POISON_READ` = 0x00010007
- `E_DCORE_QUEUE_CAPACITY` = 0x00010008
- `E_DCORE_REFERENCE_COMPUTE` = 0x00010009
- `E_DCORE_SRAM_BOUNDS` = 0x0001000a
- `E_DCORE_SRAM_LIFETIME` = 0x0001000b
- `E_DCORE_TIMING_OVERFLOW` = 0x0001000c
- `E_AXI_RESPONSE` = 0x00020001
- `E_AGENT_PROTOCOL_FATAL` = 0x00020002
- `E_COMPLETION_PATH_AXI` = 0x00020003
- `E_CQ_FULL` = 0x00020004
- `E_CQ_ORDER` = 0x00020005
- `E_INTERRUPT` = 0x00020006
- `E_SQ_CRC` = 0x00020007
- `E_SQ_FULL` = 0x00020008
- `E_SQ_MALFORMED` = 0x00020009
- `E_AGENT_AXI_PROXY_DRAIN` = 0x0002000a
- `E_REQUEST_CONTEXT_FULL` = 0x0002000b
- `E_REQUEST_BINDING` = 0x0002000c
- `E_BINDING_ALIAS_MISMATCH` = 0x0002000d
- `E_BINDING_ROLE` = 0x0002000e
- `E_RESERVED_FIELD` = 0x0002000f
- `E_PARAMETER_LENGTH_MISMATCH` = 0x00020010
- `E_REQUEST_IDENTITY` = 0x00020011
- `E_REQUEST_CANCELLED` = 0x00020012
- `E_AGENT_PLAN` = 0x00030001
- `E_AGENT_ROUND_LIMIT` = 0x00030002
- `E_BATCH_REPLAY_DIVERGENCE` = 0x00030003
- `E_WORKLOAD_CHANGED_BY_TIMING` = 0x00030004
- `E_WORKLOAD_PLAN_MISMATCH` = 0x00030005
- `E_MOE_CAPACITY` = 0x00040001
- `E_MOE_COMBINE_BYTES` = 0x00040002

## Records

### SQ_DESCRIPTOR (64 B)

| field | type | offset |
|---|---|---:|
| `abi_major` | u16 | 0 |
| `abi_minor` | u16 | 2 |
| `opcode` | u16 | 4 |
| `flags` | u16 | 6 |
| `sq_seq` | u64 | 8 |
| `request_id` | u64 | 16 |
| `session_id` | u64 | 24 |
| `parameter_block_addr` | u64 | 32 |
| `parameter_block_bytes` | u32 | 40 |
| `program_id` | u16 | 44 |
| `profile_id` | u16 | 46 |
| `completion_cookie` | u64 | 48 |
| `qos` | u8 | 56 |
| `flags2` | u8 | 57 |
| `reserved` | u16 | 58 |
| `crc32` | u32 | 60 |

### CQ_DESCRIPTOR (32 B)

| field | type | offset |
|---|---|---:|
| `cq_seq` | u64 | 0 |
| `request_id` | u64 | 8 |
| `completion_cookie` | u64 | 16 |
| `status` | u16 | 24 |
| `flags` | u16 | 26 |
| `output_bytes_or_detail_code` | u32 | 28 |

### PARAMETER_HEADER (160 B)

| field | type | offset |
|---|---|---:|
| `magic` | u32 | 0 |
| `abi_major` | u16 | 4 |
| `abi_minor` | u16 | 6 |
| `header_bytes` | u32 | 8 |
| `total_bytes` | u32 | 12 |
| `flags` | u32 | 16 |
| `reserved` | u32 | 20 |
| `input_addr` | u64 | 24 |
| `input_bytes` | u64 | 32 |
| `input_tokens` | u32 | 40 |
| `cached_tokens` | u32 | 44 |
| `output_addr` | u64 | 48 |
| `output_capacity_bytes` | u64 | 56 |
| `output_metadata_addr` | u64 | 64 |
| `output_metadata_capacity_bytes` | u32 | 72 |
| `max_output_tokens` | u32 | 76 |
| `kv_handle` | u64 | 80 |
| `kv_generation` | u32 | 88 |
| `moe_route_profile_id` | u32 | 92 |
| `user_id` | u32 | 96 |
| `task_seq` | u32 | 100 |
| `repair_round` | u16 | 104 |
| `qos` | u8 | 106 |
| `request_kind` | u8 | 107 |
| `workload_plan_item_id` | u32 | 108 |
| `target_request_id` | u64 | 112 |
| `binding_table_offset` | u32 | 120 |
| `binding_count` | u16 | 124 |
| `binding_record_bytes` | u16 | 126 |
| `extension_offset` | u32 | 128 |
| `extension_bytes` | u32 | 132 |
| `requested_profile_key` | u64 | 136 |
| `reserved2` | u64 | 144 |
| `crc32` | u32 | 152 |
| `reserved3` | u32 | 156 |

### BINDING_RECORD (24 B)

| field | type | offset |
|---|---|---:|
| `symbol_id` | u32 | 0 |
| `kind` | u16 | 4 |
| `flags` | u16 | 6 |
| `address` | u64 | 8 |
| `bytes` | u64 | 16 |

### OUTPUT_METADATA (128 B)

| field | type | offset |
|---|---|---:|
| `magic` | u32 | 0 |
| `abi_major` | u16 | 4 |
| `abi_minor` | u16 | 6 |
| `header_bytes` | u32 | 8 |
| `total_bytes` | u32 | 12 |
| `flags` | u32 | 16 |
| `terminal_status` | u32 | 20 |
| `request_id` | u64 | 24 |
| `session_id` | u64 | 32 |
| `user_id` | u32 | 40 |
| `task_seq` | u32 | 44 |
| `repair_round` | u16 | 48 |
| `reserved` | u16 | 50 |
| `output_tokens` | u32 | 52 |
| `output_bytes` | u64 | 56 |
| `semantic_content_digest` | bytes32 | 64 |
| `completed_instance_count` | u32 | 96 |
| `moe_invocation_count` | u32 | 100 |
| `request_start_tick` | u64 | 104 |
| `terminal_ready_tick` | u64 | 112 |
| `crc32` | u32 | 120 |
| `reserved2` | u32 | 124 |

### TLV_HEADER (8 B)

| field | type | offset |
|---|---|---:|
| `type` | u16 | 0 |
| `flags` | u16 | 2 |
| `payload_bytes` | u32 | 4 |

