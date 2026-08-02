"""Run two public RV64 proxy processes through one shared 2x2 CHI HN-F.

This is a syscall-emulation multiprogram experiment: CPU0 and CPU1 execute
different single-threaded processes in independent address spaces.  It is not
a SPEC CPU2006 run and must never be reported as one.
"""

import argparse
import json
import os
import shlex

import m5
from m5.objects import (
    AddrRange,
    Cache2ChiBridge,
    Chi2ClassicMemBridge,
    ChiRouterRefModel,
    DecoupledBPUWithBTB,
    GuestCallStackProfiler,
    HomeNodeFull,
    Process,
    Root,
    SEWorkload,
    SrcClockDomain,
    System,
    VoltageDomain,
)
from m5.util import addToPath, fatal

addToPath("../")

from common import CacheConfig, MemConfig, Options
from common.FSConfig import MemBus
from common.xiangshan import XiangshanCore


FULL_CAUSE = "shared HNF SLC reached full capacity"
LIMIT_CAUSE = "simulate() limit reached"


def _split_exact(value, label, count=2):
    fields = value.split(";") if value else []
    if len(fields) != count or any(not field for field in fields):
        fatal("%s must contain exactly %d non-empty ';'-separated fields",
              label, count)
    return fields


def _build_processes(args):
    binaries = [os.path.abspath(path) for path in _split_exact(args.cmd, "--cmd")]
    options = args.options.split(";") if args.options else ["", ""]
    if len(options) != 2:
        fatal("--options must contain exactly two ';'-separated fields")
    cwds = [os.path.abspath(path) for path in
            _split_exact(args.workload_cwds, "--workload-cwds")]

    processes = []
    for cpu_id, (binary, option, cwd) in enumerate(zip(binaries, options, cwds)):
        if not os.path.isfile(binary):
            fatal("CPU%d ELF does not exist: %s", cpu_id, binary)
        if not os.path.isdir(cwd):
            fatal("CPU%d working directory does not exist: %s", cpu_id, cwd)
        process = Process(pid=100 + cpu_id)
        process.executable = binary
        process.cwd = cwd
        process.gid = os.getgid()
        process.cmd = [binary] + (shlex.split(option) if option else [])
        processes.append(process)
    return binaries, processes


def _parse_args():
    parser = argparse.ArgumentParser()
    Options.addCommonOptions(parser, configure_xiangshan=True)
    Options.addXiangshanCommonOptions(parser)
    Options.addSEOptions(parser)
    parser.add_argument(
        "--workload-cwds", required=True,
        help="two ';'-separated guest working directories in CPU order")
    parser.add_argument(
        "--slc-replacement-policy",
        choices=("lru", "lsu", "random", "pseudo_random", "srrip"),
        default="lru")
    parser.add_argument(
        "--slc-replacement-seed", type=lambda value: int(value, 0), default=1)
    parser.add_argument(
        "--fill-max-ticks", type=int, default=200_000_000_000,
        help="maximum ticks allowed before the 1 MiB SLC first becomes full")
    parser.add_argument(
        "--settle-ticks", type=int, default=2_000_000,
        help="active steady-state ticks after first-full and before drain/reset")
    parser.add_argument(
        "--checkpoint-full-retry-limit", type=int, default=8,
        help="maximum drain attempts used to obtain an exactly-full boundary")
    parser.add_argument(
        "--checkpoint-refill-max-ticks", type=int, default=1_000_000_000,
        help="maximum active ticks allowed to refill between drain attempts")
    parser.add_argument(
        "--roi-ticks", type=int, default=2_000_000_000,
        help="fixed Replacement ROI length in ticks")
    parser.add_argument(
        "--min-roi-insts-per-core", type=int, default=100_000,
        help="minimum instructions each core must commit in the ROI")
    parser.add_argument(
        "--experiment-mode",
        choices=("combined", "warmup", "roi", "drain_probe"),
        default="combined",
        help="combined debug run, common-checkpoint creation, or ROI restore")
    parser.add_argument(
        "--drain-probe-start-ticks", type=int, default=10_000_000,
        help="diagnostic mode: active execution before requesting drain")
    parser.add_argument(
        "--drain-probe-budget-ticks", type=int, default=100_000_000,
        help="diagnostic mode: maximum simulated ticks allowed for drain")
    parser.add_argument(
        "--common-checkpoint", default="",
        help="warmup output checkpoint or required ROI restore checkpoint")
    parser.add_argument(
        "--enable-rnf-transaction-latency", action="store_true",
        help="record ID-matched RNF REQ/SNP latency only inside the ROI")
    parser.add_argument(
        "--enable-guest-stack-profile", action="store_true",
        help="sample ROI guest retired-instruction shadow call stacks")
    parser.add_argument(
        "--guest-stack-sample-period-insts", type=int, default=1000,
        help="retired guest macro-instructions between stack samples")
    parser.set_defaults(exit_on_slc_full=True)
    return parser.parse_args()


