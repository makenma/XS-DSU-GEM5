import dataclasses
import copy
from pathlib import Path

import pytest
import yaml

from mesh_ir.abi.schema_loader import load_schema
from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.kernel_ir import KernelBundle, KernelModule, KernelOpcode
from mesh_ir.golden_programs import (
    build_barrier_e2e_program,
    build_dual_core_program,
)
from mesh_ir.passes.execution import PassExecutor
from mesh_ir.passes.scheduled import lower_to_program
from mesh_ir.scheduled.model import (
    AuthoredProgramOrigin,
    AuthoredVariantLineage,
    BarrierExecution,
    DmaExecution,
    ExternalSlotBacking,
    KernelCommandSource,
    LocalAllocationBacking,
)
from mesh_ir.scheduled.addressing import derive_descriptor_execution_set
from mesh_ir.scheduled.assemble import _expected_traffic
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.traffic import calculate_traffic
from tests.unit.test_gate3_cpp_compute_verifier import compiler_compute_program
from tests.unit.test_gate2_scheduled_pipeline import (
    COMPILE, _inputs, _lowering_for_bundle,
)
from tests.golden.test_mutation_corpus import _cpp_verdict


SCHEMA_PATH = (
    Path(__file__).parents[2] / "mesh_ir" / "abi" / "mesh_ir_abi.yaml"
)
ROOT = Path(__file__).resolve().parents[4]


def build_two_variant_program():
    arch, lowering, _ = _inputs()
    first = lowering.bundle.modules[0]
    second = KernelModule.create(
        arch.digest().hex(), first.source_semantic_hash, first.entrypoint,
        "p5", first.tensors, first.computations, first.tensor_lineage,
        first.computation_lineage, first.placements, first.shards,
        first.partial_sums, first.objects, first.views, first.states,
        first.tokens, first.ops,
    )
    bundle = KernelBundle.create(
        arch.digest().hex(), lowering.bundle.source_graph_set_sha256,
        (first, second),
    )
    effective = resolve_compile_config(
        load_compile_config_text(
            COMPILE.replace(
                "- {profile_id: p4}",
                "- {profile_id: p4}\n    - {profile_id: p5}",
            ),
            arch,
        ),
        arch,
    )
    with PassExecutor(1) as executor:
        return arch, lower_to_program(
            _lowering_for_bundle(bundle), arch, effective, executor,
        ).program


@pytest.fixture(scope="module")
def two_variant_program():
    return build_two_variant_program()


def _fresh_program(program):
    provisional = dataclasses.replace(program, semantic_sha256="")
    return dataclasses.replace(
        provisional,
        semantic_sha256=semantic_sha256(provisional.semantic_dict()),
    )


def _refresh_dma_projection(program, arch):
    provisional = dataclasses.replace(
        program, expected_traffic=(), semantic_sha256="",
    )
    executions, identity = derive_descriptor_execution_set(provisional, arch)
    traffic = calculate_traffic(arch, identity, executions)
    semantics = dataclasses.replace(
        provisional.semantics,
        reference_binding_identity_sha256=identity,
        intrinsic_traffic=traffic,
    )
    return _fresh_program(dataclasses.replace(
        provisional,
        semantics=semantics,
        expected_traffic=_expected_traffic(traffic, executions, arch),
    ))


def _assert_rejected(program, arch, code):
    with pytest.raises(MeshIrError) as caught:
        verify_program(_fresh_program(program), arch)
    assert caught.value.code == code


def test_variant_profile_bijection_rejects_duplicate_transport_pair(
    compiler_compute_program,
):
    arch, program = compiler_compute_program
    changed = dataclasses.replace(
        program,
        profiles=program.profiles + (program.profiles[0],),
        semantic_sha256="",
    )
    changed = dataclasses.replace(
        changed,
        semantic_sha256=semantic_sha256(changed.semantic_dict()),
    )
    with pytest.raises(MeshIrError) as caught:
        verify_program(changed, arch)
    assert caught.value.code == "E_ABI_DUPLICATE"


