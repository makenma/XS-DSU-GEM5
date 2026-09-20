from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mesh_ir.canonical import U64_MAX, canonical_json_bytes
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, INVALID_CORE_ID
from mesh_ir.schema import load_yaml_mapping, validate_schema


@dataclass(frozen=True)
class ArchRegion:
    name: str
    kind: str
    base: int
    bytes: int
    tile_stride: int = 0
    tile_bytes: int = 0


@dataclass(frozen=True)
class AxiFabricConfig:
    wire_header_bytes: tuple[int, ...]
    data_header_sideband: bool
    w_beats_per_cycle: int


@dataclass(frozen=True)
class SyntheticHbmConfig:
    bytes_per_cycle: int
    queue_depth: int


@dataclass(frozen=True)
class InitiatorAdapterConfig:
    source_fifo_depths: tuple[int, ...]
    message_buffer_depths: tuple[int, ...]
    local_delivery_depths: tuple[int, ...]
    max_outstanding_reads: int
    max_outstanding_writes: int
    pre_aw_bursts: int
    pre_aw_beats: int
    b_reorder_transactions: int
    r_reorder_beats: int


@dataclass(frozen=True)
class TargetAdapterConfig:
    write_contexts: int
    write_assembly_beats: int
    read_contexts: int
    read_response_beats: int
    orphan_w_transactions: int
    orphan_w_beats: int
    service_queue_depths: tuple[int, ...]
    base_latency_cycles: tuple[int, ...]
    response_ready_depths: tuple[int, ...]


@dataclass(frozen=True)
class RouterInputDepth:
    router_id: int
    input_port: int
    vnet: int
    depth: int


@dataclass(frozen=True)
class NetworkConfig:
    router_rows: int
    router_cols: int
    flit_bytes: int
    link_latency_cycles: int
    router_latency_cycles: int
    vcs_per_vnet: int
    vnet_classes: tuple[str, ...]
    router_input_depths: tuple[int, ...]
    ni_receive_depths: tuple[int, ...]
    router_input_overrides: tuple[RouterInputDepth, ...]
    yx_vnets: tuple[int, ...]
    dual_lane: bool


@dataclass(frozen=True)
class InitiatorEndpoint:
    name: str
    core_id: int
    src_node: int
    src_port: int
    router_id: int
    default_target_node: int


@dataclass(frozen=True)
class TargetRange:
    region_id: int
    owner_core: int
    offset_bytes: int
    size_bytes: int
    access: Access


@dataclass(frozen=True)
class TargetEndpoint:
    name: str
    dst_node: int
    router_id: int
    ranges: tuple[TargetRange, ...]
    synthetic_hbm: SyntheticHbmConfig | None = None


@dataclass(frozen=True)
class SourceTargetQuota:
    src_node: int
    src_port: int
    dst_node: int
    write_contexts: int
    write_beats: int
    read_contexts: int
    read_beats: int


@dataclass(frozen=True)
class FabricConfig:
    channel_order: tuple[str, ...]
    axi: AxiFabricConfig
    initiator: InitiatorAdapterConfig
    target: TargetAdapterConfig
    network: NetworkConfig
    initiators: tuple[InitiatorEndpoint, ...]
    targets: tuple[TargetEndpoint, ...]
    default_error_target_node: int
    quotas: tuple[SourceTargetQuota, ...]


