from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from mesh_ir.analysis.sram import SramAllocation
from mesh_ir.acceptance import canonical_digest
from mesh_ir.architecture import AxiFabricConfig, ArchManifest, FabricConfig, InitiatorAdapterConfig, InitiatorEndpoint, NetworkConfig, RouterInputDepth, SourceTargetQuota, SyntheticHbmConfig, TargetAdapterConfig, TargetEndpoint, TargetRange, validate_arch
from mesh_ir.builder import ProgramBuilder
from mesh_ir.burst_splitter import split_segment
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Access, DType, DmaKind, INVALID_CORE_ID, Layout, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.kernel_ir import AllocAttrs, BufferObject, BufferView, ControlToken, DistributionKind, DmaAttrs, ElementRegion, KernelMemoryRecords, KernelOp, KernelOpcode, KernelTensor, OperandAccess, OperandAccessMode, Placement, StateOrigin, StateTransition, TensorShard, TensorState, ViewDeclarationAttrs
from mesh_ir.model import Program
from mesh_ir.publication import publish_authored_program
from mesh_ir.scheduled.model import AuthoredProgramOrigin, ExternalSlotBacking, HaltAttrs, ObjectBacking, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.traffic import Binding, BindingSlot

from .config import CHANNELS, Topology, Workload, WorkloadSpec, validated_yx_vnets


@dataclass
class ExperimentWorkload:
    arch: ArchManifest
    program: Program
    plan: dict
    oracle: dict


def resolve_experiment_architecture(
    base: ArchManifest,
    topology: Topology,
    profile: dict,
    *,
    router_input_depths: tuple[int, ...] | None = None,
    router_input_overrides: tuple[RouterInputDepth, ...] = (),
) -> ArchManifest:
    if tuple(base.core_ids) != topology.core_ids or (base.mesh_rows, base.mesh_cols) != (5, 5):
        raise ValueError("experiment architecture must retain all 25 cores")
    network = profile["network"]
    axi = profile["axi"]
    backend = profile["backend"]
    if axi["w_beats_per_cycle"] != 1:
        raise ValueError("formal experiment requires one local W beat per cycle")
    if any(type(value) is not int or value <= 0 for value in (
        backend["bytes_per_cycle"], backend["queue_depth"], backend["port_bytes"]
    )):
        raise ValueError("backend execution policy requires positive integer dimensions")
    hbm_id, hbm = next(
        (index, region)
        for index, region in enumerate(base.regions)
        if region.kind == "HBM"
    )
    window_bytes = backend["port_bytes"]
    covered_hbm = len(topology.hbm_routers) * window_bytes
    if covered_hbm >= hbm.bytes:
        raise ValueError("experiment HBM windows must leave an error-routed tail")
    error_node = topology.error_node
    targets = [
        TargetEndpoint(
            f"hbm_{index}",
            topology.target_node(index),
            router,
            (TargetRange(hbm_id, INVALID_CORE_ID, index * window_bytes, window_bytes, Access.READ_WRITE),),
            SyntheticHbmConfig(backend["bytes_per_cycle"], backend["queue_depth"]),
        )
        for index, router in enumerate(topology.hbm_routers)
    ]
    error_ranges = [
        TargetRange(hbm_id, INVALID_CORE_ID, covered_hbm, hbm.bytes - covered_hbm, Access.READ_WRITE)
    ]
    for region_id, region in enumerate(base.regions):
        if region.kind == "HBM":
            continue
        if region.kind == "CORE_SRAM_APERTURE":
            error_ranges.extend(
                TargetRange(region_id, core_id, 0, region.tile_bytes, Access.READ_WRITE)
                for core_id in base.core_ids
            )
        else:
            error_ranges.append(
                TargetRange(region_id, INVALID_CORE_ID, 0, region.bytes, Access.READ_WRITE)
            )
    targets.append(TargetEndpoint("error", error_node, 12, tuple(error_ranges)))
    initiators = tuple(
        InitiatorEndpoint(
            f"core_{core_id}",
            core_id,
            core_id,
            0,
            core_id,
            topology.target_node(core_id % len(topology.hbm_routers)),
        )
        for core_id in base.core_ids
    )
    quota = axi["target_quota"]
    quotas = tuple(
        SourceTargetQuota(
            source.src_node,
            source.src_port,
            target.dst_node,
            quota["write_contexts"],
            quota["write_beats"],
            quota["read_contexts"],
            quota["read_beats"],
        )
        for source in initiators
        for target in targets
    )
    depths = tuple(router_input_depths or network["baseline_depths"])
    fabric = FabricConfig(
        CHANNELS,
        AxiFabricConfig(
            tuple(axi["wire_header_bytes"]),
            axi.get("data_header_sideband", False),
            axi["w_beats_per_cycle"],
        ),
        InitiatorAdapterConfig(
            tuple(axi["source_fifo_depths"]),
            tuple(axi["message_buffer_depths"]),
            tuple(axi["local_delivery_depths"]),
            base.dma_read_outstanding,
            base.dma_write_outstanding,
            axi["source_pre_aw_bursts"],
            axi["source_pre_aw_beats"],
            axi["b_rob_transactions"],
            axi["r_rob_beats"],
        ),
        TargetAdapterConfig(
            len(base.core_ids) * quota["write_contexts"],
            len(base.core_ids) * quota["write_beats"],
            len(base.core_ids) * quota["read_contexts"],
            len(base.core_ids) * quota["read_beats"],
            axi["orphan_w_transactions"],
            axi["orphan_w_beats"],
            tuple(axi["target_service_depths"]),
            (backend["base_latency_cycles"],) * 2,
            tuple(axi["target_response_ready_depths"]),
        ),
        NetworkConfig(
            base.mesh_rows,
            base.mesh_cols,
            network["flit_bytes"],
            network["link_latency"],
            network["router_latency"],
            network["vcs_per_vnet"],
            ("ctrl", "data", "ctrl", "ctrl", "data"),
            depths,
            tuple(network["ni_depths"]),
            router_input_overrides,
            validated_yx_vnets(network.get("yx_vnets", ())),
            network.get("dual_lane", False),
        ),
        initiators,
        tuple(targets),
        error_node,
        quotas,
    )
    resolved = replace(base, fabric=fabric)
    validate_arch(resolved)
    return resolved


