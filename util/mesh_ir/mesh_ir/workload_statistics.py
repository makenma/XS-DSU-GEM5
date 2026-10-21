from collections import Counter

from mesh_ir.execution_view import ProfileExecutionView
from mesh_ir.generated import abi as A
from mesh_ir.runtime_reconciliation import _merged_intervals, _require
from mesh_ir.runtime_dma_timing import verified_dma_timing_phases

_ENGINE_NAMES = ("tensor", "vector", "reduce")
_LATENCY_BUCKETS = (100, 500, 1000, 5000, 20000, 100000)


def _histogram(values):
    counts = Counter()
    for value in values:
        for edge in _LATENCY_BUCKETS:
            if value < edge:
                counts[str(edge)] += 1
                break
        else:
            counts["inf"] += 1
    return dict(sorted(counts.items(),
                       key=lambda item: float(item[0] if item[0] != "inf"
                                             else "inf")))


def _core_engine_busy(observations):
    intervals = []
    for row in observations["engine_executions"]:
        if not row["ended"]:
            continue
        _require(row["begin_tick"] <= row["end_tick"],
                 "engine execution of command %s ends before it begins"
                 % row["command_id"])
        intervals.append((row["begin_tick"], row["end_tick"]))
    return _merged_intervals(intervals)


def _communication_exposure(observations, commands, busy):
    """Waiting spans of RECV_WAIT commands split at engine busy intervals:
    the exposed part could not overlap compute on the core, the rest did."""
    exposed = overlap = 0
    for row in observations["commands"]:
        if not (row["issued"] and row["terminal"]):
            # Not yet issued, or cancelled mid-flight: no measurable span.
            continue
        _require(row["issue_tick"] <= row["terminal_tick"],
                 "command %s terminates before it is issued"
                 % row["command_id"])
        if commands[row["command_id"]]["opcode"] != A.OPCODE.RECV_WAIT:
            continue
        for start, stop in _merged_intervals(
                [(row["issue_tick"], row["terminal_tick"])]):
            covered = sum(max(0, min(stop, busy_stop) - max(start, busy_start))
                          for busy_start, busy_stop in busy)
            overlap += covered
            exposed += (stop - start) - covered
    return exposed, overlap


def _engine_stall_ticks(observations):
    ticks = 0
    for row in observations.get("engine_blocks", ()):
        _require(row["last_reject_tick"] >= row["first_reject_tick"],
                 "engine stall episode of command %s closes before it opens"
                 % row["command"]["command_id"])
        ticks += row["last_reject_tick"] - row["first_reject_tick"]
    return ticks


def _wait_stall_ticks(observations):
    ticks = 0
    for row in observations.get("wait_stalls", ()):
        _require(row["last_tick"] >= row["first_tick"],
                 "wait stall of command %s closes before it opens"
                 % row["command_id"])
        ticks += row["last_tick"] - row["first_tick"]
    return ticks


def _response_errors(result, core_id, observations):
    """Non-OK DMA responses of one core; each must belong to a command the
    same ledger terminated as ERROR, so a disguised error run cannot hide
    behind rewritten command terminals."""
    core = next((row for row in result["cores"]
                 if int(row["core_id"]) == int(core_id)), None)
    _require(core is not None, "core %s archives no lifecycle row" % core_id)
    errored = set(core["errored_command_ids"])
    errors = 0
    for row in observations["descriptor_executions"]:
        status = row.get("status")
        _require(status is not None,
                 "descriptor %s archives no response status"
                 % row["descriptor_id"])
        if status == "OK":
            continue
        _require(row["command_id"] in errored,
                 "descriptor %s of command %s archived status %s while its "
                 "command is not terminal-ERROR"
                 % (row["descriptor_id"], row["command_id"], status))
        errors += 1
    return errors


def _peak_allocation_bytes(sections, core_id):
    """The SRAM planner's high-water semantics: the largest allocation end
    of the core, exactly ``analysis/sram.py::SramCoreReport.peak_bytes``
    over the same admitted allocations."""
    return max((row["offset_bytes"] + row["size_bytes"]
                for row in sections["ALLOCATIONS"]
                if row["owner_core"] == int(core_id)
                and row["memory_space"] == int(A.MEMORY_SPACE.CORE_SRAM)),
               default=0)


