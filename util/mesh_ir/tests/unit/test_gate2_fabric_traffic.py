import copy
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from mesh_ir.architecture import SyntheticHbmConfig, architecture_document, load_arch, load_arch_text, validate_arch
from mesh_ir.canonical import canonical_json_bytes, semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.experiment.config import Topology
from mesh_ir.experiment.workload import resolve_experiment_architecture
from mesh_ir.ir.common import Access, DmaKind, INVALID_CORE_ID, MemorySpace, TensorRole
from mesh_ir.traffic import (
    AddressRef,
    AxiChannel,
    Binding,
    BindingSlot,
    DescriptorExecution,
    DescriptorIdentity,
    DirectAddress,
    ResolvedDescriptor,
    SlotAddress,
    TrafficAggregateLevel,
    TrafficDirection,
    calculate_descriptor_traffic,
    calculate_traffic,
    resolve_addresses,
    verify_traffic_report,
)


ROOT = Path(__file__).resolve().parents[4]
ARCH_PATH = ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


@pytest.fixture
def arch():
    return load_arch(ARCH_PATH)


@pytest.fixture
def load_descriptor(arch):
    slot = BindingSlot(
        slot_id=1,
        symbol="input",
        memory_space=MemorySpace.HBM,
        region_id=0,
        owner_core=INVALID_CORE_ID,
        required_allocation_bytes=64,
        required_allocation_alignment_bytes=32,
        access=Access.READ_ONLY,
        reference_binding=Binding(1, 0, INVALID_CORE_ID, 0, 64, 32, Access.READ_ONLY),
    )
    src = AddressRef(
        1,
        MemorySpace.HBM,
        0,
        INVALID_CORE_ID,
        SlotAddress(1),
        0,
        64,
        64,
        32,
        Access.READ_ONLY,
    )
    dst = AddressRef(
        2,
        MemorySpace.CORE_SRAM,
        2,
        0,
        DirectAddress(0, 0, 64, 32, Access.READ_WRITE),
        0,
        64,
        64,
        32,
        Access.READ_WRITE,
    )
    resolved = resolve_addresses(
        arch,
        (slot,),
        (slot.reference_binding,),
        (src, dst),
        issuing_core=0,
    )
    return ResolvedDescriptor(
        DescriptorIdentity(
            1,
            2,
            3,
            4,
            0,
            INVALID_CORE_ID,
            5,
            TensorRole.INPUT,
            TrafficDirection.READ,
        ),
        DmaKind.LOAD,
        resolved.addresses[0],
        resolved.addresses[1],
        1,
        64,
        64,
        64,
        64,
        64,
        16,
    )


def execute(descriptor, count=1):
    return DescriptorExecution(descriptor, count)


def external_descriptor_at(arch, template, kind, offset):
    access = Access.READ_ONLY if kind == DmaKind.LOAD else Access.READ_WRITE
    slot = BindingSlot(
        100,
        "repeated",
        MemorySpace.HBM,
        0,
        INVALID_CORE_ID,
        64,
        32,
        access,
        Binding(100, 0, INVALID_CORE_ID, 0, 64, 32, access),
    )
    remote = AddressRef(
        100,
        MemorySpace.HBM,
        0,
        INVALID_CORE_ID,
        SlotAddress(100),
        0,
        64,
        64,
        32,
        access,
    )
    local_access = Access.READ_WRITE if kind == DmaKind.LOAD else Access.READ_ONLY
    local = AddressRef(
        101,
        MemorySpace.CORE_SRAM,
        2,
        0,
        DirectAddress(0, 0, 64, 32, local_access),
        0,
        64,
        64,
        32,
        local_access,
    )
    resolved = resolve_addresses(
        arch,
        (slot,),
        (Binding(100, 0, INVALID_CORE_ID, offset, 64, 32, access),),
        (remote, local),
        issuing_core=0,
    )
    remote_address, local_address = resolved.addresses
    if kind == DmaKind.LOAD:
        return replace(template, src=remote_address, dst=local_address)
    return replace(
        template,
        identity=replace(
            template.identity,
            tensor_role=TensorRole.OUTPUT,
            direction=TrafficDirection.WRITE,
        ),
        kind=DmaKind.STORE,
        src=local_address,
        dst=remote_address,
    )


def test_fabric_manifest_resolves_runtime_contract_and_sparse_geometry():
    manifest = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    assert manifest.fabric.channel_order == ("AW", "W", "B", "AR", "R")
    assert manifest.fabric.axi.w_beats_per_cycle == 1
    assert manifest.fabric.axi.wire_header_bytes == (24, 16, 8, 24, 16)
    assert manifest.fabric.network.router_input_depths == (4, 8, 4, 4, 8)
    assert manifest.fabric.network.ni_receive_depths == (4, 8, 4, 4, 8)
    assert [(item.core_id, item.router_id) for item in manifest.fabric.initiators] == [
        (7, 0),
        (2, 1),
        (9, 2),
        (13, 3),
    ]
    assert any(
        target.router_id == manifest.fabric.initiators[0].router_id
        for target in manifest.fabric.targets
    )


