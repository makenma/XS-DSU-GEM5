#!/usr/bin/env python3

import json
from pathlib import Path

from axi_test_lib import (
    MATRIX_TRAFFIC,
    PROFILE_NAMES,
    QUICK_CASES,
    RANDOM_FULL_SEEDS,
    UNIT_BINARIES,
    VC_COUNTS,
    random_error_count,
)


HERE = Path(__file__).resolve().parent


def unit_entries():
    entries = []
    for binary, suites in UNIT_BINARIES.items():
        entries.append({
            "binary": binary,
            "suites": sorted(suites),
            "tests": sorted(
                f"{suite}.{test}"
                for suite, tests in suites.items()
                for test in tests
            ),
            "timeout_seconds": 60,
        })
    return entries


def quick_cases():
    scenario_dir = "configs/example/axi_garnet_scenarios"
    entries = {
        "endpoint_probe": {"scenario": f"{scenario_dir}/smoke.json"},
        "single_write": {"scenario": f"{scenario_dir}/single_write.json"},
        "burst_read": {"scenario": f"{scenario_dir}/burst_read.json"},
        "partial_wstrb": {"scenario": f"{scenario_dir}/partial_wstrb.json"},
        "w_before_aw_at_target": {
            "scenario": f"{scenario_dir}/w_before_aw_at_target.json"
        },
        "same_id_order": {"scenario": f"{scenario_dir}/same_id_order.json"},
        "cross_id_reorder": {
            "scenario": f"{scenario_dir}/cross_id_reorder.json"
        },
        "buffer_depth_credit_d1": {
            "scenario": f"{scenario_dir}/buffer_depth_and_credit.json",
            "args": ["--link-width-bits=64", "--vcs-per-vnet=1",
                     "--garnet-buffers-per-vnet=1,1,1,1,1"],
            "expected_stall_mask": ["router_credit"],
        },
        "buffer_depth_credit_d2": {
            "scenario": f"{scenario_dir}/buffer_depth_and_credit.json",
            "args": ["--link-width-bits=64", "--vcs-per-vnet=1",
                     "--garnet-buffers-per-vnet=2,2,2,2,2"],
            "expected_stall_mask": ["router_credit"],
        },
        "buffer_depth_credit_d8": {
            "scenario": f"{scenario_dir}/buffer_depth_and_credit.json",
            "args": ["--link-width-bits=64", "--vcs-per-vnet=1",
                     "--garnet-buffers-per-vnet=8,8,8,8,8"],
            "expected_stall_mask": ["router_credit"],
        },
        "target_quota_no_hol": {
            "scenario": f"{scenario_dir}/target_quota_no_hol.json",
            "args": [
                "--axi-target-write-contexts=2",
                "--axi-target-write-assembly-beats=2",
                "--axi-target-service-depths=1,1",
                "--axi-orphan-w-transactions=1",
                "--axi-orphan-w-beats=1",
            ],
            "expected_stall_mask": ["orphan_or_quota"],
        },
        "ejection_backpressure": {
            "scenario": f"{scenario_dir}/ejection_backpressure.json",
            "args": [
                "--axi-local-delivery-depths=4,4,2,4,2",
                "--axi-message-buffer-depths=2,2,2,2,2",
                "--axi-source-fifo-depths=4,8,4,8,8",
                "--axi-target-response-ready-depths=2,2",
            ],
            "expected_stall_mask": ["message_buffer_or_ni"],
        },
        "response_progress": {
            "scenario": f"{scenario_dir}/response_progress.json"
        },
    }
    cases = []
    for name in QUICK_CASES:
        if name in entries:
            item = entries[name]
            category = (
                "ordering" if name in ("same_id_order", "cross_id_reorder")
                else "backpressure" if name in (
                    "buffer_depth_credit_d1", "buffer_depth_credit_d2",
                    "buffer_depth_credit_d8", "target_quota_no_hol",
                    "ejection_backpressure",
                ) else "response_progress" if name == "response_progress"
                else "functional"
            )
            cases.append(case(name, category=category, **item))
        elif name.startswith("rand_quick_s"):
            seed = int(name.rsplit("s", 1)[1])
            cases.append(case(
                name, category="random_quick", generator="random",
                seed=seed, transaction_count=500,
                stable_scenario_id=name,
                expected_error_transactions=random_error_count(seed, 500),
            ))
        elif name.startswith("determinism_"):
            cases.append(case(
                name, category="determinism", generator="determinism",
                seed=42, transaction_count=500,
                stable_scenario_id="determinism_s42",
                expected_error_transactions=random_error_count(42, 500),
            ))
        elif name.startswith("legacy_vnet"):
            cases.append({
                "name": name,
                "suites": ["quick", "full"],
                "binary_role": "legacy",
                "expect": "pass",
                "inj_vnet": int(name[-1]),
                "timeout_seconds": 120,
            })
        else:
            cases.append(negative_case(name))
    return cases


def case(name, category, scenario=None, generator=None, args=None, seed=42,
         transaction_count=None, stable_scenario_id=None,
         expected_stall_mask=None, expected_error_transactions=0):
    limits = {
        "functional": (200000, 120),
        "ordering": (300000, 120),
        "backpressure": (500000, 180),
        "response_progress": (20000, 120),
        "determinism": (300000, 120),
        "random_quick": (2000000, 180),
        "random_full": (5000000, 300),
        "nightly": (20000000, 1200),
        "matrix": (5000000, 300),
    }
    max_cycles, timeout = limits[category]
    result = {
        "name": name,
        "suites": ["quick", "full"] if name in QUICK_CASES else ["full"],
        "binary_role": "axi",
        "expect": "pass",
        "category": category,
        "seed": seed,
        "max_network_cycles": max_cycles,
        "timeout_seconds": timeout,
        "args": args or [],
        "expected_stall_mask": expected_stall_mask or [],
        "expected_error_transactions": expected_error_transactions,
    }
    if scenario:
        result["scenario"] = scenario
    if generator:
        result["generator"] = generator
    if transaction_count is not None:
        result["transaction_count"] = transaction_count
    if stable_scenario_id:
        result["stable_scenario_id"] = stable_scenario_id
    return result


