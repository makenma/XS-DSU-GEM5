from dataclasses import dataclass, replace

from mesh_ir.architecture import ArchManifest, load_arch
from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_fence_scopes_program, build_single_core_program
from mesh_ir.ir.common import Access, DType, INVALID_CORE_ID, Layout, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.kernel_ir import AllocAttrs, BufferObject, BufferView, DistributionKind, KernelMemoryRecords, KernelOp, KernelOpcode, KernelTensor, Placement, StateOrigin, TensorShard, TensorState, ViewDeclarationAttrs
from mesh_ir.model import Program
from mesh_ir.scheduled.model import AuthoredProgramOrigin, ExternalSlotBacking, HaltAttrs, IdSpan, ObjectBacking, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.traffic import Binding, BindingSlot
from tests.golden.support.stage2_control_fixture import ROOT
from tests.golden.support.stage4_backing_cases import (
    build_two_variant_shared_symbol_program,
)


@dataclass(frozen=True)
class Stage4ReferenceRelocationCase:
    case_id: str
    arch: ArchManifest
    baseline: Program
    program: Program
    expected_code: str | None
    baseline_image_sha256: str | None = None
    program_image_sha256: str | None = None
    roundtrips: bool = True
    expected_message: str | None = None


def _refreshed(program: Program, **changes) -> Program:
    provisional = replace(program, semantic_sha256="", **changes)
    return replace(
        provisional,
        semantic_sha256=semantic_sha256(provisional.semantic_dict()),
    )


def _with_relocation(program: Program, index: int, **changes) -> Program:
    relocations = list(program.relocations)
    relocations[index] = replace(relocations[index], **changes)
    return _refreshed(program, relocations=tuple(relocations))