def _configure_core(cpu, clock_domain):
    cpu.clk_domain = clock_domain
    cpu.branchPred = DecoupledBPUWithBTB(bpDBSwitches=[])
    cpu.branchPred.isDumpMisspredPC = True
    cpu.store_prefetch_train = False
    cpu.enable_storeSet_train = False
    cpu.enable_riscv_vector = True


def _build_system(args, binaries, processes):
    if args.num_cpus != 2:
        fatal("the 2x2 mixed SE entry point requires exactly -n 2")
    if args.external_memory_system or args.ruby:
        fatal("the 2x2 mixed SE entry point requires classic in-process memory")
    if args.classic_l2:
        fatal("the 2x2 mixed SE entry point requires the aligned private L2")

    args.sys_clock = "1.8GHz"
    args.cpu_clock = "2.3GHz"
    # Keep the experiment self-contained.  The Xiangshan DRAMsim3 default
    # expects an external ini file resolved from the launch directory.
    args.mem_type = "DDR4_2400_8x8"
    args.mem_channels = 1
    args.kmh_align = True
    args.caches = True
    args.l2cache = True
    args.l3cache = False
    args.no_l3cache = True
    args.chi_test_mode = True
    args.chi_2x2_router_test_mode = True
    args.chi_6x4_hnf_router_test_mode = False
    args.enable_difftest = False
    args.xiangshan_system = True
    args.cpu_type = "RiscvO3CPU"
    # The composite prefetchers currently have a tick-zero initialization
    # failure in this SE topology.  Disable them uniformly for all policies.
    args.no_pf = True
    args.l1_to_l2_pf_hint = False
    args.exit_on_slc_full = args.experiment_mode not in ("roi", "drain_probe")

    system = System(
        cpu=[XiangshanCore(cpu_id=cpu_id) for cpu_id in range(2)],
        mem_mode="timing",
        mem_ranges=[AddrRange(args.mem_size)],
        cache_line_size=args.cacheline_size,
    )
    system.num_cpus = 2
    system.xiangshan_system = True
    system.enable_difftest = False
    system.enable_riscv_vector = True
    system.exit_on_work_items = False

    system.voltage_domain = VoltageDomain(voltage=args.sys_voltage)
    system.clk_domain = SrcClockDomain(
        clock=args.sys_clock, voltage_domain=system.voltage_domain)
    system.cpu_voltage_domain = VoltageDomain()
    system.cpu_clk_domain = SrcClockDomain(
        clock=args.cpu_clock, voltage_domain=system.cpu_voltage_domain)
    for cpu in system.cpu:
        _configure_core(cpu, system.cpu_clk_domain)

    system.chi_bridges = [
        Cache2ChiBridge(
            transaction_latency_trace_file=(
                "rnf%d_transaction_latency_raw.csv" % cpu_id
                if args.enable_rnf_transaction_latency else ""))
        for cpu_id in range(2)
    ]
    system.home_node = [HomeNodeFull()]
    system.home_node[0].slc_restore_allow_policy_override = (
        args.experiment_mode == "roi")
    system.chi_routers = [
        ChiRouterRefModel(local_x=x, local_y=y, node_type="router")
        for y in range(2) for x in range(2)
    ]
    system.snf_bridge = Chi2ClassicMemBridge()

    system.workload = SEWorkload.init_compatible(binaries[0])
    for cpu_id, process in enumerate(processes):
        system.cpu[cpu_id].workload = process
        system.cpu[cpu_id].createThreads()

    if args.enable_guest_stack_profile:
        system.guest_stack_profilers = [
            GuestCallStackProfiler(
                manager=system.cpu[cpu_id],
                profile_file="cpu%d_guest_stack_samples_raw.csv" % cpu_id,
                metadata_file=(
                    "cpu%d_guest_stack_samples_metadata.json" % cpu_id),
                sample_period_insts=args.guest_stack_sample_period_insts,
            )
            for cpu_id in range(2)
        ]

    system.membus = MemBus()
    system.system_port = system.membus.cpu_side_ports
    CacheConfig.config_cache(args, system)
    MemConfig.config_mem(args, system)
    return system


