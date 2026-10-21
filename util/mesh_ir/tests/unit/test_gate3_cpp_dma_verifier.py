from dataclasses import replace

import pytest

from mesh_ir.abi import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.golden_programs import (
    _build_p2p_program,
    build_compute_timing_program,
    build_dma_shapes_program,
    build_fill_program,
    build_single_core_program,
    build_zero_dma_program,
)
from mesh_ir.ir.common import DmaKind
from mesh_ir.ir.kernel_ir import DmaAttrs, ElementRegion, KernelOpcode
from mesh_ir.scheduled.addressing import derive_descriptor_execution_set
from mesh_ir.scheduled.assemble import _expected_traffic, assemble_program
from mesh_ir.scheduled.model import (
    DescriptorEndpointUse,
    DescriptorSource,
    EndpointSide,
    ReadAccessUse,
    WriteAccessUse,
)
from mesh_ir.scheduled.verify import verify_pretraffic_state, verify_program
from mesh_ir.traffic import calculate_traffic
from tests.golden.test_mutation_corpus import _cpp_verdict
from tests.unit.test_gate2_scheduled_dma import _state, _with_descriptor_pieces
from tests.unit.test_gate2_empty_dma_geometry import _reauthor, _records
from tests.unit.test_gate3_cpp_verifier import ROOT


@pytest.fixture(scope="module")
def dma_program():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    return arch, build_compute_timing_program(arch)


def _with_endpoints(program, endpoints):
    changed = replace(
        program,
        semantics=replace(program.semantics, endpoint_uses=tuple(endpoints)),
    )
    return replace(
        changed, semantic_sha256=semantic_sha256(changed.semantic_dict())
    )


def _rehash(program):
    return replace(
        program, semantic_sha256=semantic_sha256(program.semantic_dict())
    )


def _assert_parity(program, arch, driver, tmp_path, expected_code):
    with pytest.raises(MeshIrError) as caught:
        verify_program(program, arch)
    assert caught.value.code == expected_code
    result = _cpp_verdict(encode_program(program), driver, tmp_path, arch)
    assert result == f"VERIFY:{expected_code}", (
        caught.value.code,
        result,
    )


def _with_split_p2p(program, arch):
    descriptors = []
    for descriptor in program.dma_descriptors:
        if descriptor.descriptor_id < 3:
            descriptors.append(descriptor)
        elif descriptor.descriptor_id == 3:
            for part in range(2):
                descriptors.append(
                    replace(
                        descriptor,
                        descriptor_id=3 + part,
                        src=replace(descriptor.src, offset_bytes=part * 64),
                        dst=replace(descriptor.dst, offset_bytes=part * 128),
                        rows=1,
                        row_bytes=64,
                        src_stride_bytes=64,
                        dst_stride_bytes=64,
                        useful_bytes=64,
                        physical_storage_bytes=64,
                    )
                )
        else:
            descriptors.append(
                replace(descriptor, descriptor_id=descriptor.descriptor_id + 1)
            )
    endpoint_uses = list(program.semantics.endpoint_uses[:4])
    p2p_endpoints = {
        endpoint.side: endpoint
        for endpoint in program.semantics.endpoint_uses
        if endpoint.descriptor_id == 3
    }
    for part in range(2):
        region = ElementRegion((part, 0), (1, 16), (1, 1))
        for side_offset, (side, use_type) in enumerate(
            (
                (EndpointSide.SRC, ReadAccessUse),
                (EndpointSide.DST, WriteAccessUse),
            )
        ):
            endpoint = p2p_endpoints[side]
            endpoint_uses.append(
                DescriptorEndpointUse(
                    5 + part * 2 + side_offset,
                    3 + part,
                    side,
                    use_type(
                        endpoint.use.kernel_op_id,
                        endpoint.use.access_index,
                        region,
                    ),
                )
            )
    endpoint_uses.extend(
        replace(
            endpoint,
            ref_id=endpoint.ref_id + 2,
            descriptor_id=endpoint.descriptor_id + 1,
        )
        for endpoint in program.semantics.endpoint_uses
        if endpoint.descriptor_id > 3
    )
    groups = tuple(
        replace(group, descriptor_ids=(3, 4))
        if group.group_id == 3
        else replace(
            group,
            descriptor_ids=tuple(
                descriptor_id + 1 if descriptor_id > 3 else descriptor_id
                for descriptor_id in group.descriptor_ids
            ),
        )
        for group in program.semantics.descriptor_groups
    )
    dependencies = []
    for dependency in program.semantics.dependencies:
        source = dependency.source
        if (
            isinstance(source, DescriptorSource)
            and source.descriptor_id > 3
        ):
            source = DescriptorSource(source.descriptor_id + 1)
        dependencies.append(replace(dependency, source=source))
    p2p_dependencies = tuple(
        dependency
        for dependency in program.semantics.dependencies
        if isinstance(dependency.source, DescriptorSource)
        and dependency.source.descriptor_id == 3
    )
    first_dependency_id = len(dependencies) + 1
    dependencies.extend(
        replace(
            dependency,
            dependency_id=first_dependency_id + index,
            source=DescriptorSource(4),
        )
        for index, dependency in enumerate(p2p_dependencies)
    )
    membership = program.semantics.variants[0].membership
    membership = replace(
        membership,
        descriptors=replace(
            membership.descriptors, count=membership.descriptors.count + 1
        ),
        dependencies=replace(
            membership.dependencies,
            count=membership.dependencies.count + len(p2p_dependencies),
        ),
        endpoint_uses=replace(
            membership.endpoint_uses,
            count=membership.endpoint_uses.count + 2,
        ),
    )
    semantics = replace(
        program.semantics,
        variants=(
            replace(program.semantics.variants[0], membership=membership),
        ),
        descriptor_groups=groups,
        endpoint_uses=tuple(endpoint_uses),
        dependencies=tuple(dependencies),
    )
    provisional = replace(
        program,
        dma_descriptors=tuple(descriptors),
        expected_traffic=(),
        semantics=semantics,
        semantic_sha256="",
    )
    executions, identity = derive_descriptor_execution_set(
        provisional, arch
    )
    traffic = calculate_traffic(arch, identity, executions)
    semantics = replace(
        semantics,
        reference_binding_identity_sha256=identity,
        intrinsic_traffic=traffic,
    )
    provisional = replace(
        provisional,
        semantics=semantics,
        expected_traffic=_expected_traffic(traffic, executions, arch),
    )
    return _rehash(provisional)


