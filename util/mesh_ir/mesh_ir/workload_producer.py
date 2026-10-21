"""Admitted write-access and current-version proofs for workload source bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mesh_ir.abi.decoder import decode_program
from mesh_ir.architecture import load_arch
from mesh_ir.analysis.regions import ByteSpan, merge_spans, view_access_byte_spans
from mesh_ir.generated import abi as A
from mesh_ir.runtime_reconciliation import _require

CONSUMER_KINDS = (2, 3)
DMA_WRITER_KINDS = (1, 3, 4)
COMPUTE_OPCODES = tuple(int(getattr(A.OPCODE, name)) for name in (
    "GEMM", "BMM", "ELEMENTWISE", "SOFTMAX", "NORM", "LOCAL_REDUCE"))
WRITE_OPERAND_ACCESS = 2


def _load_program(program_dir):
    program = decode_program(
        (Path(program_dir) / "program.mshb").read_bytes())
    sections = json.loads(
        (Path(program_dir) / "schedule.mesh.json").read_text())["sections"]
    traffic = json.loads(
        (Path(program_dir) / "expected_traffic.json").read_text())["descriptors"]
    return program, sections, traffic


def _sram_geometry(arch_manifest):
    for region in arch_manifest.regions:
        if region.kind == "CORE_SRAM_APERTURE":
            return region.base, region.tile_stride
    raise ValueError("arch declares no CORE_SRAM_APERTURE region")


def _row_bytes(row):
    raw = row.get("bytes_hex")
    if not isinstance(raw, str) or len(raw) != 2 * row["size"]:
        return None
    return bytes.fromhex(raw)


def _fact(row, address, label):
    """One archived run as ``(address, size, digest, bytes)``; the raw
    bytes, when the archive carries them, must hash to the run's digest,
    so a digest borrowed from another run cannot stand in."""
    data = _row_bytes(row)
    if data is not None:
        _require(hashlib.sha256(data).hexdigest() == row["digest"],
                 "%s run at 0x%x carries bytes that do not match its digest"
                 % (label, address))
    return (address, row["size"], row["digest"], data)


def _row_is_produced(row, pool, read_tick):
    begin, end = row["address"], row["address"] + row["size"]
    data = _row_bytes(row)
    eligible = [entry for entry in pool
                if entry[0] < end and begin < entry[0] + entry[1]
                and entry[4] <= read_tick]
    boundaries = sorted({begin, end} | {
        point for entry in eligible for point in (entry[0], entry[0] + entry[1])
        if begin < point < end})
    used = []
    for start, stop in zip(boundaries, boundaries[1:]):
        covering = [entry for entry in eligible
                    if entry[0] <= start and stop <= entry[0] + entry[1]]
        if not covering:
            return None
        frontier = max(entry[4] for entry in covering)
        latest = [entry for entry in covering if entry[4] == frontier]
        if len(latest) != 1:
            return None
        entry = latest[0]
        address, size, digest, run_data = entry[:4]
        if run_data is None or data is None:
            if (address != begin or address + size != end
                    or digest != row["digest"]):
                return None
        elif run_data[start - address:stop - address] != data[start - begin:stop - begin]:
            return None
        used.append((entry, stop - start))
    return used


def verified_workload_producer_chain(program_dir, result, arch_path):
    """Every local source row of a real workload run has an admitted
    producer at its current data version."""
    program, sections, traffic = _load_program(program_dir)
    arch_manifest = load_arch(arch_path)
    sram_base, tile_stride = _sram_geometry(arch_manifest)
    allocations = {
        row["allocation_id"]: row for row in sections["ALLOCATIONS"]
    }
    commands = {row["command_id"]: row for row in sections["COMMANDS"]}
    descriptors = {
        row["descriptor_id"]: row for row in sections["DMA_DESCRIPTORS"]
    }
    operands = sections["COMMAND_OPERANDS"]
    semantics = program.semantics
    views = {item.view_id: item for item in semantics.views}
    objects = {item.object_id: item for item in semantics.objects}
    tensors = {item.tensor_id: item for item in semantics.kernel_tensors}
    shards = {item.shard_id: item for item in semantics.logical_shards}
    operations = {item.op_id: item for item in semantics.kernel_ops}
    backings = {item.object_id: item.backing for item in semantics.object_backings}
    write_geometry = {}
    write_origins = {}
    for command in commands.values():
        if command["opcode"] not in COMPUTE_OPCODES:
            continue
        operation = operations[command["source_op_id"]]
        spans = []
        for access in operation.writes:
            allocation = allocations[backings[views[access.view_id].object_id].allocation_id]
            spans.extend(ByteSpan(allocation["offset_bytes"] + span.begin,
                                  allocation["offset_bytes"] + span.end)
                         for span in view_access_byte_spans(access, views, objects, tensors, shards))
        write_geometry[command["command_id"]] = merge_spans(tuple(spans))
        write_origins[command["command_id"]] = spans[0].begin if spans else None
    traffic_rows = {
        row["identity"]["descriptor_id"]: row for row in traffic
    }
    _require(allocations and commands and descriptors and traffic_rows,
             "the admitted program is missing ownership tables")

    def write_allocations_of(command_row):
        window = operands[command_row["operand_begin"]:
                          command_row["operand_begin"]
                          + command_row["operand_count"]]
        return {entry["allocation_id"] for entry in window
                if entry["access"] == WRITE_OPERAND_ACCESS}

    waits_of = {}
    for command in program.commands:
        waits_of[command.command_id] = frozenset(
            program.command_waits[command.wait_begin + i].event_id
            for i in range(command.wait_count)) if command.wait_count \
            else frozenset()
    signaler_of = {event.event_id: event.producer_command_id
                   for event in program.events}

    consumed = 0
    uncovered = []
    edges = {}
    for frame in result.get("instances", []):
        facts = {}
        peer_commits = {}
        for aperture in frame.get("apertures", ()):
            for transfer in aperture["transfers"]:
                key = (int(aperture["core_id"]), transfer["transfer_id"])
                _require(key not in peer_commits, "duplicate peer transfer commit identity")
                peer_commits[key] = transfer["commit_tick"]
        seen_outputs = set()
        seen_executions = set()
        for core_id, ledger in sorted(frame["cores"].items()):
            observations = ledger["observations"]
            engine_ends = {}
            issued = set()
            for row in observations["commands"]:
                if row["issued"] and row["terminal"]:
                    issued.add((row["command_id"], row.get("generation", 0)))
            for row in observations["engine_executions"]:
                if row["ended"]:
                    engine_ends[(row["command_id"], row.get("generation", 0))] = \
                        row["end_tick"]
            for output in observations.get("compute_outputs", ()):
                rows = output.get("rows", ())
                if not rows:
                    _require(not write_geometry.get(output["command_id"]),
                             "compute command %s omits its admitted write access"
                             % output["command_id"])
                    continue
                key = (output["command_id"], output.get("generation", 0))
                _require(key not in seen_outputs,
                         "command %s archives a second generation-%s "
                         "compute output in instance %s"
                         % (output["command_id"], key[1],
                            frame.get("instance")))
                seen_outputs.add(key)
                command = commands.get(output["command_id"])
                _require(command is not None,
                         "compute output names command %s, which the "
                         "admitted program does not declare"
                         % output["command_id"])
                _require(command["opcode"] in COMPUTE_OPCODES,
                         "compute output names command %s of opcode %s, "
                         "which cannot write compute bytes"
                         % (output["command_id"], command["opcode"]))
                _require(write_allocations_of(command)
                         & {output["allocation_id"]},
                         "compute output of command %s claims allocation "
                         "%s, which is not one of its admitted write "
                         "operands" % (output["command_id"],
                                       output["allocation_id"]))
                allocation = allocations.get(output["allocation_id"])
                _require(allocation is not None,
                         "compute output of command %s names allocation %s, "
                         "which the admitted program does not declare"
                         % (output["command_id"], output["allocation_id"]))
                _require(allocation["owner_core"] == int(core_id),
                         "compute output of command %s names allocation %s "
                         "of core %s inside core %s's ledger"
                         % (output["command_id"], output["allocation_id"],
                            allocation["owner_core"], core_id))
                extent = (allocation["offset_bytes"],
                          allocation["offset_bytes"] + allocation["size_bytes"])
                for row in rows:
                    _require(extent[0] <= row["address"]
                             and row["address"] + row["size"] <= extent[1],
                             "compute output of command %s writes at 0x%x, "
                             "outside its admitted allocation extent %s"
                             % (output["command_id"], row["address"],
                                (hex(extent[0]), hex(extent[1]))))
                _require(output["offset"] == rows[0]["address"],
                         "compute output of command %s declares offset "
                         "%s but first writes at 0x%x"
                         % (output["command_id"], output["offset"],
                            rows[0]["address"]))
                _require(output["offset"] == write_origins[output["command_id"]],
                         "compute command %s offset does not match its admitted write access"
                         % output["command_id"])
                observed_spans = tuple(ByteSpan(row["address"], row["address"] + row["size"])
                                       for row in rows)
                admitted_spans = write_geometry[output["command_id"]]
                _require(all(row["size"] > 0 for row in rows)
                         and merge_spans(observed_spans) == admitted_spans
                         and sum(row["size"] for row in rows)
                         == sum(span.end - span.begin for span in admitted_spans),
                         "compute command %s rows do not exactly cover its admitted write access"
                         % output["command_id"])
                _require(key in issued and key in engine_ends,
                         "compute output of command %s has no issued, "
                         "ended engine execution in its own frame"
                         % output["command_id"])
                tick = engine_ends[key]
                base = sram_base + allocation["owner_core"] * tile_stride
                bucket = facts.setdefault(output["command_id"], {})
                generation_bucket = bucket.setdefault(key[1], [])
                for row in rows:
                    generation_bucket.append((*_fact(
                        row, base + row["address"],
                        "compute output of command %s"
                        % output["command_id"]), tick,
                        output["command_id"]))
            for execution in observations["descriptor_executions"]:
                mark = (execution["descriptor_id"],
                        execution.get("generation", 0))
                _require(
                    mark not in seen_executions,
                    "descriptor %s archives a second generation-%s execution "
                    "in instance %s"
                    % (execution["descriptor_id"], mark[1],
                       frame.get("instance")))
                seen_executions.add(mark)
                descriptor = descriptors.get(execution["descriptor_id"])
                if descriptor is None \
                        or descriptor["kind"] not in DMA_WRITER_KINDS \
                        or not execution.get("source_rows"):
                    continue
                traffic = traffic_rows.get(execution["descriptor_id"])
                _require(traffic is not None,
                         "DMA descriptor %s has no admitted traffic row"
                         % execution["descriptor_id"])
                command = commands.get(descriptor["command_id"])
                _require(command is not None,
                         "DMA descriptor %s names command %s, which the "
                         "admitted program does not declare"
                         % (execution["descriptor_id"],
                            descriptor["command_id"]))
                landing_allocations = {
                    allocation_id for allocation_id
                    in write_allocations_of(command)
                    if allocation_id in allocations}
                generation = execution.get("generation", 0)
                tick = (peer_commits.get((descriptor["dst"]["owner_core"], descriptor["transfer_id"]))
                        if descriptor["kind"] == 3 else execution.get("commit_tick"))
                _require(tick is not None and tick > 0,
                         "DMA execution of descriptor %s archives no "
                         "commit or completion tick"
                         % execution["descriptor_id"])
                bucket = facts.setdefault(descriptor["command_id"], {})
                generation_bucket = bucket.setdefault(generation, [])
                for index, row in enumerate(execution["source_rows"]):
                    landed = (traffic["dst_address"]
                              + index * descriptor["dst_stride_bytes"])
                    _require(
                        any(sram_base
                            + allocations[a]["owner_core"] * tile_stride
                            + allocations[a]["offset_bytes"] <= landed
                            and landed + row["size"]
                            <= sram_base
                            + allocations[a]["owner_core"] * tile_stride
                            + allocations[a]["offset_bytes"]
                            + allocations[a]["size_bytes"]
                            for a in landing_allocations),
                        "DMA descriptor %s lands at 0x%x outside every "
                        "admitted write allocation of command %s"
                        % (execution["descriptor_id"], landed,
                           descriptor["command_id"]))
                    generation_bucket.append((*_fact(
                        row, landed,
                        "source row of descriptor %s"
                        % execution["descriptor_id"]), tick,
                        descriptor["command_id"]))
        for core_id, ledger in sorted(frame["cores"].items()):
            observations = ledger["observations"]
            terminals = {(row["command_id"], row.get("generation", 0)):
                         row["terminal_tick"]
                         for row in observations["commands"]
                         if row["issued"] and row["terminal"]}
            for execution in observations["descriptor_executions"]:
                descriptor = descriptors.get(execution["descriptor_id"])
                if descriptor is None \
                        or descriptor["kind"] not in CONSUMER_KINDS:
                    continue
                rows = execution.get("source_rows") or []
                if descriptor["rows"] > 0 and descriptor["row_bytes"] > 0:
                    _require(
                        rows,
                        "descriptor %s of command %s read admitted nonzero "
                        "bytes but archives no source rows"
                        % (execution["descriptor_id"],
                           descriptor["command_id"]))
                if not rows:
                    continue
                _require(
                    len(rows) == descriptor["rows"]
                    and all(row["size"] == descriptor["row_bytes"]
                            for row in rows),
                    "descriptor %s archives %s source rows of widths %s, its "
                    "admitted geometry is %s rows of %s bytes"
                    % (execution["descriptor_id"], len(rows),
                       sorted({row["size"] for row in rows}),
                       descriptor["rows"], descriptor["row_bytes"]))
                traffic = traffic_rows.get(execution["descriptor_id"])
                _require(traffic is not None,
                         "consumer descriptor %s has no admitted traffic row"
                         % execution["descriptor_id"])
                for index, row in enumerate(rows):
                    _fact(row, row["address"],
                          "consumer source row of descriptor %s"
                          % execution["descriptor_id"])
                    expected = (traffic["src_address"]
                                + index * descriptor["src_stride_bytes"])
                    _require(row["address"] == expected,
                             "descriptor %s source row %d reads 0x%x, its "
                             "admitted endpoint is 0x%x"
                             % (execution["descriptor_id"], index,
                                row["address"], expected))
                read_tick = execution.get("commit_tick")
                _require(read_tick is not None and read_tick > 0,
                         "consumer descriptor %s archives no source read commit tick"
                         % execution["descriptor_id"])
                generation = execution.get("generation", 0)
                _require(
                    (descriptor["command_id"], generation) in terminals,
                    "descriptor %s claims generation %s of command %s, which "
                    "the frame's command lifecycle never issued"
                    % (execution["descriptor_id"], generation,
                       descriptor["command_id"]))
                command = descriptor["command_id"]
                producers = sorted(
                    {signaler_of.get(event)
                     for event in waits_of.get(command, ())}
                    - {None, command})
                pool = [entry for producer in producers
                        for entry in facts.get(producer, {}).get(generation, ())]
                for row in rows:
                    consumed += 1
                    used = _row_is_produced(row, pool, read_tick)
                    if used is None:
                        uncovered.append({
                            "descriptor": execution["descriptor_id"],
                            "command": command,
                            "address": row["address"],
                            "size": row["size"],
                            "digest": row["digest"],
                            "admitted_producers": producers,
                        })
                        continue
                    for entry, byte_count in used:
                        edge = edges.setdefault(str(entry[5]), 0)
                        edges[str(entry[5])] = edge + byte_count
    _require(
        not uncovered,
        "%s of %s consumed source rows have no admitted producer; first: %s"
        % (len(uncovered), consumed, uncovered[:3]),
    )
    return {"consumed_rows": consumed, "uncovered": len(uncovered),
            "producer_edges_bytes": edges,
            "sha256": hashlib.sha256(
                json.dumps(sorted(
                    (row["descriptor"], row["address"], row["size"],
                     row["digest"]) for row in uncovered
                )).encode()).hexdigest()}