@dataclass
class ArchManifest:
    schema_version: str
    arch_name: str
    clock_hz: int
    core_ids: tuple[int, ...]
    mesh_rows: int
    mesh_cols: int
    mesh_routing: str
    mesh_endpoint_order: str
    command_rom_entries: int
    event_visibility_cycles: int
    decode_width: int
    admit_window: int
    sram_bytes: int
    sram_banks: int
    sram_read_ports_per_bank: int
    sram_write_ports_per_bank: int
    sram_bank_queue_depth: int
    sram_bank_interleave_bytes: int
    sram_read_bytes_per_cycle_per_bank: int
    sram_write_bytes_per_cycle_per_bank: int
    sram_base_alignment_bytes: int
    tensor_queue_depth: int
    tensor_setup_cycles: int
    tensor_pipeline_flush_cycles: int
    tensor_macs_per_cycle: dict[str, int]
    vector_queue_depth: int
    vector_setup_cycles: int
    vector_flush_cycles: int
    vector_elements_per_cycle: dict[str, int]
    reduce_queue_depth: int
    reduce_setup_cycles: int
    reduce_flush_cycles: int
    reduce_ops_per_cycle: dict[str, int]
    dma_read_engines: int
    dma_write_engines: int
    dma_descriptor_queue_depth: int
    dma_segment_queue_depth: int
    dma_read_outstanding: int
    dma_write_outstanding: int
    dma_setup_cycles: int
    dma_burst_base_latency: int
    axi_data_bytes: int
    axi_max_burst_beats: int
    axi_address_bits: int
    axi_id_bits: int
    axi_max_outstanding_per_id: int
    axi_enforce_4k_boundary: bool
    axi_qos_default: int
    regions: tuple[ArchRegion, ...]
    fabric: FabricConfig

    def region_by_id(self, region_id: int) -> ArchRegion:
        if type(region_id) is not int or region_id < 0:
            raise MeshIrError("E_CONFIG", "architecture region id is out of range", region_id=region_id)
        try:
            return self.regions[region_id]
        except IndexError as error:
            raise MeshIrError("E_CONFIG", "architecture region id is out of range", region_id=region_id) from error

    def core_id_at(self, y: int, x: int) -> int:
        if type(y) is not int or type(x) is not int or not 0 <= y < self.mesh_rows or not 0 <= x < self.mesh_cols:
            raise MeshIrError("E_CONFIG", "mesh coordinate is out of range", y=y, x=x)
        return self.core_ids[y * self.mesh_cols + x]

    def canonical_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def digest(self) -> bytes:
        import hashlib

        return hashlib.sha256(canonical_json_bytes(self.canonical_dict())).digest()


def _power_of_two(value: int) -> bool:
    return type(value) is int and value > 0 and value & (value - 1) == 0