def test_synthetic_hbm_fields_round_trip_and_change_architecture_identity():
    base = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    profile = json.loads((ROOT / "configs/example/ai_mesh/experiments/fixed_profile.json").read_text())
    arch = resolve_experiment_architecture(base, Topology("H5"), profile)
    target = arch.fabric.targets[0]
    changed = replace(
        arch,
        fabric=replace(
            arch.fabric,
            axi=replace(arch.fabric.axi, w_beats_per_cycle=2),
            targets=(replace(target, synthetic_hbm=SyntheticHbmConfig(64, 8)),)
            + arch.fabric.targets[1:],
        ),
    )
    document = architecture_document(changed)
    loaded = load_arch_text(yaml.safe_dump(document, sort_keys=False))

    assert loaded == changed
    assert loaded.digest() != arch.digest()


@pytest.mark.parametrize("field", ("w_beats_per_cycle", "bytes_per_cycle", "queue_depth"))
def test_each_experiment_service_field_changes_architecture_identity(field):
    base = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    profile = json.loads((ROOT / "configs/example/ai_mesh/experiments/fixed_profile.json").read_text())
    arch = resolve_experiment_architecture(base, Topology("H5"), profile)
    if field == "w_beats_per_cycle":
        fabric = replace(arch.fabric, axi=replace(arch.fabric.axi, w_beats_per_cycle=2))
    else:
        target = arch.fabric.targets[0]
        synthetic = replace(
            target.synthetic_hbm,
            **{field: getattr(target.synthetic_hbm, field) + 1},
        )
        fabric = replace(
            arch.fabric,
            targets=(replace(target, synthetic_hbm=synthetic),) + arch.fabric.targets[1:],
        )
    assert replace(arch, fabric=fabric).digest() != arch.digest()


@pytest.mark.parametrize("field", ("bytes_per_cycle", "queue_depth"))
@pytest.mark.parametrize("value", (0, -1, True, 2**32))
def test_synthetic_hbm_rejects_nonpositive_or_non_u32_fields(arch, field, value):
    target_index = 1
    target = replace(
        arch.fabric.targets[target_index],
        synthetic_hbm=replace(SyntheticHbmConfig(32, 64), **{field: value}),
    )
    changed = replace(
        arch,
        fabric=replace(
            arch.fabric,
            targets=arch.fabric.targets[:target_index] + (target,) + arch.fabric.targets[target_index + 1:],
        ),
    )
    with pytest.raises(MeshIrError) as error:
        validate_arch(changed)
    assert error.value.code == "E_CONFIG"


@pytest.mark.parametrize("value", (0, -1, True, 2**32))
def test_fabric_rejects_nonpositive_or_non_u32_w_rate(arch, value):
    changed = replace(
        arch,
        fabric=replace(
            arch.fabric,
            axi=replace(arch.fabric.axi, w_beats_per_cycle=value),
        ),
    )
    with pytest.raises(MeshIrError) as error:
        validate_arch(changed)
    assert error.value.code == "E_CONFIG"


def test_default_error_target_rejects_synthetic_hbm(arch):
    target = arch.fabric.targets[0]
    changed = replace(
        arch,
        fabric=replace(
            arch.fabric,
            targets=(replace(target, synthetic_hbm=SyntheticHbmConfig(32, 64)),)
            + arch.fabric.targets[1:],
        ),
    )
    with pytest.raises(MeshIrError) as error:
        validate_arch(changed)
    assert error.value.code == "E_CONFIG"


@pytest.mark.parametrize("value", ({"bytes_per_cycle": 32, "queue_depth": 64}, (32, 64), "hbm"))
def test_public_architecture_rejects_untyped_synthetic_hbm(arch, value):
    target = replace(arch.fabric.targets[1], synthetic_hbm=value)
    changed = replace(
        arch,
        fabric=replace(
            arch.fabric,
            targets=(arch.fabric.targets[0], target) + arch.fabric.targets[2:],
        ),
    )
    with pytest.raises(MeshIrError) as error:
        validate_arch(changed)
    assert error.value.code == "E_CONFIG"