def negative_case(name):
    architected = {
        "n3a_unaligned_decerr": 1,
        "n8_unmapped_decerr": 1,
        "n9_lock_decerr": 1,
    }
    bounded = {
        "n11_fifo_full": "local_fifo",
        "n11_rob_full": "local_fifo",
        "n11_orphan_full": "orphan_or_quota",
    }
    if name in architected:
        expect = "architected_error"
        marker = "AXI_MESH_FUNCTIONAL_SCENARIO_PASS"
        max_cycles, timeout = 100000, 60
    elif name == "n10_source_w_without_aw":
        expect = "final_consistency_failure"
        marker = "AXI_FINAL_CONSISTENCY: source W burst without AW"
        max_cycles, timeout = 100000, 60
    elif name in bounded:
        expect = "pass"
        marker = "AXI_MESH_FUNCTIONAL_SCENARIO_PASS"
        max_cycles, timeout = 500000, 180
    else:
        expect = "fatal_init" if name in {
            "n1_static_zero_beat_count", "n1_static_257_beat_count",
            "n2_size", "n3b_4k_crossing", "n7_strict_false",
            "n12_vector_length", "n12_zero_depth",
        } else "fatal_runtime"
        marker = {
            "n1_static_zero_beat_count": "beat_count must be in [1,256]",
            "n1_static_257_beat_count": "beat_count must be in [1,256]",
            "n1_runtime_beat_count": "AXI_PROTOCOL: runtime beatCount",
            "n2_size": "AXI transaction SIZE exceeds data bus",
            "n3b_4k_crossing": "AXI INCR burst crosses a 4 KiB boundary",
            "n4_early_wlast": "AXI_PROTOCOL: WLAST position",
            "n4_late_wlast": "AXI_PROTOCOL: W burst has more beats",
            "n4_missing_wlast": "AXI_PROTOCOL: WLAST missing",
            "n5_duplicate_rbeat": "AXI_PROTOCOL: duplicate R beat",
            "n5_out_of_range_rbeat": "AXI_PROTOCOL: R packet beat index/count",
            "n6_duplicate_uid": "AXI_PROTOCOL: duplicate txnUid",
            "n6_unknown_response": "AXI_PROTOCOL: unknown response txnUid",
            "n7_strict_false": "AXI_MESH supports strict_protocol=true only",
            "n12_vector_length": "must contain exactly 5 non-empty integers",
            "n12_zero_depth": "entries must all be positive",
        }[name]
        max_cycles, timeout = 10000, 30
    result = {
        "name": name,
        "suites": ["quick", "full"],
        "binary_role": "axi",
        "expect": expect,
        "generator": "negative",
        "seed": 1,
        "stable_scenario_id": name,
        "max_network_cycles": max_cycles,
        "timeout_seconds": timeout,
        "expected_marker": marker,
        "expected_error_transactions": architected.get(name, 0),
        "expected_stall_mask": [bounded[name]] if name in bounded else [],
        "max_injected_packets_before_failure": 0 if expect.startswith("fatal") else None,
    }
    result["args"] = {
        "n7_strict_false": ["--axi-strict-protocol=false"],
        "n11_fifo_full": ["--axi-source-fifo-depths=1,1,1,1,1"],
        "n11_rob_full": [
            "--axi-b-rob-transactions=1",
            "--axi-max-outstanding-writes=1",
        ],
        "n11_orphan_full": [
            "--axi-orphan-w-transactions=1", "--axi-orphan-w-beats=1",
        ],
        "n12_vector_length": ["--garnet-buffers-per-vnet=1,1,1,1"],
        "n12_zero_depth": ["--garnet-buffers-per-vnet=1,1,0,1,1"],
    }.get(name, [])
    return result


def all_cases():
    cases = quick_cases()
    for profile in PROFILE_NAMES:
        for vcs in VC_COUNTS:
            for traffic in MATRIX_TRAFFIC:
                name = f"buffer_matrix_{profile}_vc{vcs}_{traffic}"
                cases.append(case(
                    name, category="matrix", generator="matrix",
                    stable_scenario_id=name,
                    args=[
                        f"--vcs-per-vnet={vcs}",
                        "--garnet-buffers-per-vnet=" + ",".join(
                            str(value) for value in {
                                "11111": [1, 1, 1, 1, 1],
                                "24224": [2, 4, 2, 2, 4],
                                "48448": [4, 8, 4, 4, 8],
                                "8168816": [8, 16, 8, 8, 16],
                            }[profile]
                        ),
                    ],
                ))
    for seed in RANDOM_FULL_SEEDS:
        name = f"rand_full_s{seed}"
        cases.append(case(
            name, category="random_full", generator="random", seed=seed,
            transaction_count=2000, stable_scenario_id=name,
            expected_error_transactions=random_error_count(seed, 2000),
        ))
    cases.append(case(
        "rand_nightly_s42_10000", category="nightly", generator="random",
        seed=42, transaction_count=10000,
        stable_scenario_id="rand_nightly_s42_10000",
        expected_error_transactions=random_error_count(42, 10000),
    ))
    return cases


def main():
    manifest = {
        "schema_version": 1,
        "unit_binaries": unit_entries(),
        "integration_cases": all_cases(),
    }
    output = HERE / "manifest.json"
    output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
