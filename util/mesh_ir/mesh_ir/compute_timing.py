"""Admitted compute timing: the published engine cost model of one program.

The core schedules a compute command from its admitted attribute (shape, dtype,
element count, fan-in) and the admission dtypes, so the analytic engine latency
is a pure function of the admitted work and the architecture.  This module is the
single definition of that function for the offline side: the runner and the tests
compare the archived engine plan against it instead of against a recorded
constant.
"""

from __future__ import annotations

import json
from pathlib import Path

from mesh_ir.abi.decoder import decode_program

DTYPE_ORDER = ("fp32", "fp16", "bf16", "int8", "int32")

ENGINE_BY_OPCODE = {
    "GEMM": "tensor",
    "BMM": "tensor",
    "ELEMENTWISE": "vector",
    "SOFTMAX": "vector",
    "NORM": "vector",
    "LOCAL_REDUCE": "reduce",
}

ATTR_BY_OPCODE = {
    "GEMM": "GEMM_V1",
    "BMM": "BMM_V1",
    "ELEMENTWISE": "ELEMENTWISE_V1",
    "SOFTMAX": "SOFTMAX_V1",
    "NORM": "NORM_V1",
    "LOCAL_REDUCE": "REDUCE_V1",
}


class ComputeTimingError(RuntimeError):
    pass


def _require(condition, message):
    if not condition:
        raise ComputeTimingError(message)


def _load(program_dir):
    directory = Path(program_dir)
    sections = json.loads((directory / "schedule.mesh.json").read_text())["sections"]
    program = decode_program((directory / "program.mshb").read_bytes())
    return sections, program


def _opcode_table():
    from mesh_ir.generated import abi

    return {name: int(getattr(abi.OPCODE, name)) for name in ENGINE_BY_OPCODE}


def _attr_kind_table():
    from mesh_ir.generated import abi

    return {
        name: int(getattr(abi.ATTR_KIND, name))
        for name in sorted(set(ATTR_BY_OPCODE.values()))
    }


def _throughput(dtype, by_dtype, fallback):
    if 1 <= dtype <= len(DTYPE_ORDER) and by_dtype[dtype - 1] > 0:
        return by_dtype[dtype - 1]
    return fallback


def _ceil_div(numerator, denominator):
    return (numerator + denominator - 1) // denominator


def _by_dtype(table):
    return [table.get(name, 0) for name in DTYPE_ORDER]


def compute_cycles(arch, opcode, attr):
    """The admitted analytic cycles of one compute command.

    ``opcode`` is the ABI opcode name, ``attr`` the command's decoded attribute.
    Mirrors the core's own model: the tensor engine charges setup, the
    efficiency-scaled MAC work and the pipeline flush; elementwise charges lanes
    only; softmax and norm charge the fixed vector width; reduce charges setup,
    the fan-in work by dtype and its flush.
    """
    fields = attr.payload_dict()
    if opcode in ("GEMM", "BMM"):
        work = fields["batch"] * fields["m"] * fields["n"] * fields["k"]
        macs = _throughput(
            fields["dtype"], _by_dtype(arch.tensor_macs_per_cycle),
            arch.tensor_macs_per_cycle["fp16"],
        )
        _require(macs > 0, "E_CAPABILITY_MISMATCH: no tensor throughput")
        engine = _ceil_div(
            work * 65536, macs * fields["efficiency_q16"]
        )
        return arch.tensor_setup_cycles + engine + arch.tensor_pipeline_flush_cycles
    if opcode == "ELEMENTWISE":
        lanes = _throughput(
            fields["dtype"], _by_dtype(arch.vector_elements_per_cycle),
            arch.vector_elements_per_cycle["fp16"],
        )
        _require(lanes > 0, "E_CAPABILITY_MISMATCH: no vector throughput")
        return _ceil_div(
            fields["element_count"] * fields["ops_per_element"], lanes
        )
    if opcode == "SOFTMAX":
        return _ceil_div(
            2 * fields["axis_size"], arch.vector_elements_per_cycle["fp16"]
        )
    if opcode == "NORM":
        return _ceil_div(
            2 * fields["element_count"], arch.vector_elements_per_cycle["fp16"]
        )
    if opcode == "LOCAL_REDUCE":
        ops_per_cycle = _throughput(
            fields["dtype"], _by_dtype(arch.reduce_ops_per_cycle),
            arch.reduce_ops_per_cycle["fp16"],
        )
        ops = fields["element_count"] * (fields["fan_in"] - 1)
        _require(
            fields["fan_in"] >= 2 and ops > 0 and ops_per_cycle > 0,
            "E_CAPABILITY_MISMATCH: reduce workload is not admitted",
        )
        return (
            arch.reduce_setup_cycles
            + _ceil_div(ops, ops_per_cycle)
            + arch.reduce_flush_cycles
        )
    return 1