@pytest.mark.parametrize(
    "old,new",
    [
        ("router_input_depths: [4, 8, 4, 4, 8]", "router_input_depths: [4, 8, 0, 4, 8]"),
        ("flit_bytes: 16", "flit_bytes: 0"),
        ("vcs_per_vnet: 4", "vcs_per_vnet: .inf"),
        ("src_port: 0", "src_port: 256"),
    ],
)
def test_fabric_schema_rejects_invalid_finite_or_uid_fields(old, new):
    with pytest.raises(MeshIrError) as error:
        load_arch_text(ARCH_PATH.read_text().replace(old, new, 1))
    assert error.value.code == "E_CONFIG"


def test_fabric_validation_rejects_b_rob_below_write_outstanding(arch):
    changed = copy.deepcopy(arch)
    changed.fabric = replace(
        changed.fabric,
        initiator=replace(
            changed.fabric.initiator,
            b_reorder_transactions=changed.fabric.initiator.max_outstanding_writes - 1,
        ),
    )
    with pytest.raises(MeshIrError, match="reorder"):
        validate_arch(changed)


def test_fabric_validation_rejects_incomplete_quota_and_target_coverage(arch):
    for fabric in (
        replace(arch.fabric, quotas=arch.fabric.quotas[:-1]),
        replace(
            arch.fabric,
            targets=(
                replace(
                    arch.fabric.targets[0],
                    ranges=(replace(arch.fabric.targets[0].ranges[0], size_bytes=32),) + arch.fabric.targets[0].ranges[1:],
                ),
            ) + arch.fabric.targets[1:],
        ),
        replace(
            arch.fabric,
            initiators=(
                arch.fabric.initiators[0],
                replace(arch.fabric.initiators[1], name=arch.fabric.initiators[0].name),
            ),
        ),
    ):
        changed = copy.deepcopy(arch)
        changed.fabric = fabric
        with pytest.raises(MeshIrError):
            validate_arch(changed)


def test_dual_lane_rejects_any_multi_flit_channel(arch):
    changed = copy.deepcopy(arch)
    changed.fabric = replace(
        arch.fabric,
        network=replace(arch.fabric.network, dual_lane=True),
    )
    with pytest.raises(MeshIrError, match="single-flit"):
        validate_arch(changed)


def test_fabric_digest_covers_each_new_field_family(arch):
    variants = (
        replace(arch.fabric, axi=replace(arch.fabric.axi, data_header_sideband=True)),
        replace(arch.fabric, target=replace(arch.fabric.target, service_queue_depths=(33, 32))),
        replace(arch.fabric, network=replace(arch.fabric.network, link_latency_cycles=2)),
        replace(arch.fabric, initiators=(replace(arch.fabric.initiators[0], default_target_node=1001),) + arch.fabric.initiators[1:]),
        replace(arch.fabric, targets=(replace(arch.fabric.targets[0], router_id=1),) + arch.fabric.targets[1:]),
        replace(arch.fabric, quotas=(replace(arch.fabric.quotas[0], read_beats=2),) + arch.fabric.quotas[1:]),
    )
    for fabric in variants:
        changed = copy.deepcopy(arch)
        changed.fabric = fabric
        assert changed.digest() != arch.digest()


@pytest.mark.parametrize(
    "sideband,expected_wire_bytes,expected_flits",
    [
        (False, (0, 0, 0, 24, 96), (0, 0, 0, 2, 6)),
        (True, (0, 0, 0, 24, 64), (0, 0, 0, 2, 4)),
    ],
)
def test_read_traffic_exact_channels_and_reverse_route(
    arch, load_descriptor, sideband, expected_wire_bytes, expected_flits
):
    arch.fabric = replace(
        arch.fabric,
        axi=replace(arch.fabric.axi, data_header_sideband=sideband),
    )
    row = calculate_descriptor_traffic(arch, execute(load_descriptor))
    assert tuple(channel.channel for channel in row.channels) == tuple(AxiChannel)
    assert tuple(channel.wire_bytes for channel in row.channels) == expected_wire_bytes
    assert tuple(channel.flits for channel in row.channels) == expected_flits
    ar = row.channels[AxiChannel.AR]
    r = row.channels[AxiChannel.R]
    assert (ar.source_endpoint, ar.destination_endpoint) == ("core_0", "memory")
    assert (r.source_endpoint, r.destination_endpoint) == ("memory", "core_0")
    assert row.useful_bytes == 64
    assert row.physical_beat_bytes == 64
    assert row.bursts == 1