def experiment_hbm_targets(arch: ArchManifest) -> tuple[TargetEndpoint, ...]:
    targets = tuple(target for target in arch.fabric.targets if target.name.startswith("hbm_"))
    try:
        ordered = tuple(sorted(targets, key=lambda target: int(target.name.removeprefix("hbm_"))))
    except ValueError as error:
        raise ValueError("experiment HBM target names require numeric suffixes") from error
    routers = tuple(target.router_id for target in ordered)
    matches = tuple(name for name, layout in Topology.layouts.items() if layout == routers)
    if len(matches) != 1 or tuple(target.dst_node for target in ordered) != tuple(
        25 + index for index in range(len(ordered))
    ):
        raise ValueError("resolved fabric does not identify one supported experiment topology")
    return ordered


def experiment_topology(arch: ArchManifest) -> Topology:
    routers = tuple(target.router_id for target in experiment_hbm_targets(arch))
    return Topology(next(name for name, layout in Topology.layouts.items() if layout == routers))


def dimension_order_routers(source, destination, columns=5, y_first=False):
    current = source
    path = [current]
    for stride in ((columns, 1) if y_first else (1, columns)):
        while current // stride % columns != destination // stride % columns:
            current += stride if current // stride % columns < destination // stride % columns else -stride
            path.append(current)
    return path


