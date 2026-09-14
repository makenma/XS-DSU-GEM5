"""Dynamic MoE V1 semantic verification (main contract 7.2).

The base verifier owns the static program; this module owns every
cross-reference that only exists when MESH_FEATURE_DYNAMIC_MOE_V1 is
declared: layer/expert/region/kernel tables, the weight binding into the
external symbol, the insertion gate inside the static lifecycle and the
aggregate/per-region bound relation.
"""

from __future__ import annotations

from mesh_ir.abi.dependency import (
    command_prerequisites,
    prerequisite_of,
    stream_commands,
)
from mesh_ir.abi.spans import checked_span
from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError

DTYPE_BYTES = {
    A.DTYPE.FP32: 4,
    A.DTYPE.FP16: 2,
    A.DTYPE.BF16: 2,
    A.DTYPE.INT8: 1,
    A.DTYPE.INT32: 4,
}

INVALID_U32 = 0xFFFFFFFF
GEMM_OPCODES = (A.OPCODE.GEMM, A.OPCODE.BMM)
LAYER_BOUND_FIELDS = (
    "max_tokens_per_frozen_batch",
    "max_requests_per_batch",
    "max_routes",
    "max_materialized_commands",
    "max_materialized_events",
    "max_materialized_descriptors",
    "max_materialized_transfers",
    "max_dynamic_allocations",
)


def verify_moe_v1(program, arch) -> None:
    enabled = bool(program.required_features & A.DYNAMIC_MOE_V1)
    tables = (
        program.moe_layer_specs,
        program.moe_expert_specs,
        program.moe_dynamic_regions,
        program.moe_kernel_specs,
        program.content_digests,
    )
    unknown = program.required_features & ~A.KNOWN_FEATURE_MASK
    if unknown:
        raise MeshIrError("E_ABI_FEATURE", "unknown required feature bits",
                          features=hex(unknown))
    if not enabled:
        if any(tables):
            raise MeshIrError("E_ABI_FEATURE",
                              "MoE sections without the feature bit")
        return
    if not all(tables):
        raise MeshIrError("E_ABI_BOUNDS",
                          "MoE feature requires every conditional section")
    _verify_layers(program)
    _verify_experts(program, arch)
    _verify_regions(program, arch)
    _verify_kernels(program)


def _layer_span(program, layer) -> slice:
    return checked_span(layer.expert_first, layer.expert_count,
                        len(program.moe_expert_specs),
                        "expert slice out of table")


def _region_span(program, layer) -> slice:
    return checked_span(layer.dynamic_region_first, layer.dynamic_region_count,
                        len(program.moe_dynamic_regions),
                        "region slice out of table")