def _compute_prediction(program_dir, arch, frames, selections):
    """Admitted analytic cycles against the archived engine executions, per
    core and engine; the model stays owned by ``compute_timing``."""
    from mesh_ir.compute_timing import admitted_engine_plan
    plan = admitted_engine_plan(program_dir, arch)
    report = {}
    for frame, selected in zip(frames, selections, strict=True):
        for core in frame["cores"]:
            predicted = {"tensor": 0, "vector": 0, "reduce": 0}
            for command_id, row in plan.get(int(core["core_id"]), {}).items():
                if command_id in selected:
                    predicted[row["engine"]] += row["cycles"]
            actual = {engine: core["engines"][engine]["work_cycles"]
                      for engine in _ENGINE_NAMES}
            report["instance:%s/core:%s" % (frame["instance"], core["core_id"])] = {
                "predicted_cycles": predicted, "actual_cycles": actual,
                "matches": predicted == actual}
    return report


def _dma_activity(observations, bursts, timing_rows, transfer_count):
    """Per-instance, per-core DMA counters: executions of this frame's
    ledger, bursts of this frame, and the timing rows joined to this
    frame's completed executions."""
    descriptors = len(observations["descriptor_executions"])
    core_bursts = [row for row in bursts
                   if int(row["core_id"]) == int(observations["core_id"])]
    reads = [(row["ar_aw_tick"], row["response_tick"]) for row in core_bursts
             if row["channel"] == "AR" and row["response_tick"] is not None]
    writes = [(row["ar_aw_tick"], row["response_tick"]) for row in core_bursts
              if row["channel"] == "AW" and row["response_tick"] is not None]
    in_flight_reads = [(row["first_ar_tick"], row["done_tick"])
                       for row in timing_rows if row["first_ar_tick"]]
    in_flight_writes = [(row["first_aw_tick"], row["done_tick"])
                        for row in timing_rows if row["first_aw_tick"]]
    return {"descriptors": descriptors,
            "transfers": transfer_count,
            "bursts": len(core_bursts),
            "beat_total": sum(row["beats"] for row in core_bursts),
            "bus_bytes": sum(row["beats"] * row["beat_bytes"]
                             for row in core_bursts),
            "peak_read_outstanding": _peak_overlap(reads),
            "peak_write_outstanding": _peak_overlap(writes),
            "peak_in_flight_read_descriptors": _peak_overlap(in_flight_reads),
            "peak_in_flight_write_descriptors": _peak_overlap(in_flight_writes)}


def _dma_latencies(entries):
    queue, service = [], []
    for row, execution in entries:
        submit = execution["submit_tick"]
        if row["first_ar_tick"]:
            _require(row["first_ar_tick"] >= submit,
                     "descriptor %s issues AR before submit"
                     % row["descriptor_id"])
            queue.append(row["first_ar_tick"] - submit)
            service.append(row["done_tick"] - row["first_ar_tick"])
        if row["first_aw_tick"]:
            _require(row["first_aw_tick"] >= submit,
                     "descriptor %s issues AW before submit"
                     % row["descriptor_id"])
            queue.append(row["first_aw_tick"] - submit)
            service.append(row["done_tick"] - row["first_aw_tick"])
    return {"queue_wait": _histogram(queue), "service": _histogram(service)}


def _peak_overlap(intervals):
    """Peak simultaneous occupancy of half-open intervals; at one tick a
    retirement counts before an admission."""
    events = []
    for start, stop in intervals:
        _require(start <= stop, "an archived interval ends before it begins")
        events.append((start, 1))
        events.append((stop, -1))
    peak = current = 0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        current += delta
        peak = max(peak, current)
    return peak


def _link_vc_utilization(result, makespan_ticks):
    """Whole-run per link/vc flits over the run window, in the network
    bridges' own clock domain."""
    periods = {int(row["clock_period_ticks"]) for row in result["bridges"]
               if row.get("clock_period_ticks")}
    _require(len(periods) == 1,
             "the bridges do not share one clock domain: %s"
             % sorted(periods))
    period = periods.pop()
    cycles = makespan_ticks / period if period > 0 else 0
    if cycles <= 0:
        return {}
    return {"link:%s/vc:%s/vnet:%s" % (row["link_id"], row["vc"], row["vnet"]):
            row["sent"] / cycles for row in result["credit_ledger"]}