def packet_flits(header_bytes, data_bytes=32, flit_bytes=16, data_header_sideband=False):
    if len(header_bytes) != len(CHANNELS) or flit_bytes <= 0 or data_bytes <= 0:
        raise ValueError("invalid packetization dimensions")
    wire_bytes = {channel: data_bytes + (0 if data_header_sideband else header)
                  if channel in ("R", "W") else header for channel, header in zip(CHANNELS, header_bytes)}
    return {channel: (size + flit_bytes - 1) // flit_bytes for channel, size in wire_bytes.items()}


def traffic_oracle(plan, arch: ArchManifest):
    targets = experiment_hbm_targets(arch)
    initiators = {item.core_id: item for item in arch.fabric.initiators}
    yx_channels = {CHANNELS[vnet] for vnet in arch.fabric.network.yx_vnets}
    packets = dict.fromkeys(CHANNELS, 0)
    links = defaultdict(lambda: dict.fromkeys(CHANNELS, 0))
    matrix = defaultdict(lambda: defaultdict(lambda: {"read_bytes": 0, "write_bytes": 0,
                                                     "read_bursts": 0, "write_bursts": 0}))
    totals = {"read": 0, "write": 0}
    bursts = {"read": 0, "write": 0}
    flits_per_packet = packet_flits(
        arch.fabric.axi.wire_header_bytes,
        arch.axi_data_bytes,
        arch.fabric.network.flit_bytes,
        arch.fabric.axi.data_header_sideband,
    )
    descriptors = []
    for tile in plan["tiles"]:
        direction = tile["direction"]
        split = split_segment(
            tile["address"], tile["useful_bytes"],
            arch.axi_data_bytes, arch.axi_max_burst_beats,
        )
        totals[direction] += tile["useful_bytes"]
        bursts[direction] += len(split)
        target = tile["target_index"]
        cell = matrix[tile["core_id"]][target]
        cell[direction + "_bytes"] += tile["useful_bytes"]
        cell[direction + "_bursts"] += len(split)
        counts = {"AR": len(split), "R": sum(row.beats for row in split)} if direction == "read" else {
            "AW": len(split), "W": sum(row.beats for row in split), "B": len(split)}
        initiator = initiators[tile["core_id"]]
        endpoint = targets[target]
        descriptor_channels = {}
        descriptor_links = {}
        for channel in CHANNELS:
            count = counts.get(channel, 0)
            packets[channel] += count
            src_node, dst_node = initiator.src_node, endpoint.dst_node
            src_router, dst_router = initiator.router_id, endpoint.router_id
            if channel in ("R", "B"):
                src_node, dst_node = dst_node, src_node
                src_router, dst_router = dst_router, src_router
            route = dimension_order_routers(
                src_router, dst_router, arch.fabric.network.router_cols,
                channel in yx_channels,
            )
            traversed = [f"endpoint:{src_node}->router:{src_router}"] + [
                f"router:{left}->router:{right}" for left, right in zip(route, route[1:])] + [
                f"router:{dst_router}->endpoint:{dst_node}"]
            if count:
                for link in traversed:
                    links[link][channel] += count * flits_per_packet[channel]
            wire_bytes = count * (
                arch.axi_data_bytes + (
                    0 if arch.fabric.axi.data_header_sideband else arch.fabric.axi.wire_header_bytes[CHANNELS.index(channel)]
                ) if channel in ("R", "W") else arch.fabric.axi.wire_header_bytes[CHANNELS.index(channel)]
            )
            descriptor_channels[channel] = {
                "source_node": src_node,
                "destination_node": dst_node,
                "packets": count,
                "flits": count * flits_per_packet[channel],
                "wire_bytes": wire_bytes,
            }
            descriptor_links[channel] = traversed
        descriptors.append({
            "descriptor_id": tile["descriptor_id"],
            "core_id": tile["core_id"],
            "direction": direction,
            "target_index": target,
            "target_node": endpoint.dst_node,
            "target_router": endpoint.router_id,
            "src_router": endpoint.router_id if direction == "read" else initiator.router_id,
            "dst_router": initiator.router_id if direction == "read" else endpoint.router_id,
            "address": tile["address"],
            "useful_bytes": tile["useful_bytes"],
            "bursts": len(split),
            "channels": descriptor_channels,
            "packets": sum(row["packets"] for row in descriptor_channels.values()),
            "flits": sum(row["flits"] for row in descriptor_channels.values()),
            "wire_bytes": sum(row["wire_bytes"] for row in descriptor_channels.values()),
            "directed_links": descriptor_links,
        })
    return {"useful_bytes": sum(totals.values()), "useful_read_bytes": totals["read"],
            "useful_write_bytes": totals["write"], "burst_counts": bursts,
            "tile_count": len(plan["tiles"]), "packets": packets,
            "flits": {channel: count * flits_per_packet[channel] for channel, count in packets.items()},
            "flits_per_packet": flits_per_packet, "descriptors": descriptors,
            "directed_link_flits": dict(sorted(links.items())),
            "core_hbm_matrix": {str(core): {str(target): value for target, value in sorted(row.items())}
                                for core, row in sorted(matrix.items())}}


def validate_simulation_horizon(oracle, max_sim_ticks, clock_hz, ticks_per_second):
    if any(type(value) is not int or value <= 0 for value in (max_sim_ticks, clock_hz, ticks_per_second)):
        raise ValueError("simulation horizon and clock units must be positive integers")
    loads = {}
    for route, counts in oracle["directed_link_flits"].items():
        if set(counts) != set(CHANNELS) or any(type(count) is not int or count < 0 for count in counts.values()):
            raise ValueError("directed-link oracle requires all channel counts as nonnegative integers")
        loads[route] = sum(counts.values())
    route = min(loads, key=lambda key: (-loads[key], key)) if loads else None
    flits = loads[route] if route is not None else 0
    minimum_ticks = (flits * ticks_per_second + clock_hz - 1) // clock_hz
    if max_sim_ticks <= minimum_ticks:
        raise ValueError(f"max_sim_ticks={max_sim_ticks} is not greater than directed-link serialization lower bound "
                         f"{minimum_ticks} ticks ({route}, {flits} flits)")
    return {"bound_kind": "directed_link_serialization_necessary_only", "route": route,
            "flits": flits, "minimum_ticks": minimum_ticks}


def build_workload(arch: ArchManifest, spec: WorkloadSpec):
    topology = experiment_topology(arch)
    targets = experiment_hbm_targets(arch)
    if tuple(arch.core_ids) != topology.core_ids or (arch.mesh_rows, arch.mesh_cols) != (5, 5):
        raise ValueError("experiment architecture must retain all 25 cores")
    if arch.axi_data_bytes != 32 or arch.axi_max_burst_beats not in (1, 4, 16):
        raise ValueError("experiment requires 32-byte AXI and declared burst length")
    if not 0 <= spec.hotspot_target < len(topology.hbm_routers):
        raise ValueError("hotspot target does not exist")
    region_ids = {region.kind: index for index, region in enumerate(arch.regions)}
    hbm_id = region_ids["HBM"]
    hbm = arch.regions[hbm_id]
    target_ranges = tuple(
        next(address_range for address_range in target.ranges if address_range.region_id == hbm_id)
        for target in targets
    )
    if any(
        address_range.offset_bytes != index * spec.hbm_port_bytes
        or address_range.size_bytes != spec.hbm_port_bytes
        for index, address_range in enumerate(target_ranges)
    ):
        raise ValueError("workload HBM windows differ from the resolved architecture")
    directions = ("read", "write") if spec.workload == Workload.MIXED_1_1 else (
        "read" if spec.workload == Workload.LOAD_ONLY else "write",)
    if spec.tile_bytes * spec.ring_slots * len(directions) > arch.sram_bytes:
        raise ValueError("ring buffers exceed local SRAM")
    tensors = []
    placements = []
    shards = []
    objects = []
    views = []
    states = []
    tokens = []
    allocations = []
    backings = []
    slots = []
    effects = []
    buffers = {}
    tiles = []
    setup = []
    next_state_id = 1
    next_token_id = 1
    for core in topology.core_ids:
        if core not in spec.active_cores:
            continue
        for direction_index, direction in enumerate(directions):
            for slot in range(spec.ring_slots):
                offset = (direction_index * spec.ring_slots + slot) * spec.tile_bytes
                tensor_id = len(tensors) + 1
                placement_id = len(placements) + 1
                shard_id = len(shards) + 1
                object_id = len(objects) + 1
                view_id = len(views) + 1
                role = TensorRole.INPUT if direction == "read" else TensorRole.OUTPUT
                tensors.append(KernelTensor(tensor_id, 0, None, tensor_id, 0, f"c{core}_{direction}_{slot}", role, DType.INT8, (spec.tile_bytes,), (1,), StorageClass.EXTERNAL, Access.READ_WRITE, spec.tile_bytes, spec.tile_bytes, None))
                placements.append(Placement(placement_id, (core,)))
                shards.append(TensorShard(shard_id, tensor_id, placement_id, core, DistributionKind.PARTITIONED, (0,), (spec.tile_bytes,), (spec.tile_bytes,), 0))
                objects.append(BufferObject(object_id, tensor_id, core, MemorySpace.CORE_SRAM, (spec.tile_bytes,), (1,), spec.tile_bytes, arch.sram_base_alignment_bytes, False, 0))
                views.append(BufferView(view_id, object_id, shard_id, (0,), (spec.tile_bytes,), (spec.tile_bytes,), 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0))
                empty_state = next_state_id
                next_state_id += 1
                states.append(TensorState(empty_state, object_id, 0, StateOrigin.EMPTY))
                buffers[core, direction, slot] = {
                    "tensor_id": tensor_id,
                    "shard_id": shard_id,
                    "object_id": object_id,
                    "view_id": view_id,
                    "state_id": empty_state,
                    "version": 0,
                    "offset": offset,
                }
                allocations.append(SramAllocation(len(allocations) + 1, object_id, core, offset, spec.tile_bytes, arch.sram_base_alignment_bytes))
                if direction == "write":
                    expected_byte = (core * 37 + slot * 11 + 0x51) % 255 + 1
                    produced_state = next_state_id
                    next_state_id += 1
                    states.append(TensorState(produced_state, object_id, 1, StateOrigin.PRODUCED))
                    token_id = next_token_id
                    next_token_id += 1
                    tokens.append(ControlToken(token_id))
                    region = ElementRegion((0,), (spec.tile_bytes,), (1,))
                    effect_index = len(effects)
                    effects.append((core, f"fill:{core}:{slot}", (), (StateTransition(empty_state, produced_state, view_id, region),), DmaAttrs(DmaKind.LOCAL_FILL, core, core, core, 0, bytes((expected_byte,))), (), token_id))
                    buffers[core, direction, slot]["state_id"] = produced_state
                    buffers[core, direction, slot]["version"] = 1
                    setup.append({"core_id": core, "effect_index": effect_index,
                                  "sram_offset": offset, "useful_bytes": spec.tile_bytes,
                                  "expected_byte": expected_byte})
        pairs = spec.tiles_per_core // len(directions)
        for pair in range(pairs):
            slot = pair % spec.ring_slots
            for direction_index, direction in enumerate(directions):
                logical_tile = pair * len(directions) + direction_index
                if spec.distribution == "single_target":
                    target = spec.hotspot_target
                elif spec.distribution == "hotspot":
                    other_targets = tuple(i for i in range(len(topology.hbm_routers)) if i != spec.hotspot_target)
                    target = spec.hotspot_target if pair % 2 == 0 else other_targets[(core + pair // 2) % len(other_targets)]
                else:
                    target = (core + logical_tile) % len(topology.hbm_routers)
                remote_offset = target * spec.hbm_port_bytes + core * 2 * spec.bytes_per_core + (
                    spec.bytes_per_core if direction == "write" else 0) + pair * spec.tile_bytes
                buffer = buffers[core, direction, slot]
                external_object_id = len(objects) + 1
                external_view_id = len(views) + 1
                objects.append(BufferObject(external_object_id, buffer["tensor_id"], INVALID_CORE_ID, MemorySpace.HBM, (spec.tile_bytes,), (1,), spec.tile_bytes, arch.axi_data_bytes, False, 0))
                views.append(BufferView(external_view_id, external_object_id, buffer["shard_id"], (0,), (spec.tile_bytes,), (spec.tile_bytes,), 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0))
                slot_id = len(slots) + 1
                access = Access.READ_ONLY if direction == "read" else Access.READ_WRITE
                binding = Binding(slot_id, hbm_id, INVALID_CORE_ID, remote_offset, spec.tile_bytes, arch.axi_data_bytes, access)
                slots.append(BindingSlot(slot_id, f"c{core}:{direction}:{logical_tile}", MemorySpace.HBM, hbm_id, INVALID_CORE_ID, spec.tile_bytes, arch.axi_data_bytes, access, binding))
                backings.append(ObjectBacking(external_object_id, ExternalSlotBacking(slot_id)))
                region = ElementRegion((0,), (spec.tile_bytes,), (1,))
                external_state = next_state_id
                next_state_id += 1
                external_origin = StateOrigin.EXTERNAL if direction == "read" else StateOrigin.EMPTY
                states.append(TensorState(external_state, external_object_id, 0, external_origin))
                token_id = next_token_id
                next_token_id += 1
                tokens.append(ControlToken(token_id))
                if direction == "read":
                    new_local_state = next_state_id
                    next_state_id += 1
                    buffer["version"] += 1
                    states.append(TensorState(new_local_state, buffer["object_id"], buffer["version"], StateOrigin.PRODUCED))
                    reads = (OperandAccess(external_state, external_view_id, region, OperandAccessMode.READ),)
                    writes = (StateTransition(buffer["state_id"], new_local_state, buffer["view_id"], region),)
                    attrs = DmaAttrs(DmaKind.LOAD, core, INVALID_CORE_ID, core, 0, b"")
                    buffer["state_id"] = new_local_state
                else:
                    produced_external_state = next_state_id
                    next_state_id += 1
                    states.append(TensorState(produced_external_state, external_object_id, 1, StateOrigin.PRODUCED))
                    reads = (OperandAccess(buffer["state_id"], buffer["view_id"], region, OperandAccessMode.READ),)
                    writes = (StateTransition(external_state, produced_external_state, external_view_id, region),)
                    attrs = DmaAttrs(DmaKind.STORE, core, core, INVALID_CORE_ID, 0, b"")
                effect_index = len(effects)
                effects.append((core, f"{direction}:{core}:{logical_tile}", reads, writes, attrs, (), token_id))
                address = hbm.base + remote_offset
                tiles.append({"core_id": core, "direction": direction, "tile_index": logical_tile,
                              "pair_index": pair, "slot": slot, "effect_index": effect_index,
                              "target_index": target, "target_node": targets[target].dst_node,
                              "target_router": targets[target].router_id,
                              "address": address, "useful_bytes": spec.tile_bytes,
                              "sram_offset": buffer["offset"],
                              "expected_byte": ((core * 37 + slot * 11 + 0x51) % 255 + 1) if direction == "write" else (
                                  (core * 17 + target * 31 + pair * 7 + 3) % 255 + 1),
                              "bursts": [asdict(burst) for burst in split_segment(
                                  address, spec.tile_bytes, arch.axi_data_bytes, arch.axi_max_burst_beats)]})
    declarations = tuple(KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None) for index, item in enumerate(objects, 1))
    declarations += tuple(KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, next(obj.owner_core for obj in objects if obj.object_id == item.object_id), 0, (), (), ViewDeclarationAttrs(item.view_id), (), None) for index, item in enumerate(views, 1))
    effect_ops = tuple(KernelOp(len(declarations) + index, 0, f"{index:08d}:{stable_key}", KernelOpcode.DMA, core, 0, reads, writes, attrs, dependencies, done_token) for index, (core, stable_key, reads, writes, attrs, dependencies, done_token) in enumerate(effects, 1))
    records = KernelMemoryRecords(tuple(tensors), (), tuple(placements), tuple(shards), (), tuple(objects), tuple(views), tuple(states), tuple(tokens), declarations + effect_ops)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("experiment", "mesh_outstanding_buffer_experiment", 1))
    variant = builder.variant("main", spec.workload, f"mesh_outstanding_buffer_experiment:{spec.workload}", records=records, allocations=tuple(allocations), external_backings=tuple(backings), binding_slots=tuple(slots))
    streams = {}
    for core in topology.core_ids:
        flags = A.STREAM_FLAGS.IS_LOCAL_CONTROL | (A.STREAM_FLAGS.IS_LIFECYCLE if core == 0 else 0)
        streams[core] = variant.stream(core, 0, flags)
    streams[0].control_command(RequestBeginAttrs())
    for effect in effect_ops:
        streams[effect.owner_core].kernel_command(effect.op_id)
    streams[0].control_command(RequestEndAttrs())
    for stream in streams.values():
        stream.control_command(HaltAttrs())
    program = builder.build()
    verify_program(program, arch)
    command_by_op = {item.source.kernel_op_id: item.command_id for item in program.semantics.command_semantics if hasattr(item.source, "kernel_op_id")}
    descriptor_by_command = {item.command_id: item for item in program.dma_descriptors}
    for item in (*setup, *tiles):
        op_id = effect_ops[item.pop("effect_index")].op_id
        command_id = command_by_op[op_id]
        descriptor = descriptor_by_command[command_id]
        item.update(descriptor_id=descriptor.descriptor_id, command_id=command_id, completion_event=descriptor.completion_event)
    plan = {"schema_version": 1, "topology": topology.name, "spec": spec.document(),
            "setup": setup, "tiles": tiles}
    plan["workload_digest"] = canonical_digest(plan)
    oracle = traffic_oracle(plan, arch)
    oracle["oracle_digest"] = canonical_digest(oracle)
    return ExperimentWorkload(arch, program, plan, oracle)


def write_workload(bundle, destination):
    return publish_authored_program(
        destination,
        bundle.program,
        bundle.arch,
        kind="experiment",
        identity=(("workload_digest", bundle.plan["workload_digest"]),),
        extra_documents=(("oracle.json", bundle.oracle), ("workload.json", bundle.plan)),
    )