def _verify_layers(program) -> None:
    layers = program.moe_layer_specs
    experts_offset = 0
    regions_offset = 0
    kernel_indices = []
    for index, layer in enumerate(layers):
        if layer.layer_id != index + 1:
            raise MeshIrError("E_ABI_ORDER",
                              "layer ids must be dense from 1",
                              layer_id=layer.layer_id)
        if layer.flags != 0:
            raise MeshIrError("E_ABI_RESERVED", "layer flags must be zero",
                              layer_id=layer.layer_id)
        if layer.expert_count < 1 or not 1 <= layer.top_k <= \
                layer.expert_count:
            raise MeshIrError("E_ABI_BOUNDS", "layer top-k out of range",
                              layer_id=layer.layer_id)
        if layer.dynamic_region_count < 1:
            raise MeshIrError("E_ABI_BOUNDS",
                              "layer needs at least one region",
                              layer_id=layer.layer_id)
        if (layer.token_bytes == 0 or layer.output_token_bytes == 0 or
                layer.capacity_factor_q16 == 0):
            raise MeshIrError("E_ABI_BOUNDS",
                              "layer byte/capacity size is zero",
                              layer_id=layer.layer_id)
        for name in LAYER_BOUND_FIELDS:
            if getattr(layer, name) == 0:
                raise MeshIrError("E_ABI_BOUNDS",
                                  f"layer bound {name} must be positive",
                                  layer_id=layer.layer_id)
        if layer.max_routes < layer.top_k * layer.max_tokens_per_frozen_batch:
            raise MeshIrError("E_ABI_BOUNDS", "max_routes below top-k demand",
                              layer_id=layer.layer_id)
        if layer.expert_first != experts_offset:
            raise MeshIrError("E_ABI_ORDER",
                              "expert slices must tile the expert table",
                              layer_id=layer.layer_id)
        if layer.dynamic_region_first != regions_offset:
            raise MeshIrError("E_ABI_ORDER",
                              "region slices must tile the region table",
                              layer_id=layer.layer_id)
        expert_span = _layer_span(program, layer)
        region_span = _region_span(program, layer)
        experts_offset = expert_span.stop
        regions_offset = region_span.stop
        if not 0 <= layer.kernel_spec_index < len(program.moe_kernel_specs):
            raise MeshIrError("E_ABI_BOUNDS", "kernel index out of table",
                              layer_id=layer.layer_id)
        kernel_indices.append(layer.kernel_spec_index)
        _verify_group_bounds(layer, program.moe_dynamic_regions[region_span])
    if experts_offset != len(program.moe_expert_specs):
        raise MeshIrError("E_ABI_ORDER", "expert table has unowned records")
    if regions_offset != len(program.moe_dynamic_regions):
        raise MeshIrError("E_ABI_ORDER", "region table has unowned records")
    expected = list(range(len(layers)))
    if len(program.moe_kernel_specs) != len(layers) or sorted(
            kernel_indices) != expected:
        raise MeshIrError("E_ABI_DUPLICATE",
                          "each layer must own exactly one kernel record")


def _verify_group_bounds(layer, regions) -> None:
    totals = {
        "max_materialized_commands":
            sum(r.max_overlay_commands for r in regions),
        "max_materialized_descriptors":
            sum(r.max_overlay_descriptors for r in regions),
        "max_materialized_events":
            sum(r.max_overlay_events for r in regions) + 1,
        "max_materialized_transfers":
            max(r.max_overlay_transfers for r in regions),
        "max_dynamic_allocations":
            sum(r.max_overlay_allocations for r in regions),
    }
    for name, required in totals.items():
        if getattr(layer, name) < required:
            raise MeshIrError("E_ABI_BOUNDS",
                              f"group bound {name} below per-region demand",
                              layer_id=layer.layer_id)


def _weight_tensor(program, symbol_id):
    relocation = next((r for r in program.relocations
                       if r.symbol_sid == symbol_id), None)
    if relocation is None:
        raise MeshIrError("E_RELOCATION", "weight symbol is not relocated",
                          symbol_id=symbol_id)
    tensor = next((t for t in program.tensors
                   if t.tensor_id == relocation.tensor_id), None)
    if tensor is None:
        raise MeshIrError("E_RELOCATION", "weight symbol tensor is missing",
                          symbol_id=symbol_id)
    if (tensor.role != A.TENSOR_ROLE.WEIGHT or
            tensor.storage_class != A.STORAGE_CLASS.EXTERNAL or
            tensor.access != A.ACCESS_KIND.READ_ONLY or
            not tensor.flags & A.TENSOR_FLAGS.HAS_CONTENT_SHA256):
        raise MeshIrError(
            "E_RELOCATION",
            "MoE weight symbol must be a read-only persistent external tensor",
            symbol_id=symbol_id)
    return tensor


def _tensor_bytes(tensor) -> int:
    if tensor.rank < 1 or tensor.dtype not in DTYPE_BYTES:
        raise MeshIrError("E_ABI_BOUNDS", "weight tensor shape is unusable",
                          tensor_id=tensor.tensor_id)
    total = DTYPE_BYTES[tensor.dtype]
    for extent in tensor.dims[:tensor.rank]:
        if extent == 0:
            raise MeshIrError("E_ABI_BOUNDS", "weight tensor extent is zero",
                              tensor_id=tensor.tensor_id)
        total *= extent
    return total


