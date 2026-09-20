from dataclasses import dataclass, replace

from mesh_ir.analysis.sram import SramAllocation
from mesh_ir.architecture import ArchManifest, load_arch
from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.golden_programs import _build_p2p_program
from mesh_ir.ir.kernel_ir import (
    DistributionKind,
    KernelMemoryRecords,
    KernelTensor,
    Placement,
    TensorShard,
)
from mesh_ir.model import Program
from mesh_ir.scheduled.model import (
    AuthoredProgramOrigin,
    ControlCommandSource,
    ExternalSlotBacking,
    KernelCommandSource,
    LocalAllocationBacking,
)
from mesh_ir.scheduled.verify import _local_memory_records
from tests.golden.support.stage2_control_fixture import ROOT


@dataclass(frozen=True)
class Stage3DmaParentPairCase:
    case_id: str
    arch: ArchManifest
    baseline: Program
    program: Program
    expected_code: str | None


def _reauthor_p2p(
    arch: ArchManifest, source: Program, records: KernelMemoryRecords, name: str
) -> Program:
    semantics = source.semantics
    local_backings = {
        item.backing.allocation_id: item.object_id
        for item in semantics.object_backings
        if type(item.backing) is LocalAllocationBacking
    }
    allocations = tuple(
        SramAllocation(
            item.allocation_id,
            local_backings[item.allocation_id],
            item.owner_core,
            item.offset_bytes,
            item.size_bytes,
            item.alignment_bytes,
        )
        for item in source.allocations
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", name, 1))
    variant = builder.variant(
        "main",
        name,
        name,
        records=records,
        allocations=allocations,
        external_backings=tuple(
            item
            for item in semantics.object_backings
            if type(item.backing) is ExternalSlotBacking
        ),
        binding_slots=semantics.binding_slots,
    )
    for stream in semantics.streams:
        target = variant.stream(
            stream.core_id, stream.physical_stream_id, stream.flags
        )
        command_ids = semantics.stream_command_ids[
            stream.command_begin : stream.command_begin + stream.command_count
        ]
        for command_id in command_ids:
            command = semantics.command_semantics[command_id - 1]
            if type(command.source) is KernelCommandSource:
                target.kernel_command(command.source.kernel_op_id)
            elif type(command.source) is ControlCommandSource:
                target.control_command(command.source.attrs)
            else:
                raise AssertionError("unexpected authored command source")
    return builder.build()


def build_stage3_dma_parent_pair_cases() -> tuple[Stage3DmaParentPairCase, ...]:
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    baseline = _build_p2p_program(arch, "stage3-dma-parent-pair", 1)
    source = _local_memory_records(
        baseline.semantics, baseline.semantics.variants[0]
    )
    alternate_tensor = KernelTensor(
        2,
        0,
        None,
        1,
        0,
        "destination-logical-root",
        source.tensors[0].role,
        source.tensors[0].dtype,
        source.tensors[0].shape,
        source.tensors[0].strides,
        source.tensors[0].storage_class,
        source.tensors[0].access,
        source.tensors[0].logical_extent_bytes,
        source.tensors[0].storage_extent_bytes,
        None,
    )
    alternate_shard = TensorShard(
        3,
        2,
        2,
        1,
        DistributionKind.REPLICATED,
        (0,),
        source.tensors[0].shape,
        source.tensors[0].shape,
    )
    valid_records = replace(
        source,
        tensors=source.tensors + (alternate_tensor,),
        placements=source.placements + (Placement(2, (1,)),),
        shards=source.shards + (alternate_shard,),
        views=tuple(
            replace(view, shard_id=3) if view.view_id == 2 else view
            for view in source.views
        ),
    )
    baseline = _reauthor_p2p(
        arch, baseline, valid_records, "stage3-dma-parent-root-baseline"
    )
    changed_semantics = replace(
        baseline.semantics,
        kernel_tensors=tuple(
            replace(tensor, alias_root_tensor_id=2)
            if tensor.tensor_id == 2
            else tensor
            for tensor in baseline.semantics.kernel_tensors
        ),
    )
    provisional = replace(
        baseline, semantics=changed_semantics, semantic_sha256=""
    )
    candidate = replace(
        provisional,
        semantic_sha256=semantic_sha256(provisional.semantic_dict()),
    )
    return (
        Stage3DmaParentPairCase(
            "dma_parent_pair_logical_root_mismatch",
            arch,
            baseline,
            candidate,
            "E_DMA_RANGE",
        ),
    )
