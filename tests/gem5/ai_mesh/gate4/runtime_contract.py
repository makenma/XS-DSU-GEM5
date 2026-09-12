#!/usr/bin/env python3

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "util/mesh_ir"))
sys.path.insert(0, str(REPO / "tests/gem5/ai_mesh"))
sys.path.insert(0, str(REPO / "configs/example/ai_mesh"))

from mesh_ir.gate4_contract import (
    e2e_e_agent_only_subset,
    gate4_coverage_gaps,
    gate4_readiness_failures,
)


def _selector_module():
    spec = importlib.util.spec_from_file_location(
        "gate4_selector",
        REPO / "tests/gem5/ai_mesh/run_manifest_selector.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scenario_digest_failures(cases, selector):
    failures = []
    for case in cases:
        if case["id"] not in ("PROTO-21", "PROTO-22", "PROTO-27") and not case[
            "id"
        ].startswith("HOST-"):
            continue
        for subcase in case["subcases"]:
            execution = subcase["execution"]
            if execution["runner"] != "GEM5":
                continue
            if (
                execution["config_script"]
                != "configs/example/ai_mesh/run_gate4_agent.py"
            ):
                failures.append(f"{case['id']}/{subcase['name']}: wrong script")
                continue
            scenario = selector._gem5_scenario(
                execution, [], REPO / "tests/gem5/ai_mesh"
            )
            digests = scenario["digests"]
            for name in (
                "workload_plan",
                "control_plan",
                "command_identity",
                "host_task_identity",
                "host_arena_object_plan",
                "capacity_plan",
            ):
                if digests.get(name) is None:
                    failures.append(
                        f"{case['id']}/{subcase['name']}: digest {name} is null"
                    )
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO / "tests/gem5/ai_mesh/mandatory_case_manifest.yaml",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=REPO / "configs/example/ai_mesh/dummy_core_case_registry.py",
    )
    arguments = parser.parse_args()
    manifest = yaml.safe_load(arguments.manifest.read_text(encoding="utf-8"))
    failures = list(gate4_readiness_failures(manifest, arguments.registry))
    selector = _selector_module()
    cases = [case for case in manifest["cases"] if case["earliest_gate"] <= 4]
    failures.extend(_scenario_digest_failures(cases, selector))
    subset = e2e_e_agent_only_subset()
    known = {
        f"{case['id']}/{subcase['name']}"
        for case in cases
        for subcase in case["subcases"]
    }
    for subcase_id in subset["subcase_ids"]:
        if subcase_id not in known:
            failures.append(f"E2E-E agent-only subset references {subcase_id}")
    result = {
        "schema": "ai_mesh_gate4_runtime_readiness_v1",
        "version": 1,
        "status": "PASS" if not failures else "RED",
        "failure_count": len(failures),
        "failures": list(failures),
        "coverage_gap_count": sum(
            len(gaps) for gaps in gate4_coverage_gaps().values()
        ),
        "coverage_gaps": {
            case_id: list(gaps)
            for case_id, gaps in sorted(gate4_coverage_gaps().items())
        },
        "e2e_e_agent_only_subset": subset,
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