def _verify_experts(program, arch) -> None:
    for layer in program.moe_layer_specs:
        span = _layer_span(program, layer)
        for ordinal, expert in enumerate(program.moe_expert_specs[span]):
            if expert.layer_id != layer.layer_id:
                raise MeshIrError("E_ABI_BOUNDS", "expert layer mismatch",
                                  expert_id=expert.expert_id)
            if expert.expert_id != ordinal:
                raise MeshIrError("E_ABI_ORDER",
                                  "expert ids must be dense inside the layer",
                                  layer_id=layer.layer_id)
            if expert.flags != 0:
                raise MeshIrError("E_ABI_RESERVED",
                                  "expert flags must be zero",
                                  expert_id=expert.expert_id)
            if expert.core_id not in arch.core_ids:
                raise MeshIrError("E_ABI_BOUNDS", "expert core not in arch",
                                  core=expert.core_id)
            if expert.weight_bytes == 0:
                raise MeshIrError("E_ABI_BOUNDS", "expert weight is empty",
                                  expert_id=expert.expert_id)
            if expert.weight_region_offset > (
                    (1 << 64) - 1) - expert.weight_bytes:
                raise MeshIrError("E_ABI_OVERFLOW",
                                  "expert weight range overflows",
                                  expert_id=expert.expert_id)
            tensor = _weight_tensor(program, expert.weight_symbol_id)
            if expert.weight_region_offset + expert.weight_bytes > \
                    _tensor_bytes(tensor):
                raise MeshIrError("E_ABI_BOUNDS",
                                  "expert weight range escapes its backing",
                                  expert_id=expert.expert_id)
            if expert.weight_digest_index == INVALID_U32 or \
                    expert.weight_digest_index >= len(program.content_digests):
                raise MeshIrError("E_ABI_BOUNDS",
                                  "weight digest index out of table",
                                  expert_id=expert.expert_id)
            digest = program.content_digests[expert.weight_digest_index]
            if (digest.object_kind != A.TENSOR_ROLE.WEIGHT or
                    digest.object_id != tensor.tensor_id or
                    digest.digest != tensor.content_sha256 or
                    digest.digest == bytes(32)):
                raise MeshIrError("E_ABI_BOUNDS",
                                  "weight digest does not match its tensor",
                                  expert_id=expert.expert_id)


def _verify_regions(program, arch) -> None:
    scratch_by_core = {}
    core_sets = []
    for layer in program.moe_layer_specs:
        span = _region_span(program, layer)
        regions = program.moe_dynamic_regions[span]
        cores = []
        for ordinal, region in enumerate(regions):
            if region.region_id != ordinal + 1:
                raise MeshIrError("E_ABI_ORDER",
                                  "region ids must be core-ascending ordinals",
                                  layer_id=layer.layer_id)
            if region.layer_id != layer.layer_id:
                raise MeshIrError("E_ABI_BOUNDS", "region layer mismatch",
                                  region_id=region.region_id)
            if region.flags != 0:
                raise MeshIrError("E_ABI_RESERVED",
                                  "region flags must be zero",
                                  region_id=region.region_id)
            if region.core_id not in arch.core_ids:
                raise MeshIrError("E_ABI_BOUNDS", "region core not in arch",
                                  core=region.core_id)
            if cores and region.core_id <= cores[-1]:
                raise MeshIrError(
                    "E_ABI_ORDER",
                    "region cores must ascend without duplicates",
                    layer_id=layer.layer_id)
            cores.append(region.core_id)
            if region.scratch_bytes == 0 or region.scratch_alignment == 0 or (
                    region.scratch_alignment & (region.scratch_alignment - 1)):
                raise MeshIrError("E_ABI_BOUNDS",
                                  "region scratch geometry is invalid",
                                  region_id=region.region_id)
            _verify_scratch_partition(arch, region)
            _verify_region_scratch(scratch_by_core, region)
            _verify_region_gate(program, region)
        for expert in program.moe_expert_specs[_layer_span(program, layer)]:
            if expert.core_id not in cores:
                raise MeshIrError("E_ABI_BOUNDS",
                                  "expert core has no participating region",
                                  expert_id=expert.expert_id)
        core_sets.append(tuple(cores))
    if any(cores != core_sets[0] for cores in core_sets):
        raise MeshIrError(
            "E_ABI_BOUNDS",
            "every layer must share the same participating cores")