def test_write_one_byte_counts_full_width_sparse_w_message(arch, load_descriptor):
    src_ref = AddressRef(
        10,
        MemorySpace.CORE_SRAM,
        2,
        0,
        DirectAddress(0, 0, 32, 32, Access.READ_ONLY),
        0,
        1,
        32,
        1,
        Access.READ_ONLY,
    )
    dst_ref = AddressRef(
        11,
        MemorySpace.HBM,
        0,
        INVALID_CORE_ID,
        DirectAddress(0, 0, 32, 32, Access.READ_WRITE),
        0,
        1,
        32,
        1,
        Access.READ_WRITE,
    )
    resolved = resolve_addresses(arch, (), (), (src_ref, dst_ref), issuing_core=0)
    descriptor = replace(
        load_descriptor,
        identity=replace(
            load_descriptor.identity,
            tensor_role=TensorRole.OUTPUT,
            direction=TrafficDirection.WRITE,
        ),
        kind=DmaKind.STORE,
        src=resolved.addresses[0],
        dst=resolved.addresses[1],
        row_bytes=1,
        useful_bytes=1,
        physical_storage_bytes=32,
    )
    row = calculate_descriptor_traffic(arch, execute(descriptor))
    assert tuple(channel.messages for channel in row.channels) == (1, 1, 1, 0, 0)
    assert tuple(channel.wire_bytes for channel in row.channels) == (24, 48, 8, 0, 0)
    assert row.useful_bytes == 1
    assert row.physical_beat_bytes == 32


def test_local_fill_emits_no_network_messages(arch, load_descriptor):
    descriptor = replace(
        load_descriptor,
        identity=replace(
            load_descriptor.identity,
            peer_core=INVALID_CORE_ID,
            direction=TrafficDirection.LOCAL,
        ),
        kind=DmaKind.LOCAL_FILL,
        src=load_descriptor.dst,
        dst=load_descriptor.dst,
    )
    row = calculate_descriptor_traffic(arch, execute(descriptor))
    assert row.bursts == 0
    assert row.physical_beat_bytes == 0
    assert all(channel.messages == channel.flits == channel.wire_bytes == 0 for channel in row.channels)


def test_report_has_complete_aggregate_levels_and_independent_verification(arch, load_descriptor):
    report = calculate_traffic(arch, "a" * 64, (execute(load_descriptor),))
    assert {row.key.level for row in report.aggregates} == {
        TrafficAggregateLevel.PROGRAM,
        TrafficAggregateLevel.ENTRYPOINT,
        TrafficAggregateLevel.PROFILE,
        TrafficAggregateLevel.DETAIL,
    }
    verify_traffic_report(arch, "a" * 64, (execute(load_descriptor),), report)
    forged = replace(report, descriptors=(replace(report.descriptors[0], useful_bytes=65),))
    with pytest.raises(MeshIrError) as error:
        verify_traffic_report(arch, "a" * 64, (execute(load_descriptor),), forged)
    assert error.value.code == "E_TRAFFIC_MISMATCH"


def test_traffic_rejects_forged_resolved_endpoint(arch, load_descriptor):
    forged = replace(load_descriptor.src, target_router=99)
    with pytest.raises(MeshIrError) as error:
        calculate_descriptor_traffic(arch, execute(replace(load_descriptor, src=forged)))
    assert error.value.code == "E_TRAFFIC_MISMATCH"


def test_traffic_rejects_untyped_or_out_of_range_resolved_address_origins(arch, load_descriptor):
    cases = (
        replace(load_descriptor, src=replace(load_descriptor.src, ref_id=0)),
        replace(load_descriptor, src=replace(load_descriptor.src, ref_id=1 << 40)),
        replace(load_descriptor, src=replace(load_descriptor.src, origin=SlotAddress(1 << 40))),
        replace(
            load_descriptor,
            dst=replace(
                load_descriptor.dst,
                origin=replace(load_descriptor.dst.origin, backing_access=int(Access.READ_WRITE)),
            ),
        ),
        replace(
            load_descriptor,
            dst=replace(
                load_descriptor.dst,
                origin=replace(load_descriptor.dst.origin, backing_offset_bytes=False),
            ),
        ),
    )
    for descriptor in cases:
        with pytest.raises(MeshIrError) as error:
            calculate_descriptor_traffic(arch, execute(descriptor))
        assert error.value.code == "E_TRAFFIC_MISMATCH"


def test_p2p_routes_issuing_initiator_to_peer_target_on_multiple_hops(arch, load_descriptor):
    src_ref = AddressRef(
        20,
        MemorySpace.CORE_SRAM,
        2,
        0,
        DirectAddress(0, 0, 64, 32, Access.READ_ONLY),
        0,
        64,
        64,
        32,
        Access.READ_ONLY,
    )
    dst_ref = AddressRef(
        21,
        MemorySpace.PEER_SRAM,
        2,
        1,
        DirectAddress(0, 0, 64, 32, Access.READ_WRITE),
        0,
        64,
        64,
        32,
        Access.READ_WRITE,
    )
    resolved = resolve_addresses(arch, (), (), (src_ref, dst_ref), issuing_core=0)
    descriptor = replace(
        load_descriptor,
        identity=replace(load_descriptor.identity, peer_core=1, direction=TrafficDirection.P2P),
        kind=DmaKind.P2P_PUSH,
        src=resolved.addresses[0],
        dst=resolved.addresses[1],
    )
    row = calculate_descriptor_traffic(arch, execute(descriptor))
    assert row.hops == 1
    assert row.channels[AxiChannel.AW].source_endpoint == "core_0"
    assert row.channels[AxiChannel.AW].destination_endpoint == "peer_1"
    assert row.channels[AxiChannel.B].source_endpoint == "peer_1"
    assert row.channels[AxiChannel.B].destination_endpoint == "core_0"


