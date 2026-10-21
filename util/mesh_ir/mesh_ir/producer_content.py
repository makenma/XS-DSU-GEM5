"""Admitted producer content evidence for local-source descriptors.

A descriptor's expected content follows the admitted source's real last
producer, not an earlier writer of the same object and not the descriptor's own
payload digest.  The producer publishes the physical byte digest of every
contiguous run its functional write covered (``compute_outputs[*].rows``) and
each local-source descriptor publishes the per-row source digests it actually
read (``descriptor_executions[*].source_rows``); this module pairs one producer
observation with one descriptor execution per instance/core/command/generation
and checks them run by run, using only admitted descriptor, operand, state,
allocation and geometry relations.
"""

from __future__ import annotations

import json
from pathlib import Path

from mesh_ir.abi.decoder import decode_program


class ProducerContentError(RuntimeError):
    pass


def _require(condition, message):
    if not condition:
        raise ProducerContentError(message)


def _load(program_dir):
    directory = Path(program_dir)
    sections = json.loads((directory / "schedule.mesh.json").read_text())["sections"]
    traffic = json.loads((directory / "expected_traffic.json").read_text())[
        "descriptors"
    ]
    program = decode_program((directory / "program.mshb").read_bytes())
    return sections, traffic, program


def _descriptor(sections, descriptor_id):
    matches = [
        row
        for row in sections["DMA_DESCRIPTORS"]
        if row["descriptor_id"] == descriptor_id
    ]
    _require(len(matches) == 1, "descriptor %d is not admitted" % descriptor_id)
    return matches[0]


def _command(sections, command_id):
    matches = [
        row for row in sections["COMMANDS"] if row["command_id"] == command_id
    ]
    _require(len(matches) == 1, "command %d is not admitted" % command_id)
    return matches[0]


def _operands(sections, command):
    return sections["COMMAND_OPERANDS"][
        command["operand_begin"] : command["operand_begin"] + command["operand_count"]
    ]


def _allocation(sections, allocation_id):
    matches = [
        row for row in sections["ALLOCATIONS"] if row["allocation_id"] == allocation_id
    ]
    _require(len(matches) == 1, "allocation %d is not admitted" % allocation_id)
    return matches[0]


def _semantic(program, collection, identity, field):
    for row in getattr(program.semantics, collection):
        if getattr(row, field) == identity:
            return row
    return None


def _write_object_byte_offset(program, operation):
    _require(operation.writes, "the producer operation has no write access")
    view = _semantic(program, "views", operation.writes[0].view_id, "view_id")
    _require(view is not None, "the producer write view is not admitted")
    shard = _semantic(program, "logical_shards", view.shard_id, "shard_id")
    _require(shard is not None, "the producer write shard is not admitted")
    tensor = _semantic(program, "kernel_tensors", shard.tensor_id, "tensor_id")
    _require(tensor is not None, "the producer write tensor is not admitted")
    return view.object_offset_elements * tensor.dtype.byte_width


def _producer_relation(sections, traffic, program, descriptor):
    command = _command(sections, descriptor["command_id"])
    operation = _semantic(
        program, "kernel_ops", command["source_op_id"], "op_id"
    )
    _require(operation is not None, "the descriptor command has no admitted operation")
    _require(
        len(operation.reads) == 1,
        "the source operation does not read exactly one access",
    )
    operands = _operands(sections, command)
    _require(operands, "the descriptor command has no admitted operands")
    source_allocation = operands[0]["allocation_id"]
    _require(source_allocation != 0, "the descriptor source is not a local allocation")
    state_id = operation.reads[0].state_id
    writers = [
        candidate
        for candidate in program.semantics.kernel_ops
        for transition in candidate.writes
        if transition.new_state_id == state_id
    ]
    _require(
        len(writers) == 1,
        "the admitted source state does not have exactly one producer",
    )
    producer_commands = [
        row["command_id"]
        for row in sections["COMMANDS"]
        if row["source_op_id"] == writers[0].op_id
    ]
    _require(
        len(producer_commands) == 1,
        "the admitted producer operation has no unique command",
    )
    traffic_rows = [
        row
        for row in traffic
        if row["identity"]["descriptor_id"] == descriptor["descriptor_id"]
    ]
    _require(len(traffic_rows) == 1, "the descriptor has no admitted traffic row")
    return {
        "command_id": command["command_id"],
        "source_allocation": source_allocation,
        "source_address": traffic_rows[0]["src_address"],
        "source_piece_offset": descriptor["src"]["offset_bytes"],
        "producer_op_id": writers[0].op_id,
        "producer_command_id": producer_commands[0],
        "producer_object_byte_offset": _write_object_byte_offset(program, writers[0]),
    }


