from dataclasses import dataclass, replace

from mesh_ir.architecture import ArchManifest, load_arch
from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Access, DType, INVALID_CORE_ID, Layout, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.kernel_ir import AllocAttrs, BufferObject, BufferView, DistributionKind, KernelMemoryRecords, KernelOp, KernelOpcode, KernelTensor, Placement, StateOrigin, TensorShard, TensorState, ViewDeclarationAttrs
from mesh_ir.model import Program
from mesh_ir.scheduled.model import AuthoredProgramOrigin, ExternalSlotBacking, HaltAttrs, ObjectBacking, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.traffic import Binding, BindingSlot
from tests.golden.support.stage2_control_fixture import ROOT
from tests.golden.support.stage4_reference_relocation_cases import (
    build_unused_external_slot_program,
)


@dataclass(frozen=True)
class Stage4TransportTensorReferenceCase:
    case_id: str
    arch: ArchManifest
    baseline: Program
    program: Program
    expected_code: str
    baseline_image_sha256: str
    program_image_sha256: str


def _with_tensor_id(program: Program, index: int, tensor_id: int) -> Program:
    tensors = list(program.tensors)
    tensors[index] = replace(tensors[index], tensor_id=tensor_id)
    provisional = replace(program, semantic_sha256="", tensors=tuple(tensors))
    return replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))


def build_unused_external_alias_slot_program(arch: ArchManifest) -> Program:
    region_id = next(
        index for index, region in enumerate(arch.regions)
        if region.kind == "HBM"
    )
    storage = KernelTensor(
        1, 0, None, 1, 0, "unused-external-storage", TensorRole.INPUT,
        DType.FP16, (8,), (1,), StorageClass.EXTERNAL, Access.READ_ONLY,
        16, 16, None,
    )
    alias = KernelTensor(
        2, 0, None, 1, 0, "unused-external-alias", TensorRole.INPUT,
        DType.FP16, (8,), (1,), StorageClass.EXTERNAL, Access.READ_ONLY,
        16, 16, None,
    )
    records = KernelMemoryRecords(
        (storage, alias),
        (),
        (Placement(1, (0,)),),
        (
            TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED,
                        (0,), (8,), (8,), 0),
            TensorShard(2, 2, 1, 0, DistributionKind.PARTITIONED,
                        (0,), (8,), (8,), 0),
        ),
        (),
        (BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, (8,), (1,),
                      16, arch.axi_data_bytes, False, 0),),
        (BufferView(1, 1, 2, (0,), (8,), (8,), 0, (1,),
                    Layout.CONTIGUOUS_ROW_MAJOR, None, 0),),
        (TensorState(1, 1, 0, StateOrigin.EXTERNAL, 0),),
        (),
        (
            KernelOp(1, 0, "alloc:1", KernelOpcode.ALLOC, INVALID_CORE_ID,
                     0, (), (), AllocAttrs(1), (), None),
            KernelOp(2, 0, "view:1", KernelOpcode.VIEW, INVALID_CORE_ID,
                     0, (), (), ViewDeclarationAttrs(1), (), None),
        ),
    )
    binding = Binding(
        1, region_id, INVALID_CORE_ID, 0, 32, arch.axi_data_bytes,
        Access.READ_ONLY,
    )
    slot = BindingSlot(
        1, "unused-external-storage", MemorySpace.HBM, region_id,
        INVALID_CORE_ID, 16, arch.axi_data_bytes, Access.READ_ONLY, binding,
    )
    builder = ProgramBuilder(
        arch, AuthoredProgramOrigin("unit", "stage4-unused-external-alias", 1)
    )
    variant = builder.variant(
        "main", "unused-alias", "unused-alias", records=records,
        allocations=(),
        external_backings=(ObjectBacking(1, ExternalSlotBacking(1)),),
        binding_slots=(slot,),
    )
    stream = variant.stream(
        0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL,
    )
    stream.control_command(RequestBeginAttrs())
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_stage4_transport_tensor_reference_cases(
) -> tuple[Stage4TransportTensorReferenceCase, ...]:
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    shard_baseline = build_unused_external_slot_program(arch)
    relocation_baseline = build_unused_external_alias_slot_program(arch)
    return (
        Stage4TransportTensorReferenceCase(
            "unused_runtime_shard_tensor_missing", arch, shard_baseline,
            _with_tensor_id(shard_baseline, 0, 2), "E_ABI_BOUNDS",
            "cc9d1d4ee0bdade226841e6bc7bd24f81b982ed83dcccbd2b2089004fba22fc1",
            "25800c80d43f2582f15b1fa8a26223696c2d746c77c58afa9523fdf108cd22b8",
        ),
        Stage4TransportTensorReferenceCase(
            "unused_relocation_tensor_missing", arch, relocation_baseline,
            _with_tensor_id(relocation_baseline, 0, 3), "E_RELOCATION",
            "673c732e707310e690ced1fec69f1e95778cff62142fa2519192ab2e881473fb",
            "ebe945fa426b07047ae96232ada1ea78f32a32bd8fe3e2b5bc329438f690df84",
        ),
    )