def test_schema_rejects_duplicate_variant_membership_target(tmp_path):
    schema = yaml.safe_load(SCHEMA_PATH.read_bytes())
    fields = schema["semantic_records"][
        "mesh_ir.scheduled.model.VariantMembership"
    ]["fields"]
    target = {"source": "transport", "program_field": "tensors"}
    fields[0]["membership_target"] = target
    fields[1]["membership_target"] = copy.deepcopy(target)
    path = tmp_path / "duplicate-target.yaml"
    path.write_text(yaml.safe_dump(schema, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate variant membership target"):
        load_schema(path)


@pytest.mark.parametrize(
    "target",
    (
        None,
        {"source": [], "program_field": "tensors"},
        {"source": "transport", "program_field": "unknown"},
        {"source": "semantic", "program_field": "unknown"},
        {"source": "semantic", "program_field": "stream_command_ids"},
    ),
)
def test_schema_rejects_invalid_variant_membership_targets(tmp_path, target):
    schema = yaml.safe_load(SCHEMA_PATH.read_bytes())
    field = schema["semantic_records"][
        "mesh_ir.scheduled.model.VariantMembership"
    ]["fields"][0]
    if target is None:
        del field["membership_target"]
    else:
        field["membership_target"] = target
    path = tmp_path / "invalid-target.yaml"
    path.write_text(yaml.safe_dump(schema, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError):
        load_schema(path)


def test_generated_variant_membership_targets_preserve_schema_order():
    schema = load_schema(SCHEMA_PATH)
    fields = schema["semantic_records"][
        "mesh_ir.scheduled.model.VariantMembership"
    ]["fields"]
    expected = tuple(
        (field["name"], field["membership_target"]["source"],
         field["membership_target"]["program_field"])
        for field in fields
    )
    assert A.VARIANT_MEMBERSHIP_TARGETS == expected
    assert A.VARIANT_MEMBERSHIP_FIELDS == tuple(field[0] for field in expected)


def _cross_variant_program(program, case):
    first, second = program.semantics.variants
    if case == "command_source":
        operations = {
            operation.op_id: operation for operation in program.semantics.kernel_ops
        }
        offset = next(
            offset for offset in range(second.membership.commands.count)
            if type(program.semantics.command_semantics[
                first.membership.command_semantics.first_id - 1 + offset
            ].source) is KernelCommandSource
            and type(program.semantics.command_semantics[
                second.membership.command_semantics.first_id - 1 + offset
            ].source) is KernelCommandSource
            and program.commands[first.membership.commands.first_id - 1 + offset].opcode
            == program.commands[second.membership.commands.first_id - 1 + offset].opcode
            and operations[program.semantics.command_semantics[
                first.membership.command_semantics.first_id - 1 + offset
            ].source.kernel_op_id].opcode is not KernelOpcode.DMA
        )
        first_semantic_index = first.membership.command_semantics.first_id - 1 + offset
        second_semantic_index = second.membership.command_semantics.first_id - 1 + offset
        first_command_index = first.membership.commands.first_id - 1 + offset
        second_command_index = second.membership.commands.first_id - 1 + offset
        first_semantic = program.semantics.command_semantics[first_semantic_index]
        second_semantic = program.semantics.command_semantics[second_semantic_index]
        command_semantics = list(program.semantics.command_semantics)
        command_semantics[first_semantic_index] = dataclasses.replace(
            first_semantic,
            source=dataclasses.replace(
                first_semantic.source,
                kernel_op_id=second_semantic.source.kernel_op_id,
            ),
        )
        command_semantics[second_semantic_index] = dataclasses.replace(
            second_semantic,
            source=dataclasses.replace(
                second_semantic.source,
                kernel_op_id=first_semantic.source.kernel_op_id,
            ),
        )
        commands = list(program.commands)
        commands[first_command_index] = dataclasses.replace(
            commands[first_command_index],
            source_op_id=program.commands[second_command_index].source_op_id,
        )
        commands[second_command_index] = dataclasses.replace(
            commands[second_command_index],
            source_op_id=program.commands[first_command_index].source_op_id,
        )
        return dataclasses.replace(
            program,
            commands=tuple(commands),
            semantics=dataclasses.replace(
                program.semantics, command_semantics=tuple(command_semantics),
            ),
        )
    if case == "command_event":
        offset = next(
            offset for offset in range(second.membership.commands.count)
            if program.commands[first.membership.commands.first_id - 1 + offset].signal_event
            and program.commands[second.membership.commands.first_id - 1 + offset].signal_event
        )
        first_command_index = first.membership.commands.first_id - 1 + offset
        second_command_index = second.membership.commands.first_id - 1 + offset
        first_command = program.commands[first_command_index]
        second_command = program.commands[second_command_index]
        commands = list(program.commands)
        commands[first_command_index] = dataclasses.replace(
            first_command, signal_event=second_command.signal_event,
        )
        commands[second_command_index] = dataclasses.replace(
            second_command, signal_event=first_command.signal_event,
        )
        events = list(program.events)
        events[first_command.signal_event - 1] = dataclasses.replace(
            events[first_command.signal_event - 1],
            producer_command_id=second_command.command_id,
        )
        events[second_command.signal_event - 1] = dataclasses.replace(
            events[second_command.signal_event - 1],
            producer_command_id=first_command.command_id,
        )
        return dataclasses.replace(
            program, commands=tuple(commands), events=tuple(events),
        )
    if case == "lifecycle_stream":
        variants = (
            dataclasses.replace(
                first, lifecycle_stream_id=second.lifecycle_stream_id,
            ),
            dataclasses.replace(
                second, lifecycle_stream_id=first.lifecycle_stream_id,
            ),
        )
        return dataclasses.replace(
            program,
            semantics=dataclasses.replace(program.semantics, variants=variants),
        )
    if case in ("view_object", "view_shard"):
        field = "object_id" if case == "view_object" else "shard_id"
        first_index = first.membership.views.first_id - 1
        second_index = second.membership.views.first_id - 1
        views = list(program.semantics.views)
        first_view = views[first_index]
        second_view = views[second_index]
        views[first_index] = dataclasses.replace(
            first_view, **{field: getattr(second_view, field)},
        )
        views[second_index] = dataclasses.replace(
            second_view, **{field: getattr(first_view, field)},
        )
        return dataclasses.replace(
            program,
            semantics=dataclasses.replace(program.semantics, views=tuple(views)),
        )
    if case in ("object_tensor", "logical_shard_tensor"):
        records, field = (
            ("objects", "storage_tensor_id")
            if case == "object_tensor"
            else ("logical_shards", "tensor_id")
        )
        first_index = getattr(first.membership, records).first_id - 1
        second_index = getattr(second.membership, records).first_id - 1
        values = list(getattr(program.semantics, records))
        first_value = values[first_index]
        second_value = values[second_index]
        values[first_index] = dataclasses.replace(
            first_value, **{field: getattr(second_value, field)},
        )
        values[second_index] = dataclasses.replace(
            second_value, **{field: getattr(first_value, field)},
        )
        return dataclasses.replace(
            program,
            semantics=dataclasses.replace(
                program.semantics, **{records: tuple(values)},
            ),
        )
    if case in ("local_allocation", "external_slot"):
        backing_type, field = (
            (LocalAllocationBacking, "allocation_id")
            if case == "local_allocation"
            else (ExternalSlotBacking, "slot_id")
        )
        first_index = next(
            index for index in range(
                first.membership.object_backings.first_id - 1,
                first.membership.object_backings.first_id - 1
                + first.membership.object_backings.count,
            )
            if type(program.semantics.object_backings[index].backing)
            is backing_type
        )
        second_index = next(
            index for index in range(
                second.membership.object_backings.first_id - 1,
                second.membership.object_backings.first_id - 1
                + second.membership.object_backings.count,
            )
            if type(program.semantics.object_backings[index].backing)
            is backing_type
        )
        backings = list(program.semantics.object_backings)
        first_backing = backings[first_index]
        second_backing = backings[second_index]
        backings[first_index] = dataclasses.replace(
            first_backing,
            backing=dataclasses.replace(
                first_backing.backing,
                **{field: getattr(second_backing.backing, field)},
            ),
        )
        backings[second_index] = dataclasses.replace(
            second_backing,
            backing=dataclasses.replace(
                second_backing.backing,
                **{field: getattr(first_backing.backing, field)},
            ),
        )
        return dataclasses.replace(
            program,
            semantics=dataclasses.replace(
                program.semantics, object_backings=tuple(backings),
            ),
        )
    if case in ("object_backing_row", "binding_slot_row"):
        records = (
            "object_backings"
            if case == "object_backing_row"
            else "binding_slots"
        )
        first_index = getattr(first.membership, records).first_id - 1
        second_index = getattr(second.membership, records).first_id - 1
        rows = list(getattr(program.semantics, records))
        rows[first_index], rows[second_index] = (
            rows[second_index], rows[first_index],
        )
        return dataclasses.replace(
            program,
            semantics=dataclasses.replace(
                program.semantics, **{records: tuple(rows)},
            ),
        )
    if case == "resident_view":
        first_index = first.membership.runtime_shards.first_id - 1
        second_index = second.membership.runtime_shards.first_id - 1
        residents = list(program.semantics.resident_views)
        first_resident = residents[first_index]
        second_resident = residents[second_index]
        residents[first_index] = dataclasses.replace(
            first_resident, view_id=second_resident.view_id,
        )
        residents[second_index] = dataclasses.replace(
            second_resident, view_id=first_resident.view_id,
        )
        return dataclasses.replace(
            program,
            semantics=dataclasses.replace(
                program.semantics, resident_views=tuple(residents),
            ),
        )
    if case == "descriptor_group":
        index = next(
            index for index, group in enumerate(
                program.semantics.descriptor_groups,
            )
            if second.membership.commands.first_id <= group.command_id
            < second.membership.commands.first_id + second.membership.commands.count
        )
        groups = list(program.semantics.descriptor_groups)
        groups[index] = dataclasses.replace(
            groups[index],
            command_id=(
                first.membership.commands.first_id
                + groups[index].command_id - second.membership.commands.first_id
            ),
        )
        return dataclasses.replace(
            program,
            semantics=dataclasses.replace(
                program.semantics, descriptor_groups=tuple(groups),
            ),
        )
    if case == "descriptor_endpoint":
        index = next(
            index for index, endpoint in enumerate(
                program.semantics.endpoint_uses,
            )
            if second.membership.descriptors.first_id <= endpoint.descriptor_id
            < second.membership.descriptors.first_id
            + second.membership.descriptors.count
        )
        endpoint = program.semantics.endpoint_uses[index]
        endpoints = list(program.semantics.endpoint_uses)
        endpoints[index] = dataclasses.replace(
            endpoint,
            use=dataclasses.replace(
                endpoint.use,
                kernel_op_id=(
                    first.membership.kernel_ops.first_id
                    + endpoint.use.kernel_op_id
                    - second.membership.kernel_ops.first_id
                ),
            ),
        )
        return dataclasses.replace(
            program,
            semantics=dataclasses.replace(
                program.semantics, endpoint_uses=tuple(endpoints),
            ),
        )
    if case == "compiled_origin":
        return dataclasses.replace(
            program,
            semantics=dataclasses.replace(
                program.semantics,
                origin=dataclasses.replace(
                    program.semantics.origin,
                    kernel_bundle_semantic_sha256="0" * 63,
                ),
            ),
        )
    if case == "authored_lineage":
        variants = tuple(
            dataclasses.replace(
                variant, lineage=AuthoredVariantLineage("context-duplicate"),
            )
            for variant in program.semantics.variants
        )
        return dataclasses.replace(
            program,
            semantics=dataclasses.replace(
                program.semantics,
                origin=AuthoredProgramOrigin("unit", "context", 1),
                variants=variants,
            ),
        )
    raise AssertionError(case)


CROSS_VARIANT_CASES = (
    ("command_source", "E_ABI_BOUNDS"),
    ("command_event", "E_ABI_BOUNDS"),
    ("lifecycle_stream", "E_STREAM_CONTRACT"),
    ("view_object", "E_ABI_BOUNDS"),
    ("view_shard", "E_ABI_BOUNDS"),
    ("object_tensor", "E_ABI_BOUNDS"),
    ("logical_shard_tensor", "E_ABI_BOUNDS"),
    ("local_allocation", "E_ABI_BOUNDS"),
    ("external_slot", "E_ABI_BOUNDS"),
    ("object_backing_row", "E_ABI_BOUNDS"),
    ("binding_slot_row", "E_ABI_BOUNDS"),
    ("resident_view", "E_ABI_BOUNDS"),
    ("descriptor_group", "E_DMA_RANGE"),
    ("descriptor_endpoint", "E_DMA_RANGE"),
    ("compiled_origin", "E_ABI_CHECKSUM"),
    ("authored_lineage", "E_ABI_DUPLICATE"),
)


def _with_sparse_endpoint_ref_ids(program, arch):
    semantics = dataclasses.replace(
        program.semantics,
        endpoint_uses=tuple(
            dataclasses.replace(endpoint, ref_id=endpoint.ref_id + 10000)
            for endpoint in program.semantics.endpoint_uses
        ),
    )
    return _refresh_dma_projection(
        dataclasses.replace(program, semantics=semantics), arch,
    )


def _with_sparse_descriptor_group_ids(program, arch):
    semantics = dataclasses.replace(
        program.semantics,
        descriptor_groups=tuple(
            dataclasses.replace(group, group_id=group.group_id + 10000)
            for group in program.semantics.descriptor_groups
        ),
        command_semantics=tuple(
            dataclasses.replace(
                semantic,
                execution=dataclasses.replace(
                    semantic.execution,
                    descriptor_group_id=(
                        semantic.execution.descriptor_group_id + 10000
                    ),
                ),
            )
            if type(semantic.execution) is DmaExecution else semantic
            for semantic in program.semantics.command_semantics
        ),
    )
    return _refresh_dma_projection(
        dataclasses.replace(program, semantics=semantics), arch,
    )


def _with_cross_variant_endpoint_row_reordering(program, _arch):
    first, second = program.semantics.variants
    first_index = first.membership.endpoint_uses.first_id - 1
    second_index = second.membership.endpoint_uses.first_id - 1
    endpoints = list(program.semantics.endpoint_uses)
    endpoints[first_index], endpoints[second_index] = (
        endpoints[second_index], endpoints[first_index],
    )
    return _fresh_program(dataclasses.replace(
        program,
        semantics=dataclasses.replace(
            program.semantics, endpoint_uses=tuple(endpoints),
        ),
    ))


def _with_endpoint_address_ref_id(program, ref_id):
    endpoints = list(program.semantics.endpoint_uses)
    endpoints[0] = dataclasses.replace(endpoints[0], ref_id=ref_id)
    return dataclasses.replace(
        program,
        semantics=dataclasses.replace(
            program.semantics, endpoint_uses=tuple(endpoints),
        ),
    )


DMA_JOINED_POSITIVES = (
    ("sparse_endpoint_ref_ids", _with_sparse_endpoint_ref_ids),
    ("sparse_descriptor_group_ids", _with_sparse_descriptor_group_ids),
    ("cross_variant_endpoint_row_reordering",
     _with_cross_variant_endpoint_row_reordering),
)


@pytest.mark.parametrize(
    ("case", "code"), CROSS_VARIANT_CASES,
    ids=tuple(case for case, _ in CROSS_VARIANT_CASES),
)
def test_variant_context_rejects_cross_variant_references(
    two_variant_program, case, code,
):
    arch, program = two_variant_program
    _assert_rejected(_cross_variant_program(program, case), arch, code)


@pytest.mark.parametrize(
    ("_name", "mutation"), DMA_JOINED_POSITIVES,
    ids=tuple(name for name, _ in DMA_JOINED_POSITIVES),
)
def test_variant_context_keeps_sparse_or_reordered_dma_joined_rows(
    two_variant_program, _name, mutation,
):
    arch, program = two_variant_program
    assert verify_program(mutation(program, arch), arch).program is not None


@pytest.mark.parametrize("ref_id", (0, 1 << 32), ids=("zero", "above_u32"))
def test_variant_context_rejects_invalid_endpoint_address_identity(
    two_variant_program, ref_id,
):
    arch, program = two_variant_program
    _assert_rejected(
        _with_endpoint_address_ref_id(program, ref_id), arch, "E_RELOCATION",
    )


def test_cpp_context_accepts_complete_two_variant_program(
    driver, tmp_path, two_variant_program,
):
    arch, program = two_variant_program
    assert _cpp_verdict(
        encode_program(program), driver, tmp_path, arch,
    ) == "ACCEPTED"


@pytest.mark.parametrize(
    ("case", "code"), CROSS_VARIANT_CASES,
    ids=tuple(case for case, _ in CROSS_VARIANT_CASES),
)
def test_cpp_context_matches_cross_variant_diagnostics(
    driver, tmp_path, two_variant_program, case, code,
):
    arch, program = two_variant_program
    candidate = _fresh_program(_cross_variant_program(program, case))
    assert _cpp_verdict(
        encode_program(candidate), driver, tmp_path, arch,
    ) == f"VERIFY:{code}"


@pytest.mark.parametrize(
    ("_name", "mutation"), DMA_JOINED_POSITIVES,
    ids=tuple(name for name, _ in DMA_JOINED_POSITIVES),
)
def test_cpp_context_keeps_sparse_or_reordered_dma_joined_rows(
    driver, tmp_path, two_variant_program, _name, mutation,
):
    arch, program = two_variant_program
    assert _cpp_verdict(
        encode_program(mutation(program, arch)), driver, tmp_path,
        arch,
    ) == "ACCEPTED"


@pytest.mark.parametrize("ref_id", (0, 1 << 32), ids=("zero", "above_u32"))
def test_cpp_context_rejects_invalid_endpoint_address_identity(
    driver, tmp_path, two_variant_program, ref_id,
):
    arch, program = two_variant_program
    candidate = _fresh_program(_with_endpoint_address_ref_id(program, ref_id))
    assert _cpp_verdict(
        encode_program(candidate), driver, tmp_path, arch,
    ) == "VERIFY:E_RELOCATION"


def _within_variant_joined_rows(program, records):
    first = program.semantics.variants[0]
    first_index = getattr(first.membership, records).first_id - 1
    rows = list(getattr(program.semantics, records))
    rows[first_index], rows[first_index + 1] = (
        rows[first_index + 1], rows[first_index],
    )
    return dataclasses.replace(
        program,
        semantics=dataclasses.replace(
            program.semantics, **{records: tuple(rows)},
        ),
    )


@pytest.mark.parametrize("records", ("object_backings", "binding_slots"))
def test_variant_context_keeps_within_variant_joined_row_reordering(
    two_variant_program, records,
):
    arch, program = two_variant_program
    assert verify_program(
        _fresh_program(_within_variant_joined_rows(program, records)), arch,
    ).program is not None


@pytest.mark.parametrize("records", ("object_backings", "binding_slots"))
def test_cpp_context_keeps_within_variant_joined_row_reordering(
    driver, tmp_path, two_variant_program, records,
):
    arch, program = two_variant_program
    candidate = _fresh_program(_within_variant_joined_rows(program, records))
    assert _cpp_verdict(
        encode_program(candidate), driver, tmp_path, arch,
    ) == "ACCEPTED"


@pytest.fixture(scope="module")
def root_identity_programs(two_variant_program):
    two_variant_arch, two_variant = two_variant_program
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    return (
        (two_variant_arch, two_variant, "placements", "placement_id", "E_ABI_BOUNDS"),
        (two_variant_arch, two_variant, "tokens", "token_id", "E_ABI_BOUNDS"),
        (two_variant_arch, two_variant, "dependencies", "dependency_id", "E_ABI_ORDER"),
        (arch, build_dual_core_program(arch), "partial_sums", "partial_sum_id", "E_ABI_BOUNDS"),
        (arch, build_barrier_e2e_program(arch), "barrier_groups", "barrier_group_id", "E_ABI_ORDER"),
    )


def _with_zero_root_identity(program, records, identity):
    rows = list(getattr(program.semantics, records))
    original = getattr(rows[0], identity)
    rows[0] = dataclasses.replace(rows[0], **{identity: 0})
    semantics = dataclasses.replace(
        program.semantics, **{records: tuple(rows)},
    )
    if records == "barrier_groups":
        command_semantics = tuple(
            dataclasses.replace(
                semantic,
                execution=dataclasses.replace(
                    semantic.execution, barrier_group_id=0,
                ),
            )
            if type(semantic.execution) is BarrierExecution
            and semantic.execution.barrier_group_id == original
            else semantic
            for semantic in semantics.command_semantics
        )
        semantics = dataclasses.replace(
            semantics, command_semantics=command_semantics,
        )
    return dataclasses.replace(
        program, semantics=semantics,
    )


def _with_nonlocal_root_identity(program, records, identity, value):
    rows = list(getattr(program.semantics, records))
    rows[0] = dataclasses.replace(rows[0], **{identity: value})
    return dataclasses.replace(
        program,
        semantics=dataclasses.replace(
            program.semantics, **{records: tuple(rows)},
        ),
    )


def _with_cross_variant_root_identity_swap(program, records, identity):
    first, second = program.semantics.variants
    first_index = getattr(first.membership, records).first_id - 1
    second_index = getattr(second.membership, records).first_id - 1
    rows = list(getattr(program.semantics, records))
    first_row = rows[first_index]
    second_row = rows[second_index]
    rows[first_index] = dataclasses.replace(
        first_row, **{identity: getattr(second_row, identity)},
    )
    rows[second_index] = dataclasses.replace(
        second_row, **{identity: getattr(first_row, identity)},
    )
    return dataclasses.replace(
        program,
        semantics=dataclasses.replace(
            program.semantics, **{records: tuple(rows)},
        ),
    )


@pytest.mark.parametrize(
    "index", range(5),
    ids=("placement", "token", "dependency", "partial_sum", "barrier_group"),
)
def test_variant_context_rejects_zero_logical_root_identity(
    root_identity_programs, index,
):
    arch, program, records, identity, code = root_identity_programs[index]
    _assert_rejected(
        _with_zero_root_identity(program, records, identity),
        arch,
        code,
    )


@pytest.mark.parametrize(
    ("records", "identity", "value"),
    (
        ("kernel_tensors", "tensor_id", 1 << 32),
        ("tokens", "token_id", (1 << 64) - 1),
    ),
    ids=("tensor_above_u32", "token_u64_max"),
)
def test_variant_context_rejects_nonlocal_kernel_root_identity(
    two_variant_program, records, identity, value,
):
    arch, program = two_variant_program
    _assert_rejected(
        _with_nonlocal_root_identity(program, records, identity, value),
        arch,
        "E_ABI_BOUNDS",
    )


@pytest.mark.parametrize(
    ("records", "identity"),
    (("kernel_tensors", "tensor_id"), ("tokens", "token_id")),
)
def test_variant_context_rejects_cross_variant_kernel_root_identity_swap(
    two_variant_program, records, identity,
):
    arch, program = two_variant_program
    _assert_rejected(
        _with_cross_variant_root_identity_swap(program, records, identity),
        arch,
        "E_ABI_BOUNDS",
    )


@pytest.mark.parametrize(
    "index", range(5),
    ids=("placement", "token", "dependency", "partial_sum", "barrier_group"),
)
def test_cpp_context_rejects_zero_logical_root_identity(
    driver, tmp_path, root_identity_programs, index,
):
    arch, program, records, identity, code = root_identity_programs[index]
    candidate = _fresh_program(
        _with_zero_root_identity(program, records, identity),
    )
    assert _cpp_verdict(
        encode_program(candidate), driver, tmp_path, arch,
    ) == f"VERIFY:{code}"


@pytest.mark.parametrize(
    ("records", "identity", "value"),
    (
        ("kernel_tensors", "tensor_id", 1 << 32),
        ("tokens", "token_id", (1 << 64) - 1),
    ),
    ids=("tensor_above_u32", "token_u64_max"),
)
def test_cpp_context_rejects_nonlocal_kernel_root_identity(
    driver, tmp_path, two_variant_program, records, identity, value,
):
    arch, program = two_variant_program
    candidate = _fresh_program(
        _with_nonlocal_root_identity(program, records, identity, value),
    )
    assert _cpp_verdict(
        encode_program(candidate), driver, tmp_path, arch,
    ) == "VERIFY:E_ABI_BOUNDS"


@pytest.mark.parametrize(
    ("records", "identity"),
    (("kernel_tensors", "tensor_id"), ("tokens", "token_id")),
)
def test_cpp_context_rejects_cross_variant_kernel_root_identity_swap(
    driver, tmp_path, two_variant_program, records, identity,
):
    arch, program = two_variant_program
    candidate = _fresh_program(
        _with_cross_variant_root_identity_swap(program, records, identity),
    )
    assert _cpp_verdict(
        encode_program(candidate), driver, tmp_path, arch,
    ) == "VERIFY:E_ABI_BOUNDS"
