import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from mesh_ir.abi.encoder import encode_program
from mesh_ir.abi.verifier import verify_program
from mesh_ir.acceptance import canonical_digest
from mesh_ir.builder import ProgramBuilder
from mesh_ir.burst_splitter import split_segment
from mesh_ir.generated import abi as A
from mesh_ir.model import DmaEndpoint, canonical_json_bytes

from .config import CHANNELS, Topology, Workload, WorkloadSpec, validated_yx_vnets


@dataclass
class ExperimentWorkload:
    program: object
    plan: dict
    oracle: dict


def dimension_order_routers(source, destination, y_first=False):
    current = source
    path = [current]
    for stride in ((5, 1) if y_first else (1, 5)):
        while current // stride % 5 != destination // stride % 5:
            current += stride if current // stride % 5 < destination // stride % 5 else -stride
            path.append(current)
    return path


def packet_flits(header_bytes, data_bytes=32, flit_bytes=16, data_header_sideband=False):
    if len(header_bytes) != len(CHANNELS) or flit_bytes <= 0 or data_bytes <= 0:
        raise ValueError("invalid packetization dimensions")
    wire_bytes = {channel: data_bytes + (0 if data_header_sideband else header)
                  if channel in ("R", "W") else header for channel, header in zip(CHANNELS, header_bytes)}
    return {channel: (size + flit_bytes - 1) // flit_bytes for channel, size in wire_bytes.items()}


def traffic_oracle(plan, topology, data_bytes=32, max_beats=16,
                   flit_bytes=16, header_bytes=(24, 16, 8, 24, 16), data_header_sideband=False,
                   yx_vnets=()):
    yx_channels = {CHANNELS[vnet] for vnet in validated_yx_vnets(yx_vnets)}
    packets = dict.fromkeys(CHANNELS, 0)
    links = defaultdict(lambda: dict.fromkeys(CHANNELS, 0))
    matrix = defaultdict(lambda: defaultdict(lambda: {"read_bytes": 0, "write_bytes": 0,
                                                     "read_bursts": 0, "write_bursts": 0}))
    totals = {"read": 0, "write": 0}
    bursts = {"read": 0, "write": 0}
    flits_per_packet = packet_flits(header_bytes, data_bytes, flit_bytes, data_header_sideband)
    for tile in plan["tiles"]:
        direction = tile["direction"]
        split = split_segment(tile["address"], tile["useful_bytes"], data_bytes, max_beats)
        totals[direction] += tile["useful_bytes"]
        bursts[direction] += len(split)
        target = tile["target_index"]
        cell = matrix[tile["core_id"]][target]
        cell[direction + "_bytes"] += tile["useful_bytes"]
        cell[direction + "_bursts"] += len(split)
        channels = {"AR": len(split), "R": sum(row.beats for row in split)} if direction == "read" else {
            "AW": len(split), "W": sum(row.beats for row in split), "B": len(split)}
        for channel, count in channels.items():
            packets[channel] += count
            src_node, dst_node = tile["core_id"], topology.target_node(target)
            src_router, dst_router = tile["core_id"], topology.hbm_routers[target]
            if channel in ("R", "B"):
                src_node, dst_node = dst_node, src_node
                src_router, dst_router = dst_router, src_router
            route = dimension_order_routers(src_router, dst_router, channel in yx_channels)
            traversed = [f"endpoint:{src_node}->router:{src_router}"] + [
                f"router:{left}->router:{right}" for left, right in zip(route, route[1:])] + [
                f"router:{dst_router}->endpoint:{dst_node}"]
            for link in traversed:
                links[link][channel] += count * flits_per_packet[channel]
    return {"useful_bytes": sum(totals.values()), "useful_read_bytes": totals["read"],
            "useful_write_bytes": totals["write"], "burst_counts": bursts,
            "tile_count": len(plan["tiles"]), "packets": packets,
            "flits": {channel: count * flits_per_packet[channel] for channel, count in packets.items()},
            "flits_per_packet": flits_per_packet,
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


def build_workload(arch, topology: Topology, spec: WorkloadSpec, *, flit_bytes=16,
                   header_bytes=(24, 16, 8, 24, 16), data_header_sideband=False, yx_vnets=()):
    if tuple(arch.core_ids) != topology.core_ids or (arch.mesh_rows, arch.mesh_cols) != (5, 5):
        raise ValueError("experiment architecture must retain all 25 cores")
    if arch.axi_data_bytes != 32 or arch.axi_max_burst_beats not in (1, 4, 16):
        raise ValueError("experiment requires 32-byte AXI and declared burst length")
    if not 0 <= spec.hotspot_target < len(topology.hbm_routers):
        raise ValueError("hotspot target does not exist")
    region_ids = {region.kind: index for index, region in enumerate(arch.regions)}
    hbm_id = region_ids["HBM"]
    sram_id = region_ids["CORE_SRAM_APERTURE"]
    hbm = arch.regions[hbm_id]
    if len(topology.hbm_routers) * spec.hbm_port_bytes > hbm.bytes:
        raise ValueError("HBM region cannot contain all fixed target windows")
    directions = ("read", "write") if spec.workload == Workload.MIXED_1_1 else (
        "read" if spec.workload == Workload.LOAD_ONLY else "write",)
    if spec.tile_bytes * spec.ring_slots * len(directions) > arch.sram_bytes:
        raise ValueError("ring buffers exceed local SRAM")
    builder = ProgramBuilder(arch, "mesh_outstanding_buffer_experiment")
    entrypoint = builder.entrypoint("main", spec.workload, 0, 0)
    tiles, setup = [], []
    begin = builder.event()
    for core in topology.core_ids:
        flags = A.STREAM_FLAGS.IS_LOCAL_CONTROL | (A.STREAM_FLAGS.IS_LIFECYCLE if core == 0 else 0)
        stream = builder.stream(core, 0, flags=flags)
        if core == 0:
            stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=begin)
        if core not in spec.active_cores:
            done = builder.event()
            stream.command(A.OPCODE.REQUEST_END, signal_event=done)
            stream.command(A.OPCODE.HALT, waits=(done,))
            continue
        buffers = {}
        setup_events = []
        for direction_index, direction in enumerate(directions):
            for slot in range(spec.ring_slots):
                offset = (direction_index * spec.ring_slots + slot) * spec.tile_bytes
                tensor = builder.tensor(f"c{core}_{direction}_{slot}", A.TENSOR_ROLE.INPUT if direction == "read" else A.TENSOR_ROLE.OUTPUT,
                                        A.DTYPE.INT8, A.STORAGE_CLASS.HBM, A.ACCESS_KIND.READ_WRITE,
                                        (spec.tile_bytes,))
                allocation = builder.allocation(core, offset, spec.tile_bytes,
                                                arch.sram_base_alignment_bytes)
                shard = builder.shard(tensor, core, allocation, (spec.tile_bytes,), spec.tile_bytes)
                local = DmaEndpoint(A.MEMORY_SPACE.CORE_SRAM, sram_id, core, tensor, shard, 0, offset)
                buffers[direction, slot] = (tensor, shard, allocation, local)
                if direction == "write":
                    expected_byte = (core * 37 + slot * 11 + 0x51) % 255 + 1
                    event = builder.event()
                    command = stream.command(A.OPCODE.DMA_FILL, waits=(begin,) if core == 0 else (),
                                             operands=((tensor, shard, allocation, A.ACCESS_KIND.READ_WRITE),),
                                             attr_index=builder.fill_attr(expected_byte * 0x0101010101010101))
                    descriptor = builder.dma(command, A.DMA_KIND.LOCAL_FILL, local, local,
                                             1, spec.tile_bytes, spec.tile_bytes, spec.tile_bytes, event)
                    builder.oracle(entrypoint, 1, descriptor, command.command_id,
                                   A.DMA_KIND.LOCAL_FILL, offset, 1, spec.tile_bytes, spec.tile_bytes)
                    setup.append({"core_id": core, "descriptor_id": descriptor,
                                  "command_id": command.command_id, "completion_event": event,
                                  "sram_offset": offset, "useful_bytes": spec.tile_bytes,
                                  "expected_byte": expected_byte})
                    setup_events.append(event)
        slot_done = {slot: tuple(setup_events) for slot in range(spec.ring_slots)}
        if not setup_events and core == 0:
            slot_done = dict.fromkeys(slot_done, (begin,))
        pairs = spec.tiles_per_core // len(directions)
        for pair in range(pairs):
            slot = pair % spec.ring_slots
            waits = slot_done[slot]
            pair_events = []
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
                tensor, shard, allocation, local = buffers[direction, slot]
                remote = DmaEndpoint(A.MEMORY_SPACE.HBM, hbm_id, 0xFFFF, tensor, shard, 0, remote_offset)
                event = builder.event()
                kind = A.DMA_KIND.LOAD if direction == "read" else A.DMA_KIND.STORE
                opcode = A.OPCODE.DMA_LOAD if direction == "read" else A.OPCODE.DMA_STORE
                command = stream.command(opcode, waits=waits,
                                         operands=((tensor, shard, allocation, A.ACCESS_KIND.READ_WRITE),))
                descriptor = builder.dma(command, kind, remote if direction == "read" else local,
                                         local if direction == "read" else remote, 1, spec.tile_bytes,
                                         spec.tile_bytes, spec.tile_bytes, event)
                builder.oracle(entrypoint, 1, descriptor, command.command_id, kind,
                               remote_offset, 1, spec.tile_bytes, spec.tile_bytes)
                address = hbm.base + remote_offset
                tiles.append({"core_id": core, "direction": direction, "tile_index": logical_tile,
                              "pair_index": pair, "slot": slot, "descriptor_id": descriptor,
                              "command_id": command.command_id, "completion_event": event,
                              "target_index": target, "target_node": topology.target_node(target),
                              "address": address, "useful_bytes": spec.tile_bytes,
                              "sram_offset": local.offset_bytes,
                              "expected_byte": ((core * 37 + slot * 11 + 0x51) % 255 + 1) if direction == "write" else (
                                  (core * 17 + target * 31 + pair * 7 + 3) % 255 + 1),
                              "bursts": [asdict(burst) for burst in split_segment(
                                  address, spec.tile_bytes, arch.axi_data_bytes, arch.axi_max_burst_beats)]})
                pair_events.append(event)
            slot_done[slot] = tuple(pair_events)
        done = builder.event()
        waits = tuple(sorted({event for events in slot_done.values() for event in events}))
        stream.command(A.OPCODE.REQUEST_END, waits=waits, signal_event=done)
        stream.command(A.OPCODE.HALT, waits=(done,))
    program = builder.build()
    verify_program(program, arch)
    plan = {"schema_version": 1, "topology": topology.name, "spec": spec.document(),
            "setup": setup, "tiles": tiles}
    plan["workload_digest"] = canonical_digest(plan)
    oracle = traffic_oracle(plan, topology, data_bytes=arch.axi_data_bytes,
                            max_beats=arch.axi_max_burst_beats,
                            flit_bytes=flit_bytes, header_bytes=header_bytes,
                            data_header_sideband=data_header_sideband, yx_vnets=yx_vnets)
    oracle["oracle_digest"] = canonical_digest(oracle)
    return ExperimentWorkload(program, plan, oracle)


def write_workload(bundle, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    blob = encode_program(bundle.program)
    (destination / "program.mshb").write_bytes(blob)
    documents = {"schedule.mesh.json": bundle.program.canonical_dict(),
                 "expected_traffic.json": [asdict(row) for row in bundle.program.expected_traffic],
                 "workload.json": bundle.plan, "oracle.json": bundle.oracle,
                 "manifest.json": {"status": "ok", "mshb_sha256": hashlib.sha256(blob).hexdigest(),
                                   "arch_digest": bundle.program.arch_digest.hex(),
                                   "workload_digest": bundle.plan["workload_digest"],
                                   "semantic_sha256": bundle.program.semantic_sha256()}}
    for name, document in documents.items():
        (destination / name).write_bytes(canonical_json_bytes(document))
    return documents["manifest.json"]