def _verify_scratch_partition(arch, region) -> None:
    if region.scratch_offset + region.scratch_bytes > arch.sram_bytes:
        raise MeshIrError("E_ABI_BOUNDS", "region scratch escapes SRAM",
                          region_id=region.region_id)
    if not arch.sram_partitions:
        raise MeshIrError("E_ARCH_PARTITION",
                          "MoE feature needs a partitioned architecture",
                          region_id=region.region_id)
    scratch = arch.partition(A.SRAM_PARTITION_KIND.RUNTIME_SCRATCH)
    if scratch is None:
        raise MeshIrError("E_ARCH_PARTITION",
                          "MoE feature needs a RUNTIME_SCRATCH partition")
    if region.scratch_offset < scratch.base or \
            region.scratch_offset + region.scratch_bytes > \
            scratch.base + scratch.bytes:
        raise MeshIrError("E_ABI_BOUNDS",
                          "region scratch escapes RUNTIME_SCRATCH",
                          region_id=region.region_id)


def _verify_region_scratch(scratch_by_core, region) -> None:
    start = region.scratch_offset
    end = start + region.scratch_bytes
    for other_start, other_end, other in scratch_by_core.setdefault(
            region.core_id, []):
        if start < other_end and other_start < end:
            raise MeshIrError("E_ABI_BOUNDS",
                              "same-core scratch intervals overlap",
                              region_id=region.region_id, other=other)
    scratch_by_core[region.core_id].append((start, end, region.region_id))


def _region_lifecycle(program, region) -> tuple:
    begins = [c for c in program.commands
              if c.opcode == A.OPCODE.REQUEST_BEGIN]
    ends = [c for c in program.commands if c.opcode == A.OPCODE.REQUEST_END]
    halts = [c for c in program.commands
             if c.opcode == A.OPCODE.HALT and c.core_id == region.core_id]
    if len(begins) != 1 or len(ends) != 1:
        raise MeshIrError("E_LIFECYCLE",
                          "instance lifecycle must be unique",
                          region_id=region.region_id)
    if len(halts) != 1:
        raise MeshIrError("E_LIFECYCLE",
                          "region core must hold exactly one local halt",
                          region_id=region.region_id, core=region.core_id)
    return begins[0], ends[0], halts[0]


def _verify_region_domination(program, region, insert, resume) -> None:
    begin, end, halt = _region_lifecycle(program, region)
    prerequisites = command_prerequisites(program)
    for target, prerequisite, message in (
            (insert.command_id, begin.command_id,
             "request begin must precede the region gate"),
            (halt.command_id, resume.command_id,
             "region gate must precede the local halt"),
            (end.command_id, resume.command_id,
             "region gate must precede request end")):
        if not prerequisite_of(prerequisites, target, prerequisite):
            raise MeshIrError("E_LIFECYCLE", message,
                              region_id=region.region_id)


def _verify_region_gate(program, region) -> None:
    stream = next((s for s in program.streams
                   if s.core_id == region.core_id and
                   s.stream_id == region.stream_id), None)
    if stream is None:
        raise MeshIrError("E_STREAM_CONTRACT", "region stream missing",
                          region_id=region.region_id)
    commands = stream_commands(program, stream)
    positions = {c.command_id: i for i, c in enumerate(commands)}
    if region.insert_after_command_id not in positions or \
            region.resume_before_command_id not in positions:
        raise MeshIrError("E_ABI_BOUNDS", "region gate commands are missing",
                          region_id=region.region_id)
    insert_index = positions[region.insert_after_command_id]
    resume_index = positions[region.resume_before_command_id]
    if resume_index != insert_index + 1:
        raise MeshIrError("E_ABI_BOUNDS",
                          "region gate commands must be adjacent",
                          region_id=region.region_id)
    insert = commands[insert_index]
    if insert.signal_event != region.entry_event_id:
        raise MeshIrError("E_ABI_BOUNDS",
                          "region entry event must be the gate signal",
                          region_id=region.region_id)
    event = next((e for e in program.events
                  if e.event_id == region.entry_event_id), None)
    if event is None or event.producer_command_id != insert.command_id:
        raise MeshIrError("E_ABI_BOUNDS",
                          "region entry event has no matching producer",
                          region_id=region.region_id)
    _verify_region_outside_repeat(program, commands, region, insert_index,
                                  resume_index)
    _verify_region_domination(program, region, insert, commands[resume_index])