def test_equal_useful_bytes_can_have_different_physical_beat_counts(arch, load_descriptor):
    plans = []
    for ref_id, offset, backing_size in ((30, 0, 32), (31, 31, 64)):
        src_ref = AddressRef(
            ref_id,
            MemorySpace.HBM,
            0,
            INVALID_CORE_ID,
            DirectAddress(offset, 0, backing_size, 32, Access.READ_ONLY),
            0,
            2,
            2,
            1,
            Access.READ_ONLY,
        )
        dst_ref = AddressRef(
            ref_id + 10,
            MemorySpace.CORE_SRAM,
            2,
            0,
            DirectAddress(0, 0, backing_size, 32, Access.READ_WRITE),
            0,
            2,
            2,
            1,
            Access.READ_WRITE,
        )
        resolved = resolve_addresses(arch, (), (), (src_ref, dst_ref), issuing_core=0)
        descriptor = replace(
            load_descriptor,
            identity=replace(load_descriptor.identity, descriptor_id=ref_id),
            src=resolved.addresses[0],
            dst=resolved.addresses[1],
            row_bytes=2,
            useful_bytes=2,
            physical_storage_bytes=backing_size,
        )
        plans.append(calculate_descriptor_traffic(arch, execute(descriptor)))
    assert [row.useful_bytes for row in plans] == [2, 2]
    assert [row.physical_beat_bytes for row in plans] == [32, 64]


def test_strided_rows_are_independently_packetized_in_order(arch, load_descriptor):
    refs = tuple(
        AddressRef(
            ref_id,
            memory_space,
            region_id,
            owner,
            DirectAddress(0, 0, 64, 32, access),
            0,
            34,
            64,
            1,
            access,
        )
        for ref_id, memory_space, region_id, owner, access in (
            (50, MemorySpace.HBM, 0, INVALID_CORE_ID, Access.READ_ONLY),
            (51, MemorySpace.CORE_SRAM, 2, 0, Access.READ_WRITE),
        )
    )
    resolved = resolve_addresses(arch, (), (), refs, issuing_core=0)
    descriptor = replace(
        load_descriptor,
        src=resolved.addresses[0],
        dst=resolved.addresses[1],
        rows=2,
        row_bytes=1,
        src_stride_bytes=33,
        dst_stride_bytes=33,
        useful_bytes=2,
        physical_storage_bytes=64,
    )
    row = calculate_descriptor_traffic(arch, execute(descriptor))
    assert (row.segments, row.bursts, row.physical_beat_bytes) == (2, 2, 64)
    assert row.channels[AxiChannel.R].messages == 2


@pytest.mark.parametrize(
    "change",
    [
        lambda descriptor: replace(descriptor, useful_bytes=63),
        lambda descriptor: replace(descriptor, physical_storage_bytes=65),
        lambda descriptor: replace(descriptor, kind=DmaKind.STORE),
        lambda descriptor: replace(descriptor, identity=replace(descriptor.identity, issuing_core=1)),
        lambda descriptor: replace(descriptor, src=replace(descriptor.src, logical_span_bytes=63)),
        lambda descriptor: replace(descriptor, identity=replace(descriptor.identity, descriptor_id=0)),
        lambda descriptor: replace(descriptor, dst=None),
        lambda descriptor: replace(descriptor, rows=2, row_bytes=32, src_stride_bytes=31, dst_stride_bytes=31, useful_bytes=64),
        lambda descriptor: replace(descriptor, max_burst_beats=17),
    ],
)
def test_descriptor_contradictions_fail_closed(arch, load_descriptor, change):
    with pytest.raises(MeshIrError) as error:
        calculate_descriptor_traffic(arch, execute(change(load_descriptor)))
    assert error.value.code == "E_TRAFFIC_MISMATCH"