def verified_sram_traffic(cores, bank_count=None):
    reports = []
    seen = set()
    for core in cores:
        core_id = core.get("core_id")
        _require(type(core_id) is int and core_id >= 0 and core_id not in seen,
                 "SRAM traffic has an invalid or duplicate core identity")
        seen.add(core_id)
        count = core.get("sram_bank_count")
        _require(type(count) is int and count > 0,
                 "SRAM traffic omits its bank count")
        _require(bank_count is None or count == bank_count,
                 "SRAM bank count differs from the admitted architecture")
        banks = core.get("sram_banks")
        _require(isinstance(banks, list) and len(banks) == count,
                 "SRAM traffic omits banks")
        identities = []
        for bank in banks:
            _require(isinstance(bank, dict), "invalid SRAM bank record")
            for field in ("bank_id", "read_bytes", "write_bytes"):
                value = bank.get(field)
                _require(type(value) is int and value >= 0,
                         "SRAM bank %s must be a nonnegative integer" % field)
            identities.append(bank["bank_id"])
        _require(sorted(identities) == list(range(count)),
                 "SRAM bank identities do not exactly cover the architecture")
        report = {"core_id": core_id, "banks": banks}
        for field in ("read_bytes", "write_bytes", "bank_conflicts", "service_cycles"):
            value = core.get("sram_" + field)
            _require(type(value) is int and value >= 0,
                     "SRAM %s must be a nonnegative integer" % field)
            if field in ("read_bytes", "write_bytes"):
                _require(value == sum(bank[field] for bank in banks),
                         "SRAM %s differs from its bank sum" % field)
            report[field] = value
        reports.append(report)
    return reports