def _progress(system):
    return {
        "tick": int(m5.curTick()),
        "cpu_insts": [int(cpu.getCurrentInstCount(0)) for cpu in system.cpu],
        "cpu_active_threads": [int(cpu.numActiveThreads()) for cpu in system.cpu],
    }


def _require_active(progress, label):
    if progress["cpu_active_threads"] != [1, 1]:
        fatal("both workloads must be active at %s; active_threads=%s",
              label, progress["cpu_active_threads"])


def _slc_occupancy(system):
    slcsf = system.home_node[0].slcsf
    return {
        "slc_valid_lines": int(slcsf.slcValidLineCount()),
        "slc_capacity_lines": int(slcsf.slcCapacityLineCount()),
        "max_valid_ways_per_set": int(slcsf.maxValidWaysInSet()),
    }


def _start_roi_instrumentation(args, system, phases):
    enabled = {
        "rnf_transaction_latency": args.enable_rnf_transaction_latency,
        "guest_stack_profile": args.enable_guest_stack_profile,
        "guest_stack_sample_period_insts": (
            args.guest_stack_sample_period_insts
            if args.enable_guest_stack_profile else None),
        "start_tick": int(m5.curTick()),
    }
    if args.enable_rnf_transaction_latency:
        for bridge in system.chi_bridges:
            bridge.startTransactionLatencyTrace()
    if args.enable_guest_stack_profile:
        for profiler in system.guest_stack_profilers:
            profiler.start()
    phases["instrumentation_start"] = enabled


def _stop_roi_instrumentation(args, system, phases):
    if args.enable_guest_stack_profile:
        for profiler in system.guest_stack_profilers:
            profiler.stop()
    if args.enable_rnf_transaction_latency:
        for bridge in system.chi_bridges:
            bridge.stopTransactionLatencyTrace()
    phases["instrumentation_stop"] = {
        "tick": int(m5.curTick()),
        "rnf_transaction_latency": args.enable_rnf_transaction_latency,
        "guest_stack_profile": args.enable_guest_stack_profile,
    }


def _drain_active_workloads(system, phases, label):
    phases[label + "_active"] = _progress(system)
    _require_active(phases[label + "_active"], label + " active boundary")
    m5.drain()
    phases[label + "_drained"] = _progress(system)
    _require_active(phases[label + "_drained"], label + " drained boundary")


def _resume_drained_workloads(system, phases, label):
    # A restored checkpoint is globally drained.  Advance one excluded tick
    # to resume the drain manager and the two still-active thread contexts.
    resume_event = m5.simulate(1)
    if resume_event.getCause() != LIMIT_CAUSE or resume_event.getCode() != 0:
        fatal("failed to resume the drained checkpoint: %s",
              resume_event.getCause())
    phases[label] = _progress(system)
    _require_active(phases[label], label)