def _empty_prefetch_program(arch):
    source = build_zero_dma_program(arch)
    operations = list(source.semantics.kernel_ops)
    index, operation = next(
        (index, operation)
        for index, operation in enumerate(operations)
        if operation.stable_key == "load:zero_bytes"
    )
    operations[index] = replace(
        operation,
        attrs=replace(
            operation.attrs, kind=DmaKind.PREFETCH, max_burst_beats=8
        ),
    )
    return _reauthor(
        arch, source, _records(source, tuple(operations))
    ).build()


def _empty_store_program(arch):
    source = build_fill_program(arch)
    operations = list(source.semantics.kernel_ops)
    index, operation = next(
        (index, operation)
        for index, operation in enumerate(operations)
        if operation.opcode is KernelOpcode.DMA
        and operation.attrs.kind is DmaKind.STORE
    )
    region = ElementRegion((0, 0), (1, 0), (1, 1))
    operations[index] = replace(
        operation,
        reads=(replace(operation.reads[0], region=region),),
        writes=(replace(operation.writes[0], region=region),),
    )
    return _reauthor(
        arch, source, _records(source, tuple(operations))
    ).build()


def _with_dma_attrs(program, kind, **changes):
    operations = list(program.semantics.kernel_ops)
    index, operation = next(
        (index, operation)
        for index, operation in enumerate(operations)
        if isinstance(operation.attrs, DmaAttrs)
        and operation.attrs.kind is kind
    )
    operations[index] = replace(
        operation, attrs=replace(operation.attrs, **changes)
    )
    return _rehash(
        replace(
            program,
            semantics=replace(
                program.semantics, kernel_ops=tuple(operations)
            ),
        )
    )


def test_dma_projection_accepts_complete_positive_domains(
    dma_program, driver, tmp_path
):
    arch, timing = dma_program
    base = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)
    programs = (
        build_single_core_program(arch),
        timing,
        build_dma_shapes_program(arch),
        build_zero_dma_program(arch),
        _empty_store_program(arch),
        _empty_prefetch_program(arch),
        _with_descriptor_pieces(base, arch, (0, 2)),
        _with_split_p2p(build_dma_shapes_program(arch), arch),
        _build_p2p_program(arch, "dma-domain-two-p2p", 2),
    )
    for index, program in enumerate(programs):
        verify_program(program, arch)
        case = tmp_path / str(index)
        case.mkdir()
        assert _cpp_verdict(
            encode_program(program), driver, case, arch
        ) == "ACCEPTED"