def architecture_document(manifest: ArchManifest) -> dict[str, Any]:
    regions = []
    for region in manifest.regions:
        item = {"name": region.name, "kind": region.kind, "base": region.base, "bytes": region.bytes}
        if region.tile_stride:
            item["tile_stride"] = region.tile_stride
        if region.tile_bytes:
            item["tile_bytes"] = region.tile_bytes
        regions.append(item)
    return {
        "schema_version": manifest.schema_version,
        "arch_name": manifest.arch_name,
        "clock_hz": manifest.clock_hz,
        "mesh": {"rows": manifest.mesh_rows, "cols": manifest.mesh_cols, "routing": manifest.mesh_routing, "endpoint_order": manifest.mesh_endpoint_order, "core_ids": list(manifest.core_ids)},
        "core": {
            "command_rom_entries": manifest.command_rom_entries, "event_visibility_cycles": manifest.event_visibility_cycles, "decode_width": manifest.decode_width, "admit_window": manifest.admit_window,
            "sram": {"bytes": manifest.sram_bytes, "banks": manifest.sram_banks, "read_ports_per_bank": manifest.sram_read_ports_per_bank, "write_ports_per_bank": manifest.sram_write_ports_per_bank, "bank_queue_depth": manifest.sram_bank_queue_depth, "bank_interleave_bytes": manifest.sram_bank_interleave_bytes, "read_bytes_per_cycle_per_bank": manifest.sram_read_bytes_per_cycle_per_bank, "write_bytes_per_cycle_per_bank": manifest.sram_write_bytes_per_cycle_per_bank, "base_alignment_bytes": manifest.sram_base_alignment_bytes},
            "tensor_engine": {"queue_depth": manifest.tensor_queue_depth, "setup_cycles": manifest.tensor_setup_cycles, "pipeline_flush_cycles": manifest.tensor_pipeline_flush_cycles, "macs_per_cycle": manifest.tensor_macs_per_cycle},
            "vector_engine": {"queue_depth": manifest.vector_queue_depth, "setup_cycles": manifest.vector_setup_cycles, "flush_cycles": manifest.vector_flush_cycles, "elements_per_cycle": manifest.vector_elements_per_cycle},
            "reduce_engine": {"queue_depth": manifest.reduce_queue_depth, "setup_cycles": manifest.reduce_setup_cycles, "flush_cycles": manifest.reduce_flush_cycles, "ops_per_cycle": manifest.reduce_ops_per_cycle},
            "dma": {"read_engines": manifest.dma_read_engines, "write_engines": manifest.dma_write_engines, "descriptor_queue_depth": manifest.dma_descriptor_queue_depth, "segment_queue_depth": manifest.dma_segment_queue_depth, "read_outstanding": manifest.dma_read_outstanding, "write_outstanding": manifest.dma_write_outstanding, "setup_cycles": manifest.dma_setup_cycles, "burst_base_latency": manifest.dma_burst_base_latency},
        },
        "axi": {"data_bytes": manifest.axi_data_bytes, "max_burst_beats": manifest.axi_max_burst_beats, "address_bits": manifest.axi_address_bits, "id_bits": manifest.axi_id_bits, "max_outstanding_per_id": manifest.axi_max_outstanding_per_id, "enforce_4k_boundary": manifest.axi_enforce_4k_boundary, "qos_default": manifest.axi_qos_default},
        "memory_regions": regions,
        "fabric": {
            "channel_order": list(manifest.fabric.channel_order),
            "axi": {"wire_header_bytes": list(manifest.fabric.axi.wire_header_bytes), "data_header_sideband": manifest.fabric.axi.data_header_sideband, "w_beats_per_cycle": manifest.fabric.axi.w_beats_per_cycle},
            "initiator": {
                "source_fifo_depths": list(manifest.fabric.initiator.source_fifo_depths),
                "message_buffer_depths": list(manifest.fabric.initiator.message_buffer_depths),
                "local_delivery_depths": list(manifest.fabric.initiator.local_delivery_depths),
                "max_outstanding_reads": manifest.fabric.initiator.max_outstanding_reads,
                "max_outstanding_writes": manifest.fabric.initiator.max_outstanding_writes,
                "pre_aw_bursts": manifest.fabric.initiator.pre_aw_bursts,
                "pre_aw_beats": manifest.fabric.initiator.pre_aw_beats,
                "b_reorder_transactions": manifest.fabric.initiator.b_reorder_transactions,
                "r_reorder_beats": manifest.fabric.initiator.r_reorder_beats,
            },
            "target": {
                "write_contexts": manifest.fabric.target.write_contexts,
                "write_assembly_beats": manifest.fabric.target.write_assembly_beats,
                "read_contexts": manifest.fabric.target.read_contexts,
                "read_response_beats": manifest.fabric.target.read_response_beats,
                "orphan_w_transactions": manifest.fabric.target.orphan_w_transactions,
                "orphan_w_beats": manifest.fabric.target.orphan_w_beats,
                "service_queue_depths": list(manifest.fabric.target.service_queue_depths),
                "base_latency_cycles": list(manifest.fabric.target.base_latency_cycles),
                "response_ready_depths": list(manifest.fabric.target.response_ready_depths),
            },
            "network": {
                "router_rows": manifest.fabric.network.router_rows,
                "router_cols": manifest.fabric.network.router_cols,
                "flit_bytes": manifest.fabric.network.flit_bytes,
                "link_latency_cycles": manifest.fabric.network.link_latency_cycles,
                "router_latency_cycles": manifest.fabric.network.router_latency_cycles,
                "vcs_per_vnet": manifest.fabric.network.vcs_per_vnet,
                "vnet_classes": list(manifest.fabric.network.vnet_classes),
                "router_input_depths": list(manifest.fabric.network.router_input_depths),
                "ni_receive_depths": list(manifest.fabric.network.ni_receive_depths),
                "router_input_overrides": [dataclasses.asdict(item) for item in manifest.fabric.network.router_input_overrides],
                "yx_vnets": list(manifest.fabric.network.yx_vnets),
                "dual_lane": manifest.fabric.network.dual_lane,
            },
            "initiators": [dataclasses.asdict(item) for item in manifest.fabric.initiators],
            "targets": [
                {
                    "name": item.name,
                    "dst_node": item.dst_node,
                    "router_id": item.router_id,
                    "ranges": [
                        {**dataclasses.asdict(address_range), "access": address_range.access.name if isinstance(address_range.access, Access) else address_range.access}
                        for address_range in item.ranges
                    ],
                    **({
                        "synthetic_hbm": dataclasses.asdict(item.synthetic_hbm)
                        if isinstance(item.synthetic_hbm, SyntheticHbmConfig)
                        else item.synthetic_hbm
                    } if item.synthetic_hbm is not None else {}),
                }
                for item in manifest.fabric.targets
            ],
            "default_error_target_node": manifest.fabric.default_error_target_node,
            "quotas": [dataclasses.asdict(item) for item in manifest.fabric.quotas],
        },
    }