def _drain_to_full_boundary(args, system, phases, label):
    """Reach a global-drain fixed point whose SLC is exactly full.

    Completing legal coherence work during drain may invalidate a line after
    the first-full event.  If that happens, resume both workloads, arm an exit
    for the next exact transition back to full, and drain again.  The bounded
    loop records every attempt and accepts only a directly observed full and
    globally drained boundary.
    """
    slcsf = system.home_node[0].slcsf
    attempts = []
    for attempt in range(1, args.checkpoint_full_retry_limit + 1):
        attempt_label = label if attempt == 1 else "%s_retry_%d" % (
            label, attempt - 1)
        before = {**_progress(system), **_slc_occupancy(system)}
        _drain_active_workloads(system, phases, attempt_label)
        after = {**_progress(system), **_slc_occupancy(system)}
        record = {
            "attempt": attempt,
            "active_boundary": before,
            "drained_boundary": after,
        }
        attempts.append(record)
        if after["slc_valid_lines"] == after["slc_capacity_lines"]:
            phases[label + "_drained"] = after
            phases[label + "_boundary_attempts"] = attempts
            return after

        if attempt == args.checkpoint_full_retry_limit:
            break
        resume_label = "%s_refill_%d_start" % (label, attempt)
        _resume_drained_workloads(system, phases, resume_label)
        refill_start = {**phases[resume_label], **_slc_occupancy(system)}
        record["refill_start"] = refill_start
        if (refill_start["slc_valid_lines"] ==
                refill_start["slc_capacity_lines"]):
            record["refill_end"] = {
                **refill_start,
                "exit_cause": "SLC became full while resuming drain",
                "exit_code": 0,
            }
            continue

        slcsf.rearmSlcFullExit()
        refill_event = m5.simulate(args.checkpoint_refill_max_ticks)
        refill_end = {
            **_progress(system), **_slc_occupancy(system),
            "exit_cause": refill_event.getCause(),
            "exit_code": refill_event.getCode(),
        }
        record["refill_end"] = refill_end
        if (refill_event.getCause() != FULL_CAUSE or
                refill_event.getCode() != 0):
            fatal("SLC did not refill before the checkpoint refill limit: %s",
                  refill_event.getCause())
        _require_active(refill_end, "checkpoint refill")
        if refill_end["slc_valid_lines"] != refill_end["slc_capacity_lines"]:
            fatal("SLC-full exit did not expose an exactly-full boundary")

    phases[label + "_boundary_attempts"] = attempts
    last = attempts[-1]["drained_boundary"]
    fatal("could not obtain a drained full-SLC boundary after %d attempts: "
          "%d/%d", args.checkpoint_full_retry_limit,
          last["slc_valid_lines"], last["slc_capacity_lines"])