def _merged_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _admitted_plan(result, core_id):
    matches = [row for row in result["cores"] if row["core_id"] == core_id]
    _require(len(matches) == 1, "core %d has no admitted dispatch plan" % core_id)
    return {(row[0], row[1]) for row in matches[0]["dispatch_plan"]}


def _instance_frames(result, descriptor, instance_id):
    instances = result.get("instances", [])
    _require(instances, "the result carries no instance ledger")
    if instance_id is not None:
        instances = [row for row in instances if row["instance"] == instance_id]
        _require(len(instances) == 1, "instance %s is missing" % instance_id)
    core_id = str(descriptor["owner_core"])
    frames = []
    for instance in instances:
        _require(
            core_id in instance["cores"],
            "instance %s has no ledger for core %s"
            % (instance["instance"], core_id),
        )
        frames.append(
            (instance["instance"], instance["cores"][core_id]["observations"])
        )
    return frames


def verify_transfer_producer_content(
    program_dir, result, descriptor_id, *, instance_id=None
):
    """Verify one local-source descriptor against its admitted producer.

    Every instance is checked on its own: one execution and one producer
    observation per instance, both on the admitted dispatch plan, with the
    same generation, the producer's output start at the admitted write offset,
    and every source row equal to exactly one producer byte run in the same
    physical allocation space.  Raises :class:`ProducerContentError` on a
    duplicate, a missing instance, a generation or identity mismatch, an
    address that does not project onto the admitted allocation, or a row whose
    bytes differ from its producer run.  Returns a machine-readable summary.
    """
    sections, traffic, program = _load(program_dir)
    descriptor = _descriptor(sections, descriptor_id)
    relation = _producer_relation(sections, traffic, program, descriptor)
    allocation = _allocation(sections, relation["source_allocation"])
    plan = _admitted_plan(result, descriptor["owner_core"])
    _require(
        any(command == descriptor["command_id"] for command, _ in plan),
        "the descriptor command is not on the admitted dispatch plan",
    )
    _require(
        any(
            command == relation["producer_command_id"] for command, _ in plan
        ),
        "the producer command is not on the admitted dispatch plan",
    )
    _require(
        descriptor["useful_bytes"] == descriptor["rows"] * descriptor["row_bytes"],
        "descriptor %d useful bytes do not cover every row"
        % descriptor["descriptor_id"],
    )
    # The transfer's absolute source rows and the producer's tile-relative rows
    # are projected into the admitted allocation's own space.
    absolute_allocation_base = (
        relation["source_address"] - relation["source_piece_offset"]
    )
    admitted_source_relative = relation["source_piece_offset"]
    summaries = []
    for number, frame in _instance_frames(result, descriptor, instance_id):
        executions = [
            row
            for row in frame["descriptor_executions"]
            if row["descriptor_id"] == descriptor["descriptor_id"]
        ]
        _require(
            len(executions) == 1,
            "instance %d has %d executions of descriptor %d"
            % (number, len(executions), descriptor["descriptor_id"]),
        )
        execution = executions[0]
        identity = (execution["command_id"], execution["generation"])
        _require(
            identity in plan,
            "instance %d descriptor %d execution %s is not on the admitted "
            "dispatch plan" % (number, descriptor["descriptor_id"], (identity,)),
        )
        producers = [
            row
            for row in frame["compute_outputs"]
            if row["command_id"] == relation["producer_command_id"]
            and row["allocation_id"] == relation["source_allocation"]
        ]
        _require(
            len(producers) == 1,
            "instance %d has %d producer observations for command %d allocation %d"
            % (
                number,
                len(producers),
                relation["producer_command_id"],
                relation["source_allocation"],
            ),
        )
        producer = producers[0]
        _require(
            (relation["producer_command_id"], producer["generation"]) in plan,
            "instance %d producer generation %d is not on the admitted dispatch "
            "plan" % (number, producer["generation"]),
        )
        _require(
            producer["generation"] == execution["generation"],
            "instance %d producer generation %d differs from the execution "
            "generation %d"
            % (number, producer["generation"], execution["generation"]),
        )
        _require(
            producer["offset"] - allocation["offset_bytes"]
            == relation["producer_object_byte_offset"],
            "instance %d producer output starts at allocation offset %d instead "
            "of the admitted %d"
            % (
                number,
                producer["offset"] - allocation["offset_bytes"],
                relation["producer_object_byte_offset"],
            ),
        )
        _require(
            execution["committed"] and execution["status"] == "OK",
            "instance %d descriptor %d execution did not commit"
            % (number, descriptor["descriptor_id"]),
        )
        # The producer publishes the write access's physically contiguous byte
        # runs and, where adjacent traversals merge, the maximal contiguous
        # runs.  A source row is verified against the exact run that covers the
        # same physical bytes, so neither side may claim undigested bytes.
        fine = {}
        for run in producer["rows"]:
            relative = run["address"] - allocation["offset_bytes"]
            _require(
                relative not in fine,
                "instance %d producer repeats the byte run at allocation offset "
                "%d" % (number, relative),
            )
            fine[relative] = run
        _require(fine, "instance %d producer published no byte runs" % number)
        covering = {}
        for run in list(producer["rows"]) + list(producer.get("merged_rows", [])):
            relative = run["address"] - allocation["offset_bytes"]
            covering.setdefault(relative, []).append(run)
        rows = execution["source_rows"]
        _require(
            len(rows) == descriptor["rows"],
            "instance %d descriptor %d execution published %d of %d source rows"
            % (number, descriptor["descriptor_id"], len(rows), descriptor["rows"]),
        )
        matched = set()
        for index, row in enumerate(rows):
            relative = row["address"] - absolute_allocation_base
            _require(
                relative
                == admitted_source_relative + index * descriptor["src_stride_bytes"],
                "instance %d source row %d is not at its admitted allocation "
                "offset" % (number, index),
            )
            _require(
                row["size"] == descriptor["row_bytes"],
                "instance %d source row %d has size %d"
                % (number, index, row["size"]),
            )
            _require(
                row["digest"],
                "instance %d source row %d has no digest" % (number, index),
            )
            options = [
                run
                for run in covering.get(relative, [])
                if run["size"] == row["size"]
            ]
            _require(
                len(options) == 1,
                "instance %d has no unique producer byte run covering the "
                "source row %d bytes" % (number, index),
            )
            _require(
                options[0]["digest"] == row["digest"],
                "instance %d source row %d content differs from its producer "
                "byte run" % (number, index),
            )
            matched.add(relative)
        ordered = sorted(matched)
        lowest = ordered[0]
        highest = ordered[-1] + descriptor["row_bytes"]
        # The source rows and the producer's physically contiguous byte runs
        # must cover exactly the same bytes: neither may claim bytes it did not
        # digest, and neither may leave a gap.
        row_intervals = _merged_intervals(
            (relative, relative + descriptor["row_bytes"]) for relative in matched
        )
        run_intervals = _merged_intervals(
            (relative, relative + run["size"])
            for relative, run in fine.items()
            if relative < highest and relative + run["size"] > lowest
        )
        _require(
            row_intervals == run_intervals,
            "instance %d source rows %s do not cover the same bytes as the "
            "producer's runs %s" % (number, row_intervals, run_intervals),
        )
        summaries.append(
            {
                "instance": number,
                "generation": execution["generation"],
                "producer_generation": producer["generation"],
                "producer_offset": producer["offset"],
                "rows": [
                    {
                        "address": row["address"],
                        "size": row["size"],
                        "digest": row["digest"],
                    }
                    for row in rows
                ],
            }
        )
    return {
        "descriptor_id": descriptor["descriptor_id"],
        "command_id": relation["command_id"],
        "producer_command_id": relation["producer_command_id"],
        "producer_op_id": relation["producer_op_id"],
        "allocation_id": relation["source_allocation"],
        "allocation_offset_bytes": allocation["offset_bytes"],
        "absolute_allocation_base": absolute_allocation_base,
        "source_address": relation["source_address"],
        "source_piece_offset": relation["source_piece_offset"],
        "admitted_producer_object_byte_offset": relation[
            "producer_object_byte_offset"
        ],
        "source_stride_bytes": descriptor["src_stride_bytes"],
        "row_bytes": descriptor["row_bytes"],
        "instances": summaries,
    }