@pytest.mark.parametrize("value", (1, 9999))
def test_dma_endpoint_access_index_is_actual_ordered_pair(
    dma_program, driver, tmp_path, value
):
    arch, program = dma_program
    endpoints = list(program.semantics.endpoint_uses)
    endpoints[0] = replace(
        endpoints[0], use=replace(endpoints[0].use, access_index=value)
    )
    _assert_parity(
        _with_endpoints(program, endpoints),
        arch,
        driver,
        tmp_path,
        "E_DMA_RANGE",
    )


def test_dma_endpoint_region_lies_on_access_lattice(
    dma_program, driver, tmp_path
):
    arch, program = dma_program
    endpoints = list(program.semantics.endpoint_uses)
    endpoint = endpoints[0]
    endpoints[0] = replace(
        endpoint,
        use=replace(
            endpoint.use,
            region=replace(
                endpoint.use.region,
                origin=tuple(9999 for _ in endpoint.use.region.origin),
            ),
        ),
    )
    _assert_parity(
        _with_endpoints(program, endpoints),
        arch,
        driver,
        tmp_path,
        "E_DMA_RANGE",
    )


def test_dma_fill_source_uses_write_access(
    dma_program, driver, tmp_path
):
    arch, program = dma_program
    endpoints = list(program.semantics.endpoint_uses)
    endpoint = endpoints[0]
    endpoints[0] = replace(
        endpoint,
        use=ReadAccessUse(
            endpoint.use.kernel_op_id,
            endpoint.use.access_index,
            endpoint.use.region,
        ),
    )
    _assert_parity(
        _with_endpoints(program, endpoints),
        arch,
        driver,
        tmp_path,
        "E_DMA_RANGE",
    )


@pytest.mark.parametrize(
    ("descriptor_ids", "expected_code"),
    (((2, 1), "E_DMA_RANGE"), ((1,), "E_DMA_RANGE"), ((1, 1), "E_DMA_RANGE")),
)
def test_dma_group_preserves_complete_piece_order(
    dma_program, driver, tmp_path, descriptor_ids, expected_code
):
    arch, _ = dma_program
    base = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)
    program = _with_descriptor_pieces(base, arch, (0, 2))
    groups = list(program.semantics.descriptor_groups)
    groups[0] = replace(groups[0], descriptor_ids=descriptor_ids)
    changed = replace(
        program,
        semantics=replace(program.semantics, descriptor_groups=tuple(groups)),
    )
    _assert_parity(
        _rehash(changed), arch, driver, tmp_path, expected_code
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("rows", 2),
        ("row_bytes", 2),
        ("src_stride_bytes", 1),
        ("dst_stride_bytes", 1),
        ("useful_bytes", 8),
        ("physical_storage_bytes", 8),
        ("max_burst_beats", 1),
    ),
)
def test_dma_geometry_is_exact_access_projection(
    dma_program, driver, tmp_path, field, replacement
):
    arch, program = dma_program
    descriptors = list(program.dma_descriptors)
    value = (
        getattr(descriptors[0], field) + 1
        if field in ("rows", "row_bytes")
        else replacement
    )
    assert value != getattr(descriptors[0], field)
    descriptors[0] = replace(descriptors[0], **{field: value})
    changed = replace(program, dma_descriptors=tuple(descriptors))
    _assert_parity(
        _rehash(changed), arch, driver, tmp_path, "E_DMA_RANGE"
    )


