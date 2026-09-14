"""Gate 5 runtime capability check (coding spec 12 exit checklist).

Reports which mandatory Gate 5 capabilities are wired today and which are
still missing, so `coverage_gaps` is derived from real checks instead of a
hand-written empty list.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "util" / "mesh_ir"))

import yaml  # noqa: E402

from mesh_ir.gate5_contract import (  # noqa: E402
    GATE5_CASES,
    coverage_gaps,
)

MANIFEST = REPO / "tests/gem5/ai_mesh/mandatory_case_manifest.yaml"
REGISTRY = REPO / "configs/example/ai_mesh/dummy_core_case_registry.py"
OVERLAY_IMAGE = (REPO / "tests/gem5/ai_mesh/fixtures/gate5"
                 / "moe_dual_overlay_objects.bin")
CACHED_OVERLAY_IMAGE = (REPO / "tests/gem5/ai_mesh/fixtures/gate5"
                        / "moe_dual_overlay_cached.bin")
RUNTIME_CONTRACT = (REPO / "configs/example/ai_mesh"
                    / "run_mesh_dma_garnet.py")


def capability_checks() -> list[tuple[str, bool, str]]:
    registry_spec = importlib.util.spec_from_file_location(
        "gate5_runtime_registry", REGISTRY)
    registry = importlib.util.module_from_spec(registry_spec)
    registry_spec.loader.exec_module(registry)
    runner_source = RUNTIME_CONTRACT.read_text(encoding="utf-8")
    checks = [
        ("gate5_backend_registered",
         any(definition.backend.name == "GATE5"
             for definition in registry.CASES.values()),
         "Backend.GATE5 case registered in the case registry"),
        ("overlay_image_argument", "--overlay-image" in runner_source,
         "the GEM5 runner installs a materialized overlay image"),
        ("sram_partition_plumbing",
         "sram_partition_kinds" in runner_source,
         "the GEM5 runner forwards the arch SRAM partitions"),
        ("overlay_transport_reconciliation",
         "moe_overlay_traffic" in runner_source,
         "overlay descriptors are reconciled against actual traffic"),
        ("overlay_image_fixture", OVERLAY_IMAGE.exists(),
         "a materialized overlay image exists for the E2E-C program"),
        ("cached_overlay_image_fixture", CACHED_OVERLAY_IMAGE.exists(),
         "a cache-slot-backed overlay image exists for the cached policy"),
        ("weight_policy_plumbing", "--weight-policy" in runner_source,
         "the GEM5 runner selects the weight residency policy"),
        ("cache_residency_reconciliation",
         "moe_cache_fill_traffic" in runner_source,
         "cache fills are reconciled with slots and actual traffic"),
        ("canonical_projection_reconciliation",
         "moe_canonical_projection" in runner_source,
         "overlay traffic is reconciled with the materialized graph"),
        ("timing_ab_case", "moe_dual_timing" in runner_source,
         "a timing-variant arm re-runs the same canonical projection"),
        ("gate5_contract_registered", bool(GATE5_CASES),
         "logical IDs registered with real subcases"),
    ]
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    checks = capability_checks()
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    gaps = coverage_gaps(manifest)
    missing = [name for name, ok, _detail in checks if not ok]
    if arguments.json:
        import json
        print(json.dumps({
            "capabilities": {name: ok for name, ok, _ in checks},
            "missing_capabilities": missing,
            "coverage_gaps": list(gaps),
        }, indent=2, sort_keys=True))
    else:
        for name, ok, detail in checks:
            print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
        for gap in gaps:
            print(f"coverage-gap: {gap}")
        print(f"capability_gaps: {len(missing)}; coverage_gaps: {len(gaps)}")
    return 1 if missing or gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