def test_dma_write_destinations_require_genuine_writable_backing(arch, load_descriptor):
    local_read_only = AddressRef(
        60,
        MemorySpace.CORE_SRAM,
        2,
        0,
        DirectAddress(0, 0, 64, 32, Access.READ_ONLY),
        0,
        64,
        64,
        32,
        Access.READ_ONLY,
    )
    external_read_only = AddressRef(
        61,
        MemorySpace.HBM,
        0,
        INVALID_CORE_ID,
        DirectAddress(0, 0, 64, 32, Access.READ_ONLY),
        0,
        64,
        64,
        32,
        Access.READ_ONLY,
    )
    resolved = resolve_addresses(
        arch,
        (),
        (),
        (local_read_only, external_read_only),
        issuing_core=0,
    )
    invalid_load = replace(load_descriptor, dst=resolved.addresses[0])
    invalid_store = replace(
        load_descriptor,
        identity=replace(load_descriptor.identity, direction=TrafficDirection.WRITE),
        kind=DmaKind.STORE,
        src=load_descriptor.dst,
        dst=resolved.addresses[1],
    )
    for descriptor in (invalid_load, invalid_store):
        with pytest.raises(MeshIrError) as error:
            calculate_descriptor_traffic(arch, execute(descriptor))
        assert error.value.code == "E_TRAFFIC_MISMATCH"


def test_report_verifier_rejects_boolean_counter_even_with_dataclass_equality(arch, load_descriptor):
    report = calculate_traffic(arch, "b" * 64, (execute(load_descriptor),))
    forged = replace(
        report,
        descriptors=(replace(report.descriptors[0], bursts=True),),
    )
    with pytest.raises(MeshIrError) as error:
        verify_traffic_report(arch, "b" * 64, (execute(load_descriptor),), forged)
    assert error.value.code == "E_TRAFFIC_MISMATCH"


def test_report_verifier_rejects_malformed_nested_aggregate_with_typed_diagnostic(arch, load_descriptor):
    report = calculate_traffic(arch, "b" * 64, (execute(load_descriptor),))
    forged_aggregates = (
        replace(report.aggregates[0], key=None),
        replace(report.aggregates[0], static_descriptors=True),
        replace(report.aggregates[0], descriptor_executions=True),
    )
    for forged_aggregate in forged_aggregates:
        forged = replace(
            report,
            aggregates=(forged_aggregate,) + report.aggregates[1:],
        )
        with pytest.raises(MeshIrError) as error:
            verify_traffic_report(arch, "b" * 64, (execute(load_descriptor),), forged)
        assert error.value.code == "E_TRAFFIC_MISMATCH"


def test_report_verifier_rejects_canonically_equivalent_untyped_nested_rows(arch, load_descriptor):
    report = calculate_traffic(arch, "b" * 64, (execute(load_descriptor),))
    row = report.descriptors[0]
    forged_rows = (
        replace(row, execution_count=True),
        replace(row, channels=list(row.channels)),
        replace(
            row,
            channels=(replace(row.channels[0], channel=0),) + row.channels[1:],
        ),
        replace(row, identity=row.identity.__dict__),
    )
    for forged_row in forged_rows:
        forged = replace(report, descriptors=(forged_row,))
        with pytest.raises(MeshIrError) as error:
            verify_traffic_report(arch, "b" * 64, (execute(load_descriptor),), forged)
        assert error.value.code == "E_TRAFFIC_MISMATCH"


def test_descriptor_stable_id_widths_are_exact(arch, load_descriptor):
    forged = replace(
        load_descriptor,
        identity=replace(load_descriptor.identity, descriptor_id=0x100000000),
    )
    with pytest.raises(MeshIrError) as error:
        calculate_descriptor_traffic(arch, execute(forged))
    assert error.value.code == "E_TRAFFIC_MISMATCH"


def test_report_descriptor_order_is_canonical(arch, load_descriptor):
    second = replace(
        load_descriptor,
        identity=replace(load_descriptor.identity, command_id=4, descriptor_id=5),
    )
    forward = calculate_traffic(arch, "c" * 64, (execute(load_descriptor), execute(second)))
    reversed_input = calculate_traffic(arch, "c" * 64, (execute(second), execute(load_descriptor)))
    assert forward == reversed_input
    assert [row.identity.descriptor_id for row in forward.descriptors] == [4, 5]


@pytest.mark.parametrize(
    "change",
    [
        lambda descriptor: replace(descriptor, dst=None),
        lambda descriptor: replace(descriptor, identity=replace(descriptor.identity, descriptor_id=0)),
    ],
)
def test_malformed_descriptor_nested_records_fail_with_typed_diagnostic(arch, load_descriptor, change):
    with pytest.raises(MeshIrError) as error:
        calculate_descriptor_traffic(arch, execute(change(load_descriptor)))
    assert error.value.code == "E_TRAFFIC_MISMATCH"


