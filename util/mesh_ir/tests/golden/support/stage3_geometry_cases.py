from dataclasses import dataclass, replace

from mesh_ir.architecture import ArchManifest, load_arch
from mesh_ir.canonical import U64_MAX, semantic_sha256
from mesh_ir.ir.common import Layout
from mesh_ir.ir.kernel_ir import ElementRegion
from mesh_ir.model import Program
from tests.golden.support.stage2_control_fixture import ROOT
from tests.golden.support.stage3_intrinsic_cases import build_complete_program
from tests.unit.test_gate2_kernel_ir import local_copy_kernel


@dataclass(frozen=True)
class Stage3GeometryCase:
    case_id: str
    arch: ArchManifest
    program: Program
    whole_program_code: str | None = None


def build_stage3_geometry_cases() -> tuple[Stage3GeometryCase, ...]:
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    source = local_copy_kernel().memory_records()
    copy = source.ops[-1]
    unit = ElementRegion((0,), (1,), (1,))
    singleton_records = replace(
        source,
        views=tuple(
            replace(
                view,
                padded_shape=(1,),
                valid_shape=(1,),
                object_strides=(U64_MAX,),
                layout=None,
            )
            if view.view_id == copy.reads[0].view_id
            else view
            for view in source.views
        ),
        ops=tuple(
            replace(
                operation,
                reads=(replace(operation.reads[0], region=unit),),
                writes=(replace(operation.writes[0], region=unit),),
            )
            if operation.op_id == copy.op_id
            else operation
            for operation in source.ops
        ),
    )
    broadcast_records = replace(
        source,
        tensors=tuple(
            replace(
                tensor,
                shape=(1, 4),
                strides=(4, 1),
                logical_extent_bytes=16,
                storage_extent_bytes=16,
            )
            for tensor in source.tensors
        ),
        shards=tuple(
            replace(
                shard,
                global_origin=(0, 0),
                padded_local_shape=(1, 4),
                valid_shape=(1, 4),
            )
            for shard in source.shards
        ),
        objects=tuple(
            replace(object_, shape=(1, 4), strides=(4, 1), footprint_bytes=16)
            for object_ in source.objects
        ),
        views=tuple(
            replace(
                view,
                shard_origin=(0, 0),
                padded_shape=(1, 4),
                valid_shape=(1, 4),
                object_strides=(0, 1)
                if view.view_id == copy.reads[0].view_id
                else (4, 1),
                layout=None
                if view.view_id == copy.reads[0].view_id
                else Layout.CONTIGUOUS_ROW_MAJOR,
            )
            for view in source.views
        ),
        ops=tuple(
            replace(
                operation,
                reads=(
                    replace(
                        operation.reads[0],
                        region=ElementRegion((0, 0), (1, 4), (1, 1)),
                    ),
                ),
                writes=(
                    replace(
                        operation.writes[0],
                        region=ElementRegion((0, 0), (1, 4), (1, 1)),
                    ),
                ),
            )
            if operation.op_id == copy.op_id
            else operation
            for operation in source.ops
        ),
    )
    many_to_one_base = build_complete_program(
        arch, source, "stage3-broadcast-many-to-one"
    )
    many_to_one_provisional = replace(
        many_to_one_base,
        semantics=replace(
            many_to_one_base.semantics,
            views=tuple(
                replace(view, object_strides=(0,), layout=None)
                if view.view_id == copy.reads[0].view_id
                else view
                for view in many_to_one_base.semantics.views
            ),
        ),
        semantic_sha256="",
    )
    many_to_one_program = replace(
        many_to_one_provisional,
        semantic_sha256=semantic_sha256(many_to_one_provisional.semantic_dict()),
    )
    return (
        Stage3GeometryCase(
            "geometry_singleton_u64_stride_positive",
            arch,
            build_complete_program(arch, singleton_records, "stage3-singleton"),
        ),
        Stage3GeometryCase(
            "geometry_broadcast_read_positive",
            arch,
            build_complete_program(arch, broadcast_records, "stage3-broadcast"),
        ),
        Stage3GeometryCase(
            "geometry_broadcast_read_whole_program_rejection",
            arch,
            many_to_one_program,
            "E_DMA_RANGE",
        ),
    )