def test_distinct_p2p_operations_cannot_reuse_transfer_identity(
    dma_program, driver, tmp_path
):
    arch, _ = dma_program
    program = _build_p2p_program(arch, "dma-domain-duplicate-p2p", 2)
    operations = list(program.semantics.kernel_ops)
    p2p_indices = tuple(
        index
        for index, operation in enumerate(operations)
        if isinstance(operation.attrs, DmaAttrs)
        and operation.attrs.kind is DmaKind.P2P_PUSH
    )
    transfer_id = operations[p2p_indices[0]].attrs.transfer_id
    second = operations[p2p_indices[1]]
    operations[p2p_indices[1]] = replace(
        second, attrs=replace(second.attrs, transfer_id=transfer_id)
    )
    descriptors = list(program.dma_descriptors)
    p2p_descriptors = tuple(
        index
        for index, descriptor in enumerate(descriptors)
        if descriptor.transfer_id != 0
    )
    descriptors[p2p_descriptors[1]] = replace(
        descriptors[p2p_descriptors[1]], transfer_id=transfer_id
    )
    changed = replace(
        program,
        semantics=replace(
            program.semantics, kernel_ops=tuple(operations)
        ),
        dma_descriptors=tuple(descriptors),
    )
    _assert_parity(
        _rehash(changed), arch, driver, tmp_path, "E_P2P_UNMATCHED"
    )


@pytest.mark.parametrize(
    ("kind", "changes", "expected_code"),
    (
        (DmaKind.LOAD, {"source_core": 0}, "E_DMA_RANGE"),
        (DmaKind.STORE, {"destination_core": 0}, "E_DMA_RANGE"),
        (DmaKind.LOCAL_FILL, {"source_core": 1}, "E_DMA_RANGE"),
        (DmaKind.P2P_PUSH, {"source_core": 1}, "E_P2P_UNMATCHED"),
        (DmaKind.P2P_PUSH, {"destination_core": 0}, "E_P2P_UNMATCHED"),
    ),
)
def test_dma_semantic_core_identities_match_endpoints(
    dma_program, driver, tmp_path, kind, changes, expected_code
):
    arch, program = dma_program
    if kind in (DmaKind.LOAD, DmaKind.STORE):
        program = build_single_core_program(arch)
    _assert_parity(
        _with_dma_attrs(program, kind, **changes),
        arch,
        driver,
        tmp_path,
        expected_code,
    )


def test_dma_fill_pattern_matches_transport_projection(
    dma_program, driver, tmp_path
):
    arch, program = dma_program
    _assert_parity(
        _with_dma_attrs(program, DmaKind.LOCAL_FILL, fill_pattern=b"\x12"),
        arch,
        driver,
        tmp_path,
        "E_ABI_ENUM",
    )


def test_dma_transport_fill_value_matches_semantic_projection(
    dma_program, driver, tmp_path
):
    arch, program = dma_program
    attrs = list(program.op_attrs)
    attrs[0] = replace(attrs[0], payload=(0x12, 0))
    _assert_parity(
        _rehash(replace(program, op_attrs=tuple(attrs))),
        arch,
        driver,
        tmp_path,
        "E_ABI_ENUM",
    )


def test_dma_fill_pattern_fits_transport_width(
    dma_program, driver, tmp_path
):
    arch, program = dma_program
    _assert_parity(
        _with_dma_attrs(
            program, DmaKind.LOCAL_FILL, fill_pattern=b"123456789"
        ),
        arch,
        driver,
        tmp_path,
        "E_ABI_BOUNDS",
    )


def test_p2p_transfer_identity_is_nonzero(
    dma_program, driver, tmp_path
):
    arch, program = dma_program
    changed = _with_dma_attrs(
        program, DmaKind.P2P_PUSH, transfer_id=0
    )
    descriptors = list(changed.dma_descriptors)
    index = next(
        index
        for index, descriptor in enumerate(descriptors)
        if descriptor.kind == int(DmaKind.P2P_PUSH)
    )
    descriptors[index] = replace(descriptors[index], transfer_id=0)
    _assert_parity(
        _rehash(replace(changed, dma_descriptors=tuple(descriptors))),
        arch,
        driver,
        tmp_path,
        "E_P2P_UNMATCHED",
    )


@pytest.mark.parametrize("field", ("offset_bytes", "shard_id", "tensor_id"))
def test_dma_transport_endpoint_is_exact_semantic_projection(
    dma_program, driver, tmp_path, field
):
    arch, program = dma_program
    descriptors = list(program.dma_descriptors)
    endpoint = descriptors[0].src
    value = endpoint.offset_bytes + 1 if field == "offset_bytes" else 9999
    descriptors[0] = replace(
        descriptors[0], src=replace(endpoint, **{field: value})
    )
    changed = replace(program, dma_descriptors=tuple(descriptors))
    _assert_parity(
        _rehash(changed), arch, driver, tmp_path, "E_DMA_RANGE"
    )