def _write_phase_metadata(
        args, binaries, processes, phases, stats_sections):
    payload = {
        "schema_version": 1,
        "classification": "public-source proxy; not SPEC CPU2006",
        "topology": {
            "mesh": "2x2", "cpus": 2, "rnfs": 2, "hnfs": 1,
            "cpu0_rnf": "0x00@router(0,0)/P0D0",
            "cpu1_rnf": "0x04@router(0,0)/P1D0",
            "hnf": "0x90@router(1,1)/P0D0",
            "sn": "0x80@router(1,0)/P0D0",
            "slc": {"bytes": 1 << 20, "sets": 1024, "ways": 16,
                    "line_bytes": 64},
        },
        "policy": args.slc_replacement_policy,
        "random_seed": args.slc_replacement_seed,
        "processes": [
            {"cpu_id": cpu_id, "pid": 100 + cpu_id, "elf": binary,
             "cwd": str(process.cwd), "argv": [str(arg) for arg in process.cmd]}
            for cpu_id, (binary, process) in enumerate(zip(binaries, processes))
        ],
        "fill_max_ticks": args.fill_max_ticks,
        "settle_ticks": args.settle_ticks,
        "checkpoint_full_retry_limit": args.checkpoint_full_retry_limit,
        "checkpoint_refill_max_ticks": args.checkpoint_refill_max_ticks,
        "roi_ticks": args.roi_ticks,
        "min_roi_insts_per_core": args.min_roi_insts_per_core,
        "instrumentation": {
            "rnf_transaction_latency": {
                "enabled": args.enable_rnf_transaction_latency,
                "identity": "exact CHI SrcID + TxnID",
                "start": "REQ injection or TXSNP acceptance at RNF",
                "end": "matching terminal RSP/DAT acceptance or SNP response injection",
                "semantic_timing_effect": False,
            },
            "guest_stack_profile": {
                "enabled": args.enable_guest_stack_profile,
                "sample_period_insts": (
                    args.guest_stack_sample_period_insts
                    if args.enable_guest_stack_profile else None),
                "source": "O3 retired guest macro-instruction Commit probe",
                "semantic_timing_effect": False,
            },
        },
        "experiment_mode": args.experiment_mode,
        "common_checkpoint": args.common_checkpoint or None,
        "stats_sections": stats_sections,
        "phases": phases,
    }
    with open(os.path.join(m5.options.outdir, "phase_metadata.json"),
              "w", encoding="utf-8") as output:
        json.dump(payload, output, indent=2, sort_keys=True)
        output.write("\n")


def _run(args, system, binaries, processes):
    phases = {"cold_start": _progress(system)}
    m5.stats.reset()

    fill_event = m5.simulate(args.fill_max_ticks)
    phases["first_full"] = {
        **_progress(system), "exit_cause": fill_event.getCause(),
        "exit_code": fill_event.getCode(),
    }
    if fill_event.getCause() != FULL_CAUSE or fill_event.getCode() != 0:
        fatal("SLC did not become full before the fill limit: %s",
              fill_event.getCause())
    _require_active(phases["first_full"], "first-full")
    m5.stats.dump()

    settle_event = m5.simulate(args.settle_ticks)
    phases["post_settle"] = {
        **_progress(system), "exit_cause": settle_event.getCause(),
        "exit_code": settle_event.getCode(),
    }
    if settle_event.getCause() != LIMIT_CAUSE or settle_event.getCode() != 0:
        fatal("a workload exited during the settle interval: %s",
              settle_event.getCause())
    _require_active(phases["post_settle"], "post-settle")

    # Draining completes every transient CPU/CHI/HNF request without clearing
    # any cache state.  The following reset therefore starts the ROI from a
    # quiescent, full-cache boundary shared by every policy.
    _drain_to_full_boundary(args, system, phases, "pre_reset")
    m5.stats.dump()
    _resume_drained_workloads(system, phases, "roi_start")
    _start_roi_instrumentation(args, system, phases)
    m5.stats.reset()

    roi_event = m5.simulate(args.roi_ticks)
    phases["roi_end"] = {
        **_progress(system), "exit_cause": roi_event.getCause(),
        "exit_code": roi_event.getCode(),
    }
    if roi_event.getCause() != LIMIT_CAUSE or roi_event.getCode() != 0:
        fatal("a workload exited during the Replacement ROI: %s",
              roi_event.getCause())
    _require_active(phases["roi_end"], "ROI end")
    roi_insts = [end - start for start, end in zip(
        phases["roi_start"]["cpu_insts"], phases["roi_end"]["cpu_insts"])]
    phases["roi_end"]["measured_cpu_insts"] = roi_insts
    if any(insts < args.min_roi_insts_per_core for insts in roi_insts):
        fatal("insufficient ROI instructions per core: %s", roi_insts)
    _stop_roi_instrumentation(args, system, phases)
    m5.stats.dump()
    _write_phase_metadata(
        args, binaries, processes, phases,
        {"first_full": 0, "pre_reset_drained": 1, "replacement_roi": 2})


