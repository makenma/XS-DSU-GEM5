#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "util/mesh_ir"))

from mesh_ir.gate3_contract import gate3_readiness_failures


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
    failures = gate3_readiness_failures(manifest, arguments.registry)
    result = {
        "schema": "ai_mesh_gate3_runtime_readiness_v1",
        "version": 1,
        "status": "PASS" if not failures else "RED",
        "failure_count": len(failures),
        "failures": list(failures),
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