def summarize_workload(sections, result, arch=None, program_dir=None, *,
                       execution_views: tuple[ProfileExecutionView, ...] | None = None):
    commands = {row["command_id"]: row for row in sections["COMMANDS"]}
    if execution_views is None:
        selections = (frozenset(commands),) * len(result['instances'])
    else:
        _require(type(execution_views) is tuple and len(execution_views) == len(result['instances']) and
            all(type(view) is ProfileExecutionView and view.command_ids and view.command_ids <= commands.keys() and
                view.core_ids == {commands[command_id]['core_id'] for command_id in view.command_ids}
                for view in execution_views), 'workload phase views differ from admitted commands')
        selections = tuple(view.command_ids for view in execution_views)
    descriptors = {row["descriptor_id"]: row
                   for row in sections["DMA_DESCRIPTORS"]}
    joined_timing, _ = verified_dma_timing_phases(result)
    frames = []
    for frame, selected in zip(result["instances"], selections, strict=True):
        cores, collective = [], {}
        rom_entries = Counter(commands[command_id]['core_id'] for command_id in selected)
        phase_cores = {commands[command_id]['core_id'] for command_id in selected}
        _require(phase_cores <= {int(core_id) for core_id in frame['cores']},
            'workload phase has no ledger for an assigned core')
        for core_id, ledger in sorted(frame["cores"].items(), key=lambda item: int(item[0])):
            observations = ledger["observations"]
            _require(all(row['command_id'] in selected and commands[row['command_id']]['core_id'] == int(core_id)
                for row in observations['commands']), 'workload command does not belong to its admitted phase and core')
            issued = [row for row in observations["commands"] if row["issued"]]
            terminal = [row for row in observations["commands"] if row["terminal"]]
            participating = int(core_id) in phase_cores
            if participating:
                _require(issued and terminal, "workload statistics require command lifecycle facts")
                begin = min(row["issue_tick"] for row in issued)
                end = max(row["terminal_tick"] for row in terminal)
            else:
                _require(not any(observations[field] for field in (
                    'commands', 'engine_executions', 'engine_blocks', 'wait_stalls',
                    'compute_outputs', 'transfers', 'descriptor_executions',
                    'runtime_descriptor_executions', 'transfer_commits')),
                    "unassigned core archives execution activity")
                begin = end = None
            elapsed_ticks = end - begin if participating else 0
            engines = {}
            for engine in _ENGINE_NAMES:
                runs = [row for row in observations["engine_executions"] if row["engine"] == engine]
                intervals = [(row["begin_tick"], row["end_tick"]) for row in runs if row["ended"]]
                busy = sum(stop - start for start, stop in _merged_intervals(intervals))
                engines[engine] = {"commands": len(runs), "work_cycles": sum(row["cycles"] for row in runs),
                                   "busy_ticks": busy, "utilization": busy / max(1, elapsed_ticks)}
            for row in observations["descriptor_executions"]:
                descriptor = descriptors.get(row["descriptor_id"])
                transfer_id = descriptor.get("transfer_id") \
                    if descriptor else None
                if not transfer_id:
                    continue
                entry = collective.setdefault(transfer_id, [])
                entry.append((row["submit_tick"], row["completion_tick"]))
            exposed, overlap = _communication_exposure(
                observations, commands, _core_engine_busy(observations))
            timing_entries = joined_timing.get((id(frame), int(core_id)), [])
            timing_rows = [row for row, _ in timing_entries]
            transfer_count = len({
                descriptors[row["descriptor_id"]]["transfer_id"]
                for row in observations["descriptor_executions"]
                if row["descriptor_id"] in descriptors
                and descriptors[row["descriptor_id"]].get("transfer_id")})
            cores.append({"core_id": int(core_id), "begin_tick": begin, "end_tick": end,
                          "elapsed_ticks": elapsed_ticks, "issued": len(issued), "terminal": len(terminal),
                          "opcodes": dict(sorted(Counter(commands[row["command_id"]]["opcode"] for row in issued).items())),
                          "engines": engines,
                          "engine_fifo_stall_ticks": _engine_stall_ticks(observations),
                          "dependency_stall_ticks": _wait_stall_ticks(observations),
                          "communication_exposed_ticks": exposed,
                          "communication_overlap_ticks": overlap,
                          "dma": _dma_activity(observations, frame["bursts"],
                                               timing_rows, transfer_count),
                          "dma_response_errors": _response_errors(
                              result, core_id, observations),
                          "dma_latency_ticks": _dma_latencies(timing_entries),
                          "peak_allocation_bytes": _peak_allocation_bytes(sections, core_id),
                          "command_rom_entries": rom_entries[int(core_id)],
                          "command_rom_capacity_entries": arch.command_rom_entries if arch is not None else None})
        active = [row for row in cores if row['issued']]
        _require(active, "workload statistics require command lifecycle facts")
        begin, end = min(row["begin_tick"] for row in active), max(row["end_tick"] for row in active)
        elapsed = [row["elapsed_ticks"] for row in cores]
        frames.append({"instance": frame["instance"], "makespan_ticks": end - begin,
                       "core_imbalance_ticks": max(elapsed) - min(elapsed), "cores": cores,
                       "collective_transactions": [{"transfer_id": key,
                           "source_completion_latency_ticks": max(end for _, end in rows) - min(begin for begin, _ in rows)}
                           for key, rows in sorted(collective.items())]})
    run_begin = min(row["begin_tick"] for frame in frames for row in frame["cores"] if row['issued'])
    run_end = max(row["end_tick"] for frame in frames for row in frame["cores"] if row['issued'])
    report = {"instances": frames,
              "network_stalls": result["network_stalls"],
              "credit_ledger": result["credit_ledger"],
              "vnet_traffic": result["garnet_traffic"],
              "run_window": {"begin_tick": run_begin, "end_tick": run_end,
                             "link_vc_utilization": _link_vc_utilization(
                                 result, run_end - run_begin)},
              "bridges": [{"core_id": row["core_id"],
                           "clock_period_ticks": row["clock_period_ticks"],
                           "peak_read_outstanding": row["peak_read_outstanding"],
                           "peak_write_outstanding": row["peak_write_outstanding"],
                           "b_error_count": row["b_error_count"],
                           "r_error_beats": row["r_error_beats"]}
                          for row in result["bridges"]],
              "sram": verified_sram_traffic(
                  result["cores"], arch.sram_banks if arch is not None else None),
              "unavailable": []}
    if arch is not None and program_dir is not None:
        report["compute_prediction"] = _compute_prediction(
            program_dir, arch, frames, selections)
    return report