def _run_warmup(args, system, binaries, processes):
    phases = {"cold_start": _progress(system)}
    m5.stats.reset()
    fill_event = m5.simulate(args.fill_max_ticks)
    phases["first_full"] = {
        **_progress(system), "exit_cause": fill_event.getCause(),
        "exit_code": fill_event.getCode(),
    }
    if fill_event.getCause() != FULL_CAUSE or fill_event.getCode() != 0:
        fatal("SLC did not become full before the fill limit: %s",
              fill_event.getCause())
    _require_active(phases["first_full"], "first-full")
    m5.stats.dump()

    settle_event = m5.simulate(args.settle_ticks)
    phases["post_settle"] = {
        **_progress(system), "exit_cause": settle_event.getCause(),
        "exit_code": settle_event.getCode(),
    }
    if settle_event.getCause() != LIMIT_CAUSE or settle_event.getCode() != 0:
        fatal("a workload exited during common warm-up settle: %s",
              settle_event.getCause())
    _require_active(phases["post_settle"], "post-settle")

    checkpoint_boundary = _drain_to_full_boundary(
        args, system, phases, "pre_checkpoint")
    phases["checkpoint"] = checkpoint_boundary
    valid = checkpoint_boundary["slc_valid_lines"]
    capacity = checkpoint_boundary["slc_capacity_lines"]
    if valid != capacity:
        fatal("common checkpoint SLC is not full: %d/%d", valid, capacity)
    m5.stats.dump()
    checkpoint = args.common_checkpoint or os.path.join(
        m5.options.outdir, "common_full_cpt")
    args.common_checkpoint = os.path.abspath(checkpoint)
    m5.checkpoint(args.common_checkpoint)
    _write_phase_metadata(
        args, binaries, processes, phases,
        {"first_full": 0, "pre_checkpoint_drained": 1})


def _run_roi(args, system, binaries, processes):
    phases = {"restore_drained": _progress(system)}
    # O3 reconstructs activeThreads in drainResume(), while each serialized
    # ThreadState already carries Active status.  A freshly instantiated
    # drained checkpoint therefore normally reports [0, 0] here; reject only
    # a partial/asymmetric reconstruction and require [1, 1] immediately
    # after the first excluded resume tick below.
    if phases["restore_drained"]["cpu_active_threads"] not in (
            [0, 0], [1, 1]):
        fatal("asymmetric restored CPU activity: %s",
              phases["restore_drained"]["cpu_active_threads"])
    _resume_drained_workloads(system, phases, "restore")
    slcsf = system.home_node[0].slcsf
    phases["restore"].update(_slc_occupancy(system))
    valid = phases["restore"]["slc_valid_lines"]
    capacity = phases["restore"]["slc_capacity_lines"]
    if valid != capacity:
        fatal("Replacement ROI restored a non-full SLC: %d/%d",
              valid, capacity)

    m5.stats.reset()
    phases["roi_start"] = _progress(system)
    _start_roi_instrumentation(args, system, phases)
    roi_event = m5.simulate(args.roi_ticks)
    phases["roi_end"] = {
        **_progress(system), "exit_cause": roi_event.getCause(),
        "exit_code": roi_event.getCode(),
    }
    if roi_event.getCause() != LIMIT_CAUSE or roi_event.getCode() != 0:
        fatal("a workload exited during the Replacement ROI: %s",
              roi_event.getCause())
    _require_active(phases["roi_end"], "ROI end")
    roi_insts = [end - start for start, end in zip(
        phases["roi_start"]["cpu_insts"],
        phases["roi_end"]["cpu_insts"])]
    phases["roi_end"]["measured_cpu_insts"] = roi_insts
    if any(insts < args.min_roi_insts_per_core for insts in roi_insts):
        fatal("insufficient ROI instructions per core: %s", roi_insts)
    _stop_roi_instrumentation(args, system, phases)
    # Coherence may invalidate a small number of SLC lines after the common
    # full boundary.  The experiment requires an exactly-full ROI start,
    # 100% peak occupancy, and sustained replacement/victim activity; it does
    # not require the final instantaneous occupancy to remain exactly full.
    m5.stats.dump()
    _write_phase_metadata(
        args, binaries, processes, phases, {"replacement_roi": 0})