def validate_arch(manifest: ArchManifest) -> None:
    validate_schema("mesh_arch_v1.schema.json", architecture_document(manifest), "architecture")
    if manifest.mesh_rows * manifest.mesh_cols != len(manifest.core_ids):
        raise MeshIrError("E_CONFIG", "mesh rows*cols must equal the core count")
    if len(set(manifest.core_ids)) != len(manifest.core_ids):
        raise MeshIrError("E_CONFIG", "core ids must be unique")
    for field, value in (
        ("sram_bank_interleave_bytes", manifest.sram_bank_interleave_bytes),
        ("sram_base_alignment_bytes", manifest.sram_base_alignment_bytes),
    ):
        if not _power_of_two(value):
            raise MeshIrError("E_CONFIG", "architecture field must be a power of two", field=field, value=value)
    names = [region.name for region in manifest.regions]
    if len(names) != len(set(names)):
        raise MeshIrError("E_CONFIG", "memory region names must be unique")
    ordered = sorted(manifest.regions, key=lambda region: (region.base, region.bytes, region.name))
    for region in ordered:
        if region.base > U64_MAX - region.bytes:
            raise MeshIrError("E_CONFIG", "memory region end overflows u64", region=region.name)
        if region.kind == "CORE_SRAM_APERTURE":
            if not _power_of_two(region.tile_stride) or region.tile_stride < region.tile_bytes:
                raise MeshIrError("E_CONFIG", "invalid SRAM aperture stride", region=region.name)
            required = max(manifest.core_ids) * region.tile_stride + region.tile_bytes
            if region.bytes < required:
                raise MeshIrError("E_CONFIG", "SRAM aperture does not cover every core id", region=region.name, required=required)
            if region.tile_bytes < manifest.sram_bytes:
                raise MeshIrError("E_CONFIG", "SRAM aperture tile is smaller than core SRAM", region=region.name)
    for left, right in zip(ordered, ordered[1:]):
        if right.base < left.base + left.bytes:
            raise MeshIrError("E_CONFIG", "memory regions overlap", first=left.name, second=right.name)
    fabric = manifest.fabric
    if fabric.channel_order != ("AW", "W", "B", "AR", "R"):
        raise MeshIrError("E_CONFIG", "fabric channel order is invalid")
    if fabric.initiator.b_reorder_transactions < fabric.initiator.max_outstanding_writes:
        raise MeshIrError("E_CONFIG", "B reorder capacity must cover outstanding writes")
    if type(fabric.axi.w_beats_per_cycle) is not int or not 1 <= fabric.axi.w_beats_per_cycle <= 0xFFFFFFFF:
        raise MeshIrError("E_CONFIG", "fabric W acceptance rate must be a positive u32")
    router_count = fabric.network.router_rows * fabric.network.router_cols
    initiator_cores = [endpoint.core_id for endpoint in fabric.initiators]
    if tuple(initiator_cores) != manifest.core_ids or len(set(initiator_cores)) != len(initiator_cores):
        raise MeshIrError("E_CONFIG", "fabric requires one ordered initiator per architecture core")
    source_keys = [(endpoint.src_node, endpoint.src_port) for endpoint in fabric.initiators]
    initiator_names = [endpoint.name for endpoint in fabric.initiators]
    if len(source_keys) != len(set(source_keys)) or len(initiator_names) != len(set(initiator_names)):
        raise MeshIrError("E_CONFIG", "fabric initiator protocol identities must be unique")
    target_nodes = [endpoint.dst_node for endpoint in fabric.targets]
    target_names = [endpoint.name for endpoint in fabric.targets]
    if len(target_nodes) != len(set(target_nodes)) or len(target_names) != len(set(target_names)):
        raise MeshIrError("E_CONFIG", "fabric target identities must be unique")
    if fabric.default_error_target_node not in target_nodes:
        raise MeshIrError("E_CONFIG", "fabric default error target is unknown")
    for endpoint in fabric.targets:
        if endpoint.synthetic_hbm is not None:
            if not isinstance(endpoint.synthetic_hbm, SyntheticHbmConfig) or any(
                type(value) is not int or not 1 <= value <= 0xFFFFFFFF
                for value in dataclasses.astuple(endpoint.synthetic_hbm)
            ):
                raise MeshIrError("E_CONFIG", "synthetic HBM dimensions must be positive u32 values", target=endpoint.name)
        if endpoint.dst_node == fabric.default_error_target_node and endpoint.synthetic_hbm is not None:
            raise MeshIrError("E_CONFIG", "default error target cannot enable synthetic HBM", target=endpoint.name)
    if any(endpoint.default_target_node not in target_nodes for endpoint in fabric.initiators):
        raise MeshIrError("E_CONFIG", "fabric initiator default target is unknown")
    endpoint_routers = [endpoint.router_id for endpoint in fabric.initiators] + [endpoint.router_id for endpoint in fabric.targets]
    if any(router < 0 or router >= router_count for router in endpoint_routers):
        raise MeshIrError("E_CONFIG", "fabric endpoint router is out of range")
    if len(set(fabric.network.yx_vnets)) != len(fabric.network.yx_vnets):
        raise MeshIrError("E_CONFIG", "fabric YX vnets must be unique")
    override_keys = [(item.router_id, item.input_port, item.vnet) for item in fabric.network.router_input_overrides]
    if len(override_keys) != len(set(override_keys)) or any(item.router_id >= router_count for item in fabric.network.router_input_overrides):
        raise MeshIrError("E_CONFIG", "fabric router input override is invalid")
    wire_sizes = tuple(
        manifest.axi_data_bytes + (0 if fabric.axi.data_header_sideband else header)
        if index in (1, 4) else header
        for index, header in enumerate(fabric.axi.wire_header_bytes)
    )
    if any(size > 0x7FFFFFFF for size in wire_sizes):
        raise MeshIrError("E_CONFIG", "AXI wire message size must fit positive int")
    if fabric.network.dual_lane and any(size > fabric.network.flit_bytes for size in wire_sizes):
        raise MeshIrError("E_CONFIG", "dual-lane fabric requires single-flit AXI messages")
    covered: dict[tuple[int, int], list[tuple[int, int, str]]] = {}
    for target in fabric.targets:
        for address_range in target.ranges:
            if address_range.region_id >= len(manifest.regions):
                raise MeshIrError("E_CONFIG", "fabric target range has unknown region", target=target.name)
            region = manifest.regions[address_range.region_id]
            if region.kind == "CORE_SRAM_APERTURE":
                if address_range.owner_core not in manifest.core_ids:
                    raise MeshIrError("E_CONFIG", "fabric peer range has unknown owner", target=target.name)
                limit = region.tile_bytes
            else:
                if address_range.owner_core != INVALID_CORE_ID:
                    raise MeshIrError("E_CONFIG", "non-per-core target range has an owner", target=target.name)
                limit = region.bytes
            if address_range.offset_bytes > limit - address_range.size_bytes:
                raise MeshIrError("E_CONFIG", "fabric target range exceeds region storage", target=target.name)
            covered.setdefault((address_range.region_id, address_range.owner_core), []).append(
                (address_range.offset_bytes, address_range.offset_bytes + address_range.size_bytes, target.name)
            )
    expected_coverage = {
        (region_id, core_id): region.tile_bytes
        for region_id, region in enumerate(manifest.regions)
        if region.kind == "CORE_SRAM_APERTURE"
        for core_id in manifest.core_ids
    }
    expected_coverage.update({
        (region_id, INVALID_CORE_ID): region.bytes
        for region_id, region in enumerate(manifest.regions)
        if region.kind != "CORE_SRAM_APERTURE"
    })
    if set(covered) != set(expected_coverage):
        raise MeshIrError("E_CONFIG", "fabric target ranges do not cover every accessible architecture range")
    for key, limit in expected_coverage.items():
        cursor = 0
        for begin, end, target_name in sorted(covered[key]):
            if begin != cursor:
                raise MeshIrError("E_CONFIG", "fabric target ranges overlap or leave a gap", target=target_name)
            cursor = end
        if cursor != limit:
            raise MeshIrError("E_CONFIG", "fabric target range coverage is incomplete", region_id=key[0], owner_core=key[1])
    quota_keys = [(item.src_node, item.src_port, item.dst_node) for item in fabric.quotas]
    expected_quotas = {(source[0], source[1], target) for source in source_keys for target in target_nodes}
    if len(quota_keys) != len(set(quota_keys)) or set(quota_keys) != expected_quotas:
        raise MeshIrError("E_CONFIG", "fabric quotas must cover every source-target pair exactly once")
    for target_node in target_nodes:
        matching = [item for item in fabric.quotas if item.dst_node == target_node]
        totals = tuple(sum(getattr(item, field) for item in matching) for field in ("write_contexts", "write_beats", "read_contexts", "read_beats"))
        limits = (fabric.target.write_contexts, fabric.target.write_assembly_beats, fabric.target.read_contexts, fabric.target.read_response_beats)
        if any(total > limit for total, limit in zip(totals, limits)):
            raise MeshIrError("E_CONFIG", "fabric source-target quota sum exceeds shared target capacity", target=target_node)


