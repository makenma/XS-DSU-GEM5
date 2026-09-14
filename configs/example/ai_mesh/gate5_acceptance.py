"""Gate 5 acceptance entry: contract/manifest consistency and gaps.

Prints the Gate 5 registration state; a non-empty gap list means a mandatory
clause is still a placeholder and the gate cannot be claimed.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "util" / "mesh_ir"))

import yaml

from mesh_ir.gate5_contract import (  # noqa: E402
    GATE5_CASES,
    GATE5_PLACEHOLDER_IDS,
    coverage_gaps,
    gate5_readiness_failures,
)

REGISTRY = REPO / "configs/example/ai_mesh/dummy_core_case_registry.py"
MANIFEST = REPO / "tests/gem5/ai_mesh/mandatory_case_manifest.yaml"


def load_manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def registered_backend_cases() -> tuple:
    spec = importlib.util.spec_from_file_location("gate5_acceptance_registry",
                                                 REGISTRY)
    registry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registry)
    return tuple(name for name, definition in registry.CASES.items()
                 if definition.backend.name == "GATE5")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    manifest = load_manifest()
    failures = gate5_readiness_failures(manifest, REGISTRY)
    gaps = coverage_gaps(manifest)
    backend_cases = registered_backend_cases()
    if arguments.json:
        import json
        print(json.dumps({
            "wired_ids": sorted(GATE5_CASES),
            "subcases": sum(len(value) for value in GATE5_CASES.values()),
            "backend_cases": list(backend_cases),
            "failures": list(failures),
            "coverage_gaps": list(gaps),
        }, indent=2, sort_keys=True))
        return 1 if failures or gaps else 0
    for failure in failures:
        print(f"CONTRACT-FAILURE: {failure}")
    for gap in gaps:
        print(f"coverage-gap: {gap}")
    print(f"Gate 5 registered logical IDs: {len(GATE5_CASES)}/"
          f"{len(GATE5_CASES)}, placeholders: "
          f"{len(GATE5_PLACEHOLDER_IDS)}, "
          f"subcases: {sum(len(v) for v in GATE5_CASES.values())}, "
          f"GATE5 backend cases: {len(backend_cases)}, "
          f"coverage_gaps: {len(gaps)}")
    return 1 if failures or gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