def _run_drain_probe(args, system, binaries, processes):
    """Bounded diagnostic for gem5's concurrent global-drain protocol."""
    from m5.simulate import _drain_manager

    phases = {"cold_start": _progress(system)}
    event = m5.simulate(args.drain_probe_start_ticks)
    if event.getCause() != LIMIT_CAUSE or event.getCode() != 0:
        fatal("a workload exited before the drain probe: %s", event.getCause())
    phases["drain_request"] = _progress(system)
    _require_active(phases["drain_request"], "drain probe request")

    budget_remaining = args.drain_probe_budget_ticks
    completed = False
    drain_passes = 0
    exit_cause = None
    exit_code = 0
    while budget_remaining > 0:
        drain_passes += 1
        if _drain_manager.tryDrain():
            completed = True
            exit_cause = "global drain fixed point"
            break
        pass_start = int(m5.curTick())
        event = m5.simulate(budget_remaining)
        elapsed = int(m5.curTick()) - pass_start
        budget_remaining -= elapsed
        exit_cause = event.getCause()
        exit_code = event.getCode()
        if exit_cause != "Finished drain":
            break
    phases["drain_result"] = {
        **_progress(system),
        "completed": completed,
        "drain_passes": drain_passes,
        "exit_cause": exit_cause,
        "exit_code": exit_code,
        "budget_ticks_used": (args.drain_probe_budget_ticks -
                              budget_remaining),
    }
    m5.stats.dump()
    _write_phase_metadata(args, binaries, processes, phases,
                          {"drain_probe": 0})
    print("Drain probe result: %s" % json.dumps(
        phases["drain_result"], sort_keys=True))


if __name__ == "__m5_main__":
    args = _parse_args()
    if min(args.fill_max_ticks, args.settle_ticks, args.roi_ticks,
           args.checkpoint_full_retry_limit,
           args.checkpoint_refill_max_ticks,
           args.min_roi_insts_per_core, args.drain_probe_start_ticks,
           args.drain_probe_budget_ticks) <= 0:
        fatal("fill/settle/ROI limits and minimum instructions must be positive")
    if args.guest_stack_sample_period_insts <= 0:
        fatal("--guest-stack-sample-period-insts must be positive")
    binaries, processes = _build_processes(args)
    test_sys = _build_system(args, binaries, processes)
    print("CHI 2x2 dual-process SE topology (public proxies; NOT SPEC CPU2006):")
    print("  CPU0 -> RNF 0x00 -> router(0,0) P0D0")
    print("  CPU1 -> RNF 0x04 -> router(0,0) P1D0")
    print("  shared HNF 0x90 -> router(1,1) P0D0, SLC 1MiB 1024x16x64B")
    print("  shared SN  0x80 -> router(1,0) P0D0 -> memory")
    print("  SLC policy: %s seed=%d" %
          (args.slc_replacement_policy, args.slc_replacement_seed))
    root = Root(full_system=False, system=test_sys)
    if args.experiment_mode == "roi":
        if not args.common_checkpoint or not os.path.isdir(
                args.common_checkpoint):
            fatal("--common-checkpoint must name an existing ROI checkpoint")
        args.common_checkpoint = os.path.abspath(args.common_checkpoint)
        m5.instantiate(args.common_checkpoint)
        _run_roi(args, test_sys, binaries, processes)
    else:
        m5.instantiate()
        if args.experiment_mode == "warmup":
            _run_warmup(args, test_sys, binaries, processes)
        elif args.experiment_mode == "drain_probe":
            _run_drain_probe(args, test_sys, binaries, processes)
        else:
            _run(args, test_sys, binaries, processes)
