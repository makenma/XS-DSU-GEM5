#!/usr/bin/env python3
"""Run and strictly verify the versioned AXI-over-Garnet process matrix."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from axi_result_verifier import (
    load_json_strict,
    sha256_file,
    validate_axi_result,
    validate_credit_ledger,
    validate_final_consistency_trace,
)
from axi_test_lib import (
    NEGATIVE_CASES,
    QUICK_CASES,
    RANDOM_FULL_SEEDS,
    UNIT_BINARIES,
    build_matrix_scenario,
    build_negative_scenario,
    build_random_scenario,
    canonical_json_bytes,
    constants,
    expand_response_progress_scenario,
    full_case_names,
    semantic_config,
    sha256_bytes,
    workload_jsonl,
)


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = Path(__file__).with_name("manifest.json")
LEGACY_GOLDEN = Path(__file__).with_name("golden") / \
    "garnet_xs_dev_7478835a.json"
CONFIG_SCRIPT = ROOT / "configs/example/axi_garnet_test.py"
LEGACY_SCRIPT = ROOT / "configs/example/garnet_synth_traffic.py"
MANAGED_NEGATIVE_ARTIFACTS = (
    "stats.txt", "config.json", "credit_ledger.json", "residual_state.json",
    "workload.jsonl", "event_trace.jsonl",
)
EXPECTED_OUTCOME = {
    "pass": 0,
    "architected_error": 0,
    "fatal_init": 1,
    "fatal_runtime": 1,
    "final_consistency_failure": 2,
}


def write_json(path, value):
    Path(path).write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def git_sha():
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def require_binary(path, protocol):
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size == 0 or \
            not os.access(path, os.X_OK):
        raise ValueError(f"missing, empty, or non-executable gem5 binary: {path}")
    variables = path.parent / "gem5.build/variables"
    if not variables.is_file():
        raise ValueError(f"cannot identify gem5 build protocol for {path}")
    first = variables.read_text(encoding="utf-8").splitlines()[0]
    if first.strip() != f"PROTOCOL = '{protocol}'":
        raise ValueError(
            f"binary protocol mismatch: expected {protocol}, found {first.strip()}"
        )
    return path


def validate_manifest(manifest):
    if manifest.get("schema_version") != 1:
        raise ValueError("unknown manifest schema_version")
    units = manifest.get("unit_binaries")
    if not isinstance(units, list) or len(units) != len(UNIT_BINARIES):
        raise ValueError("unit manifest must contain exactly 10 binaries")
    if {entry.get("binary") for entry in units} != set(UNIT_BINARIES):
        raise ValueError("unit manifest binary set mismatch")

    cases = manifest.get("integration_cases")
    if not isinstance(cases, list):
        raise ValueError("integration_cases must be a list")
    names = [case.get("name") for case in cases]
    if len(names) != len(set(names)):
        raise ValueError("duplicate integration case name")
    expected_full = set(full_case_names())
    if set(names) != expected_full or len(names) != 127:
        missing = sorted(expected_full - set(names))
        extra = sorted(set(names) - expected_full)
        raise ValueError(f"full manifest mismatch: missing={missing}, extra={extra}")
    quick = [case["name"] for case in cases if "quick" in case.get("suites", [])]
    full = [case["name"] for case in cases if "full" in case.get("suites", [])]
    if set(quick) != set(QUICK_CASES) or len(quick) != 44:
        raise ValueError("quick manifest is not the exact 44-case set")
    if set(full) != expected_full or len(full) != 127:
        raise ValueError("full manifest is not the exact 127-case set")
    if sum(name.startswith("n") for name in quick) != 22 or \
            set(name for name in quick if name.startswith("n")) != \
            set(NEGATIVE_CASES):
        raise ValueError("negative manifest is not the exact 22-case set")
    quick_random = [name for name in quick if name.startswith("rand_quick_")]
    full_only_random = [name for name in full if name.startswith("rand_full_")]
    full_only_random += [name for name in full if name.startswith("rand_nightly_")]
    if len(quick_random) != 3 or len(full_only_random) != 11:
        raise ValueError("random process counts do not match the contract")
    for case in cases:
        if case.get("expect") not in EXPECTED_OUTCOME:
            raise ValueError(f"case {case['name']} has an invalid expectation")
        if case.get("binary_role") not in ("axi", "legacy"):
            raise ValueError(f"case {case['name']} has an invalid binary role")
        if not isinstance(case.get("timeout_seconds"), int) or \
                case["timeout_seconds"] <= 0:
            raise ValueError(f"case {case['name']} has an invalid timeout")
    return cases


def _parse_matrix_name(name):
    match = re.fullmatch(
        r"buffer_matrix_(11111|24224|48448|8168816)_vc(1|2|4)_"
        r"(aw|w|b|ar|r|all)", name,
    )
    if not match:
        raise ValueError(f"malformed buffer-matrix case name: {name}")
    return match.group(1), int(match.group(2)), match.group(3)


def scenario_for_case(case):
    if "scenario" in case:
        scenario = load_json_strict(ROOT / case["scenario"])
    elif case.get("generator") == "negative":
        scenario = build_negative_scenario(case["name"])
    elif case.get("generator") in ("random", "determinism"):
        scenario = build_random_scenario(
            case["seed"], case["transaction_count"],
            case["stable_scenario_id"],
        )
    elif case.get("generator") == "matrix":
        scenario = build_matrix_scenario(*_parse_matrix_name(case["name"]))
    else:
        raise ValueError(f"case {case['name']} has no scenario source")
    scenario = dict(scenario)
    scenario = expand_response_progress_scenario(scenario)
    scenario["name"] = case["name"]
    scenario.setdefault(
        "stable_scenario_id", case.get("stable_scenario_id", case["name"])
    )
    return scenario


def option_value(args, option, default, converter=int):
    prefix = f"--{option}="
    values = [argument[len(prefix):] for argument in args
              if argument.startswith(prefix)]
    return converter(values[-1]) if values else default


def command_sha256(command):
    return sha256_bytes(canonical_json_bytes([str(item) for item in command]))


def semantic_cli(case, routers, rows):
    return {
        "network": "garnet",
        "topology": "AxiMeshDie",
        "routing_algorithm": 1,
        "routers": routers,
        "mesh_rows": rows,
        "link_width_bits": 128,
        "vcs_per_vnet": 4,
        "garnet_buffers_per_vnet": [4, 8, 4, 4, 8],
        "ruby_clock": "1GHz",
        "system_clock": "1GHz",
        "seed": case["seed"],
        "max_network_cycles": case["max_network_cycles"],
        "case_args": list(case.get("args", [])),
    }


def run_process(command, timeout_seconds, outdir):
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            [str(item) for item in command], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout_seconds, check=False,
        )
        return_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        return_code = 124
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
    duration = time.monotonic() - started
    (outdir / "stdout.log").write_text(stdout, encoding="utf-8")
    (outdir / "stderr.log").write_text(stderr, encoding="utf-8")
    write_json(outdir / "command.json", [str(item) for item in command])
    return {
        "exit_code": return_code,
        "timed_out": timed_out,
        "terminated_by_signal": return_code < 0,
        "stdout": stdout,
        "stderr": stderr,
        "duration_seconds": duration,
    }


def patch_axi_result(outdir, case, source_sha, config_hash):
    result_path = outdir / "axi_result.json"
    result = load_json_strict(result_path)
    result["case"] = case["name"]
    result["git_sha"] = source_sha
    result["config_hash"] = config_hash
    result["workload_sha256"] = sha256_file(outdir / "workload.jsonl")
    result["trace_sha256"] = sha256_file(outdir / "event_trace.jsonl")
    result["credit_ledger"]["sha256"] = sha256_file(
        outdir / "credit_ledger.json"
    )
    write_json(result_path, result)


def expected_network_shape(scenario, routers, rows, vcs_per_vnet):
    columns = routers // rows
    internal_directed = 2 * (
        rows * (columns - 1) + (rows - 1) * columns
    )
    endpoints = len(scenario["endpoint_to_router"]["initiators"]) + \
        len(scenario["endpoint_to_router"]["targets"])
    return internal_directed + 2 * endpoints, 5 * vcs_per_vnet


def write_resolved_config(outdir, source_sha, semantic, config_hash, case):
    write_json(outdir / "config.json", {
        "schema_version": 1,
        "case": case["name"],
        "git_sha": source_sha,
        "config_hash": config_hash,
        "semantic": semantic,
    })


def build_negative_result(outdir, case, process, source_sha, command):
    expected_code = EXPECTED_OUTCOME[case["expect"]]
    combined = process["stdout"] + "\n" + process["stderr"]
    marker = case["expected_marker"]
    if process["timed_out"] or process["terminated_by_signal"]:
        raise ValueError("expected failure ended via timeout or signal")
    if process["exit_code"] != expected_code or marker not in combined:
        raise ValueError(
            f"failure mismatch: exit={process['exit_code']} marker={marker!r}"
        )

    available = [name for name in MANAGED_NEGATIVE_ARTIFACTS
                 if (outdir / name).is_file() and (outdir / name).stat().st_size]
    unavailable = []
    for name, field in (
        ("stats.txt", "live_stats"),
        ("credit_ledger.json", "credit_ledger"),
        ("residual_state.json", "residual_state"),
        ("event_trace.jsonl", "trace_sha256"),
    ):
        if name not in available:
            unavailable.append(field)

    packets = 0
    flits = 0
    residual = None
    trace_hash = sha256_file(outdir / "event_trace.jsonl") \
        if "event_trace.jsonl" in available else None
    if case["expect"] == "final_consistency_failure":
        required = list(MANAGED_NEGATIVE_ARTIFACTS)
        if available != required:
            raise ValueError(
                f"final-consistency artifact set mismatch: {available}"
            )
        if unavailable:
            raise ValueError("final-consistency result has unavailable fields")
        residual = load_json_strict(outdir / "residual_state.json")
        expected_residual = {
            "schema_version": 1,
            "write_ordinal": 0,
            "buffered_w_beats": 3,
            "saw_wlast": False,
            "txn_uid_present": False,
        }
        if residual != expected_residual:
            raise ValueError("final-consistency residual snapshot mismatch")
        validate_final_consistency_trace(outdir / "event_trace.jsonl")
    else:
        maximum = case.get("max_injected_packets_before_failure")
        if maximum is None or packets > maximum:
            raise ValueError("fatal case exceeded pre-failure injection bound")

    result = {
        "schema_version": 1,
        "case": case["name"],
        "status": "expected_failure",
        "failure_class": case["expect"],
        "expected_marker": marker,
        "observed_marker": marker,
        "exit_code": process["exit_code"],
        "timed_out": process["timed_out"],
        "terminated_by_signal": process["terminated_by_signal"],
        "git_sha": source_sha,
        "command_sha256": command_sha256(command),
        "seed": case["seed"],
        "packets_injected_before_failure": packets,
        "flits_injected_before_failure": flits,
        "available_artifacts": available,
        "unavailable_fields": unavailable,
        "workload_sha256": sha256_file(outdir / "workload.jsonl"),
        "trace_sha256": trace_hash,
        "residual_state": residual,
    }
    write_json(outdir / "negative_result.json", result)
    return result


def run_axi_case(case, gem5, run_root, source_sha):
    outdir = run_root / case["name"]
    outdir.mkdir()
    scenario = scenario_for_case(case)
    write_json(outdir / "scenario.json", scenario)
    stable_id = scenario["stable_scenario_id"]
    (outdir / "workload.jsonl").write_bytes(
        workload_jsonl(scenario, stable_id, case["seed"])
    )

    large_mesh = case.get("generator") in ("random", "determinism")
    routers, rows = (16, 4) if large_mesh else (4, 2)
    semantic = semantic_config(scenario, semantic_cli(case, routers, rows))
    config_hash = sha256_bytes(canonical_json_bytes(semantic))
    max_ticks = case["max_network_cycles"] * 1000
    command = [
        gem5, "-d", outdir, CONFIG_SCRIPT,
        "--network=garnet", "--topology=AxiMeshDie", "--routing-algorithm=1",
        f"--axi-mesh-routers={routers}", f"--mesh-rows={rows}",
        "--link-width-bits=128", "--vcs-per-vnet=4",
        "--garnet-buffers-per-vnet=4,8,4,4,8",
        f"--axi-scenario={outdir / 'scenario.json'}",
        f"--axi-seed={case['seed']}", f"--axi-max-sim-ticks={max_ticks}",
        "--ruby-clock=1GHz", "--sys-clock=1GHz",
        *case.get("args", []),
    ]
    process = run_process(command, case["timeout_seconds"], outdir)
    write_resolved_config(outdir, source_sha, semantic, config_hash, case)

    if case["expect"] in ("fatal_init", "fatal_runtime",
                          "final_consistency_failure"):
        result = build_negative_result(
            outdir, case, process, source_sha, command
        )
        if case["expect"] == "final_consistency_failure":
            vcs = option_value(case.get("args", []), "vcs-per-vnet", 4)
            links, total_vcs = expected_network_shape(
                scenario, routers, rows, vcs
            )
            ledger = validate_credit_ledger(
                outdir / "credit_ledger.json", links, total_vcs, True
            )
            if any(entry["sent"] != 0 for entry in ledger["entries"]):
                raise ValueError(
                    "final-consistency fault unexpectedly entered Garnet"
                )
        return result

    if process["timed_out"] or process["terminated_by_signal"] or \
            process["exit_code"] != 0:
        raise ValueError(
            f"AXI pass case failed: exit={process['exit_code']} "
            f"stderr={process['stderr'][-600:]}"
        )
    marker = "AXI_MESH_FUNCTIONAL_SCENARIO_PASS"
    if marker not in process["stdout"] + process["stderr"]:
        raise ValueError("AXI pass marker was not observed")
    for name in (
        "axi_result.json", "credit_ledger.json", "stats.txt",
        "config.json", "workload.jsonl", "event_trace.jsonl",
    ):
        if not (outdir / name).is_file() or (outdir / name).stat().st_size == 0:
            raise ValueError(f"successful AXI case is missing {name}")
    patch_axi_result(outdir, case, source_sha, config_hash)

    args = case.get("args", [])
    link_width = option_value(args, "link-width-bits", 128)
    data_width = option_value(args, "axi-data-width-bits", 512)
    vcs = option_value(args, "vcs-per-vnet", 4)
    headers = option_value(
        args, "axi-wire-header-bytes", (24, 16, 8, 24, 16),
        lambda value: tuple(int(item) for item in value.split(",")),
    )
    links, total_vcs = expected_network_shape(scenario, routers, rows, vcs)
    return validate_axi_result(
        outdir / "axi_result.json", case,
        outdir / "workload.jsonl", outdir / "event_trace.jsonl",
        outdir / "credit_ledger.json", config_hash, source_sha,
        links, total_vcs, link_width, data_width, headers,
    )


def parse_scalar(stats, key):
    match = re.search(rf"(?m)^{re.escape(key)}\s+([^\s#|]+)", stats)
    if not match:
        raise ValueError(f"legacy stats missing {key}")
    return float(match.group(1))


def parse_vector(stats, key):
    line = next((line for line in stats.splitlines()
                 if line.startswith(key)), None)
    if line is None:
        raise ValueError(f"legacy stats missing {key}")
    values = re.findall(r"\|\s*([-+]?[0-9]+(?:\.[0-9]+)?)", line)
    if not values:
        raise ValueError(f"legacy vector stat is malformed: {key}")
    return [int(float(value)) for value in values]


def run_legacy_case(case, binary, run_root, source_sha, golden):
    outdir = run_root / case["name"]
    outdir.mkdir()
    command = [
        binary, "-d", outdir, LEGACY_SCRIPT,
        "--network=garnet", "--topology=Mesh_XY", "--routing-algorithm=1",
        "--num-cpus=4", "--num-dirs=4", "--mesh-rows=2",
        "--single-sender-id=0", "--single-dest-id=3",
        f"--inj-vnet={case['inj_vnet']}", "--num-packets-max=100",
        "--sim-cycles=5000000", "--injectionrate=0.1",
    ]
    process = run_process(command, case["timeout_seconds"], outdir)
    if process["timed_out"] or process["terminated_by_signal"] or \
            process["exit_code"] != 0:
        raise ValueError("legacy Garnet process did not exit normally")
    for name in ("stats.txt", "config.ini", "config.json"):
        if not (outdir / name).is_file() or (outdir / name).stat().st_size == 0:
            raise ValueError(f"legacy case is missing stock {name}")
    stats = (outdir / "stats.txt").read_text(encoding="utf-8")
    actual = {
        "sim_ticks": int(parse_scalar(stats, "simTicks")),
        "packets_injected": parse_vector(
            stats, "system.ruby.network.packets_injected"
        ),
        "packets_received": parse_vector(
            stats, "system.ruby.network.packets_received"
        ),
        "flits_injected": parse_vector(
            stats, "system.ruby.network.flits_injected"
        ),
        "flits_received": parse_vector(
            stats, "system.ruby.network.flits_received"
        ),
    }
    scalar_keys = {
        "packets_injected_total": "system.ruby.network.packets_injected::total",
        "packets_received_total": "system.ruby.network.packets_received::total",
        "flits_injected_total": "system.ruby.network.flits_injected::total",
        "flits_received_total": "system.ruby.network.flits_received::total",
        "average_packet_latency": "system.ruby.network.average_packet_latency",
        "average_flit_latency": "system.ruby.network.average_flit_latency",
        "average_hops": "system.ruby.network.average_hops",
    }
    for name, stat in scalar_keys.items():
        value = parse_scalar(stats, stat)
        actual[name] = value if name.startswith("average_") else int(value)

    expected = golden["cases"][case["name"]]
    comparisons = {}
    for name, expected_value in expected.items():
        if name == "inj_vnet":
            continue
        actual_value = actual[name]
        if isinstance(expected_value, list):
            passed = actual_value == expected_value
            delta = [a - b for a, b in zip(actual_value, expected_value)]
            tolerance = 0
        else:
            delta = actual_value - expected_value
            tolerance = 1e-12 if isinstance(expected_value, float) else 0
            passed = abs(delta) <= tolerance
        comparisons[name] = {
            "expected": expected_value, "actual": actual_value,
            "delta": delta, "tolerance": tolerance,
        }
        if not passed:
            raise ValueError(f"legacy golden mismatch for {name}")
    result = {
        "schema_version": 1,
        "case": case["name"],
        "status": "pass",
        "git_sha": source_sha,
        "base_sha": golden["provenance"]["spec_base_sha"],
        "command_sha256": command_sha256(command),
        "golden_sha256": sha256_file(LEGACY_GOLDEN),
        "exit_code": process["exit_code"],
        "stock_outdir": str(outdir),
        "stats": comparisons,
    }
    write_json(outdir / "legacy_result.json", result)
    return result


def check_determinism(run_root, selected_names):
    names = {"determinism_a", "determinism_b", "determinism_replay"}
    if not names.issubset(selected_names):
        return
    results = [load_json_strict(run_root / name / "axi_result.json")
               for name in sorted(names)]
    for field in ("config_hash", "workload_sha256", "trace_sha256"):
        if len({result[field] for result in results}) != 1:
            raise ValueError(f"determinism triplet disagrees on {field}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True)
    parser.add_argument("--suite", choices=("quick", "full"), required=True)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--out-root", default="")
    parser.add_argument(
        "--case", action="append", default=[],
        help="Development-only case filter; the manifest is still fully checked",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    manifest = load_json_strict(args.manifest)
    all_cases = validate_manifest(manifest)
    suite_cases = [case for case in all_cases if args.suite in case["suites"]]
    if args.case:
        requested = set(args.case)
        unknown = requested - {case["name"] for case in suite_cases}
        if unknown:
            raise ValueError(f"requested cases are not in suite: {sorted(unknown)}")
        suite_cases = [case for case in suite_cases if case["name"] in requested]
    expected_count = 44 if args.suite == "quick" else 127
    if not args.case and len(suite_cases) != expected_count:
        raise ValueError("selected suite process count mismatch")

    axi_binary = require_binary(args.gem5, "AXI_MESH")
    legacy_binary = None
    if any(case["binary_role"] == "legacy" for case in suite_cases):
        legacy_binary = require_binary(
            ROOT / "build/Garnet_standalone/gem5.opt", "Garnet_standalone"
        )
    source_sha = git_sha()
    golden = load_json_strict(LEGACY_GOLDEN)
    if golden.get("schema_version") != 1:
        raise ValueError("unknown legacy golden schema")

    if args.out_root:
        run_root = Path(args.out_root).resolve()
        run_root.mkdir(parents=True, exist_ok=True)
        if any(run_root.iterdir()):
            raise ValueError("--out-root must be empty to prevent stale artifacts")
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        run_root = ROOT / "m5out" / f"axi-garnet-{args.suite}-{stamp}-{os.getpid()}"
        run_root.mkdir(parents=True)

    records = []
    failures = []
    for case in suite_cases:
        try:
            if case["binary_role"] == "legacy":
                result = run_legacy_case(
                    case, legacy_binary, run_root, source_sha, golden
                )
            else:
                result = run_axi_case(
                    case, axi_binary, run_root, source_sha
                )
            records.append({"case": case["name"], "status": "pass"})
            print(f"[PASS] {case['name']}", flush=True)
        except Exception as error:  # preserve all case artifacts and continue
            records.append({
                "case": case["name"], "status": "fail", "error": str(error),
            })
            failures.append(case["name"])
            print(f"[FAIL] {case['name']}: {error}", file=sys.stderr, flush=True)
    try:
        check_determinism(run_root, {case["name"] for case in suite_cases})
    except Exception as error:
        failures.append("determinism_cross_check")
        records.append({
            "case": "determinism_cross_check", "status": "fail",
            "error": str(error),
        })

    report = {
        "schema_version": 1,
        "suite": args.suite,
        "git_sha": source_sha,
        "expected_processes": len(suite_cases),
        "run": len(suite_cases),
        "passed": len(suite_cases) - sum(
            record["status"] == "fail" and record["case"] !=
            "determinism_cross_check" for record in records
        ),
        "failed": len(failures),
        "skipped": 0,
        "timed_out": 0,
        "cases": records,
    }
    write_json(run_root / "integration_suite_result.json", report)
    print(f"results: {run_root}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"AXI Garnet runner error: {error}", file=sys.stderr)
        raise SystemExit(1)