def test_load_rejects_peer_sram_source_and_external_peer_identity(arch, load_descriptor):
    peer_ref = AddressRef(
        70,
        MemorySpace.PEER_SRAM,
        2,
        1,
        DirectAddress(0, 0, 64, 32, Access.READ_ONLY),
        0,
        64,
        64,
        32,
        Access.READ_ONLY,
    )
    peer = resolve_addresses(arch, (), (), (peer_ref,), issuing_core=0).addresses[0]
    cases = (
        replace(load_descriptor, src=peer, identity=replace(load_descriptor.identity, peer_core=1)),
        replace(load_descriptor, identity=replace(load_descriptor.identity, peer_core=1)),
    )
    for descriptor in cases:
        with pytest.raises(MeshIrError) as error:
            calculate_descriptor_traffic(arch, execute(descriptor))
        assert error.value.code == "E_TRAFFIC_MISMATCH"


def test_report_canonical_payload_omits_unused_aggregate_dimensions(arch, load_descriptor):
    report = calculate_traffic(arch, "d" * 64, (execute(load_descriptor),))
    payload = canonical_json_bytes(report.canonical_dict())
    assert b'"level":"PROGRAM"' in payload
    assert b"null" not in payload


def test_empty_traffic_report_still_validates_architecture(arch):
    changed = copy.deepcopy(arch)
    changed.fabric = replace(
        arch.fabric,
        initiator=replace(arch.fabric.initiator, b_reorder_transactions=1),
    )
    with pytest.raises(MeshIrError):
        calculate_traffic(changed, "e" * 64, ())


def test_zero_row_and_zero_width_descriptors_remain_distinct(arch, load_descriptor):
    zero_rows = replace(
        load_descriptor,
        rows=0,
        useful_bytes=0,
        physical_storage_bytes=0,
    )
    zero_width = replace(
        load_descriptor,
        row_bytes=0,
        useful_bytes=0,
        physical_storage_bytes=0,
    )
    first = calculate_traffic(arch, "f" * 64, (execute(zero_rows),))
    second = calculate_traffic(arch, "f" * 64, (execute(zero_width),))
    assert first.semantic_sha256 != second.semantic_sha256
    assert all(channel.messages == 0 for channel in first.descriptors[0].channels)
    assert all(channel.messages == 0 for channel in second.descriptors[0].channels)


@pytest.mark.parametrize(
    "kind,offset,expected_bursts,expected_messages",
    [
        (DmaKind.LOAD, 0, 3, (0, 0, 0, 3, 6)),
        (DmaKind.LOAD, 4064, 6, (0, 0, 0, 6, 6)),
        (DmaKind.STORE, 0, 3, (3, 6, 3, 0, 0)),
        (DmaKind.STORE, 4064, 6, (6, 6, 6, 0, 0)),
    ],
)
def test_repeated_external_traffic_has_static_geometry_and_scaled_totals(
    arch, load_descriptor, kind, offset, expected_bursts, expected_messages
):
    descriptor = external_descriptor_at(arch, load_descriptor, kind, offset)
    row = calculate_descriptor_traffic(arch, execute(descriptor, 3))
    assert row.execution_count == 3
    assert row.rows == 1
    assert row.row_bytes == 64
    assert row.useful_bytes == 192
    assert row.physical_beat_bytes == 192
    assert row.segments == 3
    assert row.bursts == expected_bursts
    assert tuple(channel.messages for channel in row.channels) == expected_messages


def test_repeated_strided_rows_restart_static_addresses_each_execution(arch, load_descriptor):
    refs = tuple(
        AddressRef(
            ref_id,
            memory_space,
            region_id,
            owner,
            DirectAddress(0, 0, 64, 32, access),
            0,
            34,
            64,
            1,
            access,
        )
        for ref_id, memory_space, region_id, owner, access in (
            (150, MemorySpace.HBM, 0, INVALID_CORE_ID, Access.READ_ONLY),
            (151, MemorySpace.CORE_SRAM, 2, 0, Access.READ_WRITE),
        )
    )
    resolved = resolve_addresses(arch, (), (), refs, issuing_core=0)
    descriptor = replace(
        load_descriptor,
        src=resolved.addresses[0],
        dst=resolved.addresses[1],
        rows=2,
        row_bytes=1,
        src_stride_bytes=33,
        dst_stride_bytes=33,
        useful_bytes=2,
        physical_storage_bytes=64,
    )
    row = calculate_descriptor_traffic(arch, execute(descriptor, 3))
    assert (row.rows, row.remote_address, row.remote_stride_bytes) == (
        2,
        descriptor.src.address,
        33,
    )
    assert (row.useful_bytes, row.physical_beat_bytes, row.segments, row.bursts) == (
        6,
        192,
        6,
        6,
    )
    assert row.channels[AxiChannel.AR].messages == 6
    assert row.channels[AxiChannel.R].messages == 6