def admitted_engine_plan(program_dir, arch):
    """{core_id: {command_id: {"opcode", "engine", "cycles"}}} of admitted work.

    Only commands that really occupy an engine appear; control and DMA opcodes
    are not part of the admitted compute plan.
    """
    sections, program = _load(program_dir)
    opcodes = _opcode_table()
    attr_kinds = _attr_kind_table()
    plan = {}
    for command in program.commands:
        name = next(
            (key for key, value in opcodes.items() if value == command.opcode),
            None,
        )
        if name is None or name not in ENGINE_BY_OPCODE:
            continue
        _require(
            command.attr_index > 0,
            "compute command %d has no admitted attribute" % command.command_id,
        )
        attr = program.op_attrs[command.attr_index - 1]
        _require(
            attr.kind == attr_kinds[ATTR_BY_OPCODE[name]],
            "compute command %d carries attribute kind %s instead of %s"
            % (command.command_id, attr.kind, attr_kinds[ATTR_BY_OPCODE[name]]),
        )
        plan.setdefault(command.core_id, {})[command.command_id] = {
            "opcode": name,
            "engine": ENGINE_BY_OPCODE[name],
            "cycles": compute_cycles(arch, name, attr),
        }
    _require(plan, "the admitted program has no compute command")
    return plan


def verify_engine_timing(program_dir, arch, result, instances):
    """Every admitted compute command really ran for its admitted cycles.

    ``result`` is one archived run: the engine plan of each instance frame must
    carry exactly one execution per admitted compute command and generation, the
    analytic cycle count must equal the admitted model, the execution must have
    ended exactly at the tick the model predicts from the command's own issue
    tick and the core's clock period, and the per-core cumulative engine cycle
    statistics must be the admitted sum over every instance.
    """
    _require(isinstance(instances, int) and instances >= 1,
             "instances must be positive")
    period = result.get("core_clock_period_ticks")
    _require(isinstance(period, int) and period > 0,
             "the result does not carry the core clock period")
    plan = admitted_engine_plan(program_dir, arch)
    frames = result.get("instances") or []
    _require(len(frames) == instances,
             "result carries %d instances but %d were requested"
             % (len(frames), instances))
    checked = 0
    totals = {}
    for frame in frames:
        for core_id, commands in sorted(plan.items()):
            ledger = frame["cores"].get(str(core_id))
            _require(ledger is not None,
                     "instance %s has no core %d ledger" % (frame["instance"], core_id))
            executions = {
                (row["command_id"], row["generation"]): row
                for row in ledger["observations"]["engine_executions"]
                if row["command_id"] in commands
            }
            _require(
                len(executions) == len(commands),
                "instance %s core %d archived %d of %d admitted engine plans"
                % (frame["instance"], core_id, len(executions), len(commands)),
            )
            issue_ticks = {
                (row["command_id"], row["generation"]): row["issue_tick"]
                for row in ledger["observations"]["commands"]
            }
            for command_id, admitted in sorted(commands.items()):
                key = (command_id, 0)
                row = executions[key]
                _require(
                    row["engine"] == admitted["engine"],
                    "command %d ran on the %s engine instead of the admitted %s"
                    % (command_id, row["engine"], admitted["engine"]),
                )
                _require(
                    row["ended"] is True,
                    "command %d never ended its engine execution" % command_id,
                )
                _require(
                    row["cycles"] == admitted["cycles"],
                    "command %d ran %s cycles, the admitted %s work is %d"
                    % (command_id, row["cycles"], admitted["opcode"],
                       admitted["cycles"]),
                )
                issue_tick = issue_ticks.get(key)
                _require(
                    issue_tick is not None,
                    "command %d has no admitted issue tick" % command_id,
                )
                expected_end = (
                    row["begin_tick"] + period * admitted["cycles"] + 1
                    - issue_tick % period
                )
                _require(
                    row["scheduled_end_tick"] == expected_end
                    and row["end_tick"] == expected_end,
                    "command %d was planned to end at %s and ended at %s, the "
                    "admitted plan is %s"
                    % (command_id, row["scheduled_end_tick"], row["end_tick"],
                       expected_end),
                )
                stats = totals.setdefault((core_id, admitted["engine"]), 0)
                totals[(core_id, admitted["engine"])] = stats + admitted["cycles"]
                checked += 1
    for core in result.get("cores", []):
        core_id = core["core_id"]
        _require(
            core.get("gemm_cycles") == totals.get((core_id, "tensor"), 0),
            "core %s reports %s tensor cycles, the admitted plan over %s "
            "instances is %s"
            % (core_id, core.get("gemm_cycles"), instances,
               totals.get((core_id, "tensor"), 0)),
        )
        _require(
            core.get("reduce_cycles") == totals.get((core_id, "reduce"), 0),
            "core %s reports %s reduce cycles, the admitted plan over %s "
            "instances is %s"
            % (core_id, core.get("reduce_cycles"), instances,
               totals.get((core_id, "reduce"), 0)),
        )
    return {
        "commands": checked,
        "tensor_cycles": sum(
            value for (core_id, engine), value in totals.items() if engine == "tensor"
        ),
        "reduce_cycles": sum(
            value for (core_id, engine), value in totals.items() if engine == "reduce"
        ),
    }