def build_unused_external_slot_program(arch: ArchManifest) -> Program:
    region_id = next(index for index, region in enumerate(arch.regions) if region.kind == "HBM")
    tensor = KernelTensor(1, 0, None, 1, 0, "unused-external", TensorRole.INPUT, DType.FP16, (8,), (1,), StorageClass.EXTERNAL, Access.READ_ONLY, 16, 16, None)
    shard = TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0,), (8,), (8,), 0)
    object_ = BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, (8,), (1,), 16, arch.axi_data_bytes, False, 0)
    view = BufferView(1, 1, 1, (0,), (8,), (8,), 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0)
    records = KernelMemoryRecords(
        (tensor,),
        (),
        (Placement(1, (0,)),),
        (shard,),
        (),
        (object_,),
        (view,),
        (TensorState(1, 1, 0, StateOrigin.EXTERNAL, 0),),
        (),
        (
            KernelOp(1, 0, "alloc:1", KernelOpcode.ALLOC, INVALID_CORE_ID, 0, (), (), AllocAttrs(1), (), None),
            KernelOp(2, 0, "view:1", KernelOpcode.VIEW, INVALID_CORE_ID, 0, (), (), ViewDeclarationAttrs(1), (), None),
        ),
    )
    binding = Binding(1, region_id, INVALID_CORE_ID, 0, 32, arch.axi_data_bytes, Access.READ_ONLY)
    slot = BindingSlot(1, "unused-external", MemorySpace.HBM, region_id, INVALID_CORE_ID, 16, arch.axi_data_bytes, Access.READ_ONLY, binding)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "stage4-unused-external", 1))
    variant = builder.variant(
        "main",
        "unused",
        "unused",
        records=records,
        allocations=(),
        external_backings=(ObjectBacking(1, ExternalSlotBacking(1)),),
        binding_slots=(slot,),
    )
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_stage4_reference_relocation_cases() -> tuple[Stage4ReferenceRelocationCase, ...]:
    reference_arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    reference = build_single_core_program(reference_arch)
    fence_arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    fence = build_fence_scopes_program(fence_arch)
    shared_symbols = build_two_variant_shared_symbol_program(fence_arch)
    unused_arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    unused = build_unused_external_slot_program(unused_arch)
    unused_variant = unused.semantics.variants[0]
    unused_without_relocation = _refreshed(
        unused,
        relocations=(),
        semantics=replace(
            unused.semantics,
            variants=(
                replace(
                    unused_variant,
                    membership=replace(
                        unused_variant.membership,
                        relocations=IdSpan(1, 0),
                    ),
                ),
            ),
        ),
    )
    cross_variant_shard_swap = _refreshed(
        shared_symbols,
        shards=(
            shared_symbols.shards[4],
            *shared_symbols.shards[1:4],
            shared_symbols.shards[0],
            *shared_symbols.shards[5:],
        ),
    )
    within_variant_shard_reorder = _refreshed(
        shared_symbols,
        shards=(
            shared_symbols.shards[1],
            shared_symbols.shards[0],
            *shared_symbols.shards[2:],
        ),
    )
    empty_symbol = _refreshed(
        unused,
        strings=(*unused.strings[:2], replace(unused.strings[2], value="")),
        semantics=replace(
            unused.semantics,
            binding_slots=(replace(unused.semantics.binding_slots[0], symbol=""),),
        ),
    )
    return (
        Stage4ReferenceRelocationCase("retained_relocation_wrong_offset", reference_arch, reference, _with_relocation(reference, 0, offset_bytes=1048592), "E_RELOCATION", "e66705f21faf82801d8e49df1d0121614647bdccc0f5b78a248cd56ec9cc7fdb", "e09cb83acb790ae56e952eda66f3ff4ce83b321b93369fbccc5991789efe922b"),
        Stage4ReferenceRelocationCase("retained_relocation_wrong_tensor", reference_arch, reference, _with_relocation(reference, 0, tensor_id=3), "E_RELOCATION", "e66705f21faf82801d8e49df1d0121614647bdccc0f5b78a248cd56ec9cc7fdb", "2d378c9906991c87f96afda79ae7853cb912638666c0991f931afe9af32ae796"),
        Stage4ReferenceRelocationCase("retained_relocation_duplicate_id", reference_arch, reference, _with_relocation(reference, 0, relocation_id=2), "E_ABI_DUPLICATE", "e66705f21faf82801d8e49df1d0121614647bdccc0f5b78a248cd56ec9cc7fdb", "07c27e75a49311a9d395681748e61edc73cc49bb3f4f469699c33d9d76bcfa8f"),
        Stage4ReferenceRelocationCase("retained_relocation_wrong_region", reference_arch, reference, _with_relocation(reference, 0, region_id=1), "E_RELOCATION", "e66705f21faf82801d8e49df1d0121614647bdccc0f5b78a248cd56ec9cc7fdb", "2a06a5b731676e063bc932d00ef4833b4f7e9012a7793411361bc58389b425d6"),
        Stage4ReferenceRelocationCase("relocation_slot_identity_mismatch", fence_arch, fence, _with_relocation(fence, 2, relocation_id=4), "E_RELOCATION"),
        Stage4ReferenceRelocationCase("relocation_wrong_symbol", fence_arch, fence, _with_relocation(fence, 0, symbol_sid=fence.relocations[1].symbol_sid), "E_RELOCATION"),
        Stage4ReferenceRelocationCase("relocation_wrong_family", fence_arch, fence, _with_relocation(fence, 0, kind=A.RELOCATION_KIND.REGION_BASE), "E_RELOCATION"),
        Stage4ReferenceRelocationCase("unused_slot_wrong_offset", unused_arch, unused, _with_relocation(unused, 0, offset_bytes=16), "E_RELOCATION"),
        Stage4ReferenceRelocationCase("unused_slot_missing_relocation", unused_arch, unused, unused_without_relocation, "E_RELOCATION"),
        Stage4ReferenceRelocationCase("relocation_cross_variant_row_swap", fence_arch, shared_symbols, _refreshed(shared_symbols, relocations=(shared_symbols.relocations[2], shared_symbols.relocations[1], shared_symbols.relocations[0], shared_symbols.relocations[3])), "E_RELOCATION"),
        Stage4ReferenceRelocationCase("runtime_shard_cross_variant_row_swap", fence_arch, shared_symbols, cross_variant_shard_swap, "E_ABI_BOUNDS", expected_message="resident view is missing or crosses its variant"),
        Stage4ReferenceRelocationCase("runtime_shard_within_variant_row_reorder_positive", fence_arch, shared_symbols, within_variant_shard_reorder, None),
        Stage4ReferenceRelocationCase("unused_slot_empty_symbol", unused_arch, unused, empty_symbol, "E_RELOCATION", expected_message="binding slot symbols must be unique and nonempty"),
        Stage4ReferenceRelocationCase("relocation_reserved_bool", fence_arch, fence, _with_relocation(fence, 0, reserved=False), "E_ABI_BOUNDS", roundtrips=False),
    )