def _parse(text: str) -> dict[str, Any]:
    raw = load_yaml_mapping(text, "architecture")
    validate_schema("mesh_arch_v1.schema.json", raw, "architecture", apply_defaults=True)
    return raw


def load_arch_text(text: str) -> ArchManifest:
    raw = _parse(text)
    mesh, core, axi, fabric_raw = raw["mesh"], raw["core"], raw["axi"], raw["fabric"]
    sram = core["sram"]
    max_core = max(mesh["core_ids"])
    regions = []
    for entry in raw["memory_regions"]:
        stride = entry.get("tile_stride", 0)
        tile_bytes = entry.get("tile_bytes", 0)
        size = entry.get("bytes")
        if size is None:
            if entry["kind"] != "CORE_SRAM_APERTURE" or not stride or not tile_bytes:
                raise MeshIrError("E_CONFIG", "memory region bytes is required", region=entry["name"])
            size = (max_core + 1) * stride
        regions.append(ArchRegion(entry["name"], entry["kind"], entry["base"], size, stride, tile_bytes))
    fabric = FabricConfig(
        tuple(fabric_raw["channel_order"]),
        AxiFabricConfig(tuple(fabric_raw["axi"]["wire_header_bytes"]), fabric_raw["axi"]["data_header_sideband"], fabric_raw["axi"]["w_beats_per_cycle"]),
        InitiatorAdapterConfig(
            tuple(fabric_raw["initiator"]["source_fifo_depths"]), tuple(fabric_raw["initiator"]["message_buffer_depths"]), tuple(fabric_raw["initiator"]["local_delivery_depths"]),
            fabric_raw["initiator"]["max_outstanding_reads"], fabric_raw["initiator"]["max_outstanding_writes"], fabric_raw["initiator"]["pre_aw_bursts"], fabric_raw["initiator"]["pre_aw_beats"], fabric_raw["initiator"]["b_reorder_transactions"], fabric_raw["initiator"]["r_reorder_beats"],
        ),
        TargetAdapterConfig(
            fabric_raw["target"]["write_contexts"], fabric_raw["target"]["write_assembly_beats"], fabric_raw["target"]["read_contexts"], fabric_raw["target"]["read_response_beats"], fabric_raw["target"]["orphan_w_transactions"], fabric_raw["target"]["orphan_w_beats"], tuple(fabric_raw["target"]["service_queue_depths"]), tuple(fabric_raw["target"]["base_latency_cycles"]), tuple(fabric_raw["target"]["response_ready_depths"]),
        ),
        NetworkConfig(
            fabric_raw["network"]["router_rows"], fabric_raw["network"]["router_cols"], fabric_raw["network"]["flit_bytes"], fabric_raw["network"]["link_latency_cycles"], fabric_raw["network"]["router_latency_cycles"], fabric_raw["network"]["vcs_per_vnet"], tuple(fabric_raw["network"]["vnet_classes"]), tuple(fabric_raw["network"]["router_input_depths"]), tuple(fabric_raw["network"]["ni_receive_depths"]), tuple(RouterInputDepth(**item) for item in fabric_raw["network"]["router_input_overrides"]), tuple(fabric_raw["network"]["yx_vnets"]), fabric_raw["network"]["dual_lane"],
        ),
        tuple(InitiatorEndpoint(**item) for item in fabric_raw["initiators"]),
        tuple(TargetEndpoint(item["name"], item["dst_node"], item["router_id"], tuple(TargetRange(entry["region_id"], entry["owner_core"], entry["offset_bytes"], entry["size_bytes"], Access[entry["access"]]) for entry in item["ranges"]), SyntheticHbmConfig(**item["synthetic_hbm"]) if "synthetic_hbm" in item else None) for item in fabric_raw["targets"]),
        fabric_raw["default_error_target_node"],
        tuple(SourceTargetQuota(**item) for item in fabric_raw["quotas"]),
    )
    manifest = ArchManifest(
        raw["schema_version"], raw["arch_name"], raw["clock_hz"], tuple(mesh["core_ids"]), mesh["rows"], mesh["cols"], mesh["routing"], mesh["endpoint_order"],
        core["command_rom_entries"], core["event_visibility_cycles"], core["decode_width"], core["admit_window"],
        sram["bytes"], sram["banks"], sram["read_ports_per_bank"], sram["write_ports_per_bank"], sram["bank_queue_depth"], sram["bank_interleave_bytes"], sram["read_bytes_per_cycle_per_bank"], sram["write_bytes_per_cycle_per_bank"], sram["base_alignment_bytes"],
        core["tensor_engine"]["queue_depth"], core["tensor_engine"]["setup_cycles"], core["tensor_engine"]["pipeline_flush_cycles"], dict(core["tensor_engine"]["macs_per_cycle"]),
        core["vector_engine"]["queue_depth"], core["vector_engine"]["setup_cycles"], core["vector_engine"]["flush_cycles"], dict(core["vector_engine"]["elements_per_cycle"]),
        core["reduce_engine"]["queue_depth"], core["reduce_engine"]["setup_cycles"], core["reduce_engine"]["flush_cycles"], dict(core["reduce_engine"]["ops_per_cycle"]),
        core["dma"]["read_engines"], core["dma"]["write_engines"], core["dma"]["descriptor_queue_depth"], core["dma"]["segment_queue_depth"], core["dma"]["read_outstanding"], core["dma"]["write_outstanding"], core["dma"]["setup_cycles"], core["dma"]["burst_base_latency"],
        axi["data_bytes"], axi["max_burst_beats"], axi["address_bits"], axi["id_bits"], axi["max_outstanding_per_id"], axi["enforce_4k_boundary"], axi["qos_default"], tuple(regions), fabric,
    )
    validate_arch(manifest)
    return manifest


def load_arch(path: str | Path) -> ArchManifest:
    try:
        return load_arch_text(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise MeshIrError("E_CONFIG", "cannot read architecture manifest", path=str(path), detail=str(error)) from error