def _verify_region_outside_repeat(program, commands, region, insert_index,
                                  resume_index) -> None:
    for command in commands:
        if command.opcode != A.OPCODE.REPEAT:
            continue
        attr = program.op_attrs[command.attr_index - 1]
        values = dict(zip(attr.payload_fields, attr.payload))
        begin = values["subrange_begin_stream_ordinal"]
        count = values["subrange_command_count"]
        covered = range(begin, begin + count)
        if insert_index in covered or resume_index in covered:
            raise MeshIrError(
                "E_ABI_BOUNDS",
                "region gate may not sit inside a REPEAT subrange",
                region_id=region.region_id)


def _verify_kernels(program) -> None:
    for layer in program.moe_layer_specs:
        kernel = program.moe_kernel_specs[layer.kernel_spec_index]
        if kernel.layer_id != layer.layer_id:
            raise MeshIrError("E_ABI_BOUNDS", "kernel layer mismatch",
                              layer_id=layer.layer_id)
        if kernel.expert_opcode not in GEMM_OPCODES:
            raise MeshIrError("E_ABI_ENUM",
                              "kernel opcode must be GEMM or BMM",
                              layer_id=layer.layer_id)
        if kernel.flags != 0:
            raise MeshIrError("E_ABI_RESERVED", "kernel flags must be zero",
                              layer_id=layer.layer_id)
        if kernel.batch == 0 or kernel.n == 0 or kernel.k == 0 or \
                kernel.max_m == 0 or kernel.expert_result_alignment == 0 or (
                kernel.expert_result_alignment &
                (kernel.expert_result_alignment - 1)):
            raise MeshIrError("E_ABI_BOUNDS", "kernel geometry is invalid",
                              layer_id=layer.layer_id)
        if kernel.max_m < layer.max_tokens_per_frozen_batch:
            raise MeshIrError("E_ABI_BOUNDS", "kernel max_m below batch bound",
                              layer_id=layer.layer_id)
        if kernel.input_token_bytes != layer.token_bytes or \
                kernel.output_token_bytes != layer.output_token_bytes:
            raise MeshIrError("E_ABI_BOUNDS",
                              "kernel token bytes disagree with the layer",
                              layer_id=layer.layer_id)
        _verify_kernel_shape(layer, kernel)
        for expert in program.moe_expert_specs[_layer_span(program, layer)]:
            if expert.weight_bytes != kernel.weight_operand_bytes:
                raise MeshIrError("E_ABI_BOUNDS",
                                  "expert weight disagrees with the kernel",
                                  expert_id=expert.expert_id)


def _verify_kernel_shape(layer, kernel) -> None:
    input_bytes = DTYPE_BYTES.get(kernel.input_dtype, 0)
    output_bytes = DTYPE_BYTES.get(kernel.output_dtype, 0)
    planes = 1 if kernel.expert_opcode == A.OPCODE.GEMM else kernel.batch
    expected_input = planes * kernel.k * input_bytes
    expected_output = planes * kernel.n * output_bytes
    expected_weight = planes * kernel.k * kernel.n * input_bytes
    if kernel.input_token_bytes != expected_input or \
            kernel.output_token_bytes != expected_output:
        raise MeshIrError("E_ABI_BOUNDS",
                          "kernel token bytes disagree with its shape",
                          layer_id=layer.layer_id)
    if kernel.weight_operand_bytes != expected_weight:
        raise MeshIrError("E_ABI_BOUNDS",
                          "kernel weight bytes disagree with its shape",
                          layer_id=layer.layer_id)