def test_repeated_per_message_flit_rounding_scales_after_rounding(arch, load_descriptor):
    row = calculate_descriptor_traffic(arch, execute(load_descriptor, 3))
    assert tuple(channel.wire_bytes for channel in row.channels) == (0, 0, 0, 72, 288)
    assert tuple(channel.flits for channel in row.channels) == (0, 0, 0, 6, 18)


def test_repeated_local_fill_and_zero_descriptor_preserve_planned_count(arch, load_descriptor):
    fill = replace(
        load_descriptor,
        identity=replace(load_descriptor.identity, direction=TrafficDirection.LOCAL),
        kind=DmaKind.LOCAL_FILL,
        src=load_descriptor.dst,
        dst=load_descriptor.dst,
    )
    zero = replace(load_descriptor, rows=0, useful_bytes=0, physical_storage_bytes=0)
    fill_row = calculate_descriptor_traffic(arch, execute(fill, 3))
    zero_row = calculate_descriptor_traffic(arch, execute(zero, 3))
    assert (fill_row.execution_count, fill_row.useful_bytes) == (3, 192)
    assert (zero_row.execution_count, zero_row.useful_bytes) == (3, 0)
    for row in (fill_row, zero_row):
        assert row.physical_beat_bytes == row.segments == row.bursts == 0
        assert all(channel.messages == channel.packets == channel.flits == channel.wire_bytes == 0 for channel in row.channels)


def test_repeated_aggregate_counts_static_descriptors_and_executions_at_every_level(arch, load_descriptor):
    second = replace(
        load_descriptor,
        identity=replace(load_descriptor.identity, command_id=4, descriptor_id=5),
    )
    report = calculate_traffic(
        arch,
        "9" * 64,
        (execute(load_descriptor, 2), execute(second, 3)),
    )
    assert len(report.descriptors) == 2
    assert {row.execution_count for row in report.descriptors} == {2, 3}
    for aggregate in report.aggregates:
        assert aggregate.static_descriptors == 2
        assert aggregate.descriptor_executions == 5
        assert (aggregate.useful_bytes, aggregate.physical_beat_bytes) == (320, 320)
        assert (aggregate.segments, aggregate.bursts) == (5, 5)
        assert tuple(channel.messages for channel in aggregate.channels) == (0, 0, 0, 5, 10)
    verify_traffic_report(
        arch,
        "9" * 64,
        (execute(second, 3), execute(load_descriptor, 2)),
        report,
    )


@pytest.mark.parametrize("count", [True, 1.0, 0, -1, 0x100000000])
def test_descriptor_execution_count_requires_exact_positive_u32(arch, load_descriptor, count):
    with pytest.raises(MeshIrError) as error:
        calculate_descriptor_traffic(arch, execute(load_descriptor, count))
    assert error.value.code == "E_TRAFFIC_MISMATCH"


def test_repeated_traffic_rejects_duplicate_static_identity(arch, load_descriptor):
    with pytest.raises(MeshIrError) as error:
        calculate_traffic(
            arch,
            "8" * 64,
            (execute(load_descriptor, 1), execute(load_descriptor, 2)),
        )
    assert error.value.code == "E_TRAFFIC_MISMATCH"


def test_repeated_traffic_uses_checked_u64_multiplication(arch, load_descriptor):
    changed = copy.deepcopy(arch)
    changed.fabric = replace(
        changed.fabric,
        network=replace(changed.fabric.network, router_cols=0xFFFFFFFF),
        targets=(
            replace(changed.fabric.targets[0], router_id=0xFFFFFFFE),
        )
        + changed.fabric.targets[1:],
    )
    descriptor = external_descriptor_at(changed, load_descriptor, DmaKind.LOAD, 0)
    with pytest.raises(MeshIrError) as error:
        calculate_descriptor_traffic(changed, execute(descriptor, 0xFFFFFFFF))
    assert error.value.code == "E_ABI_OVERFLOW"


def test_repeated_report_rejects_fresh_checksum_forged_count_and_total(arch, load_descriptor):
    executions = (execute(load_descriptor, 3),)
    report = calculate_traffic(arch, "7" * 64, executions)
    forged = replace(
        report,
        descriptors=(
            replace(report.descriptors[0], execution_count=2, useful_bytes=128),
        ),
    )
    payload = forged.canonical_dict()
    payload.pop("semantic_sha256")
    forged = replace(forged, semantic_sha256=semantic_sha256(payload))
    with pytest.raises(MeshIrError) as error:
        verify_traffic_report(arch, "7" * 64, executions, forged)
    assert error.value.code == "E_TRAFFIC_MISMATCH"


def test_repeated_calculation_does_not_import_torch(arch, load_descriptor):
    assert "torch" not in sys.modules
    report = calculate_traffic(arch, "6" * 64, (execute(load_descriptor, 3),))
    assert report.aggregates[0].descriptor_executions == 3
    assert "torch" not in sys.modules
