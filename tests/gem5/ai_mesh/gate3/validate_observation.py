#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "util/mesh_ir"))

from mesh_ir.gate3_oracle import Gate3OracleError, load_observation, validate_observation


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("observation", type=Path)
    arguments = parser.parse_args()
    try:
        document = load_observation(arguments.observation)
        checks = validate_observation(document)
        result = {
            "schema": "ai_mesh_gate3_observation_result_v1",
            "version": 1,
            "status": "PASS",
            "id": document["id"],
            "subcase": document["subcase"],
            "checks": list(checks),
        }
        code = 0
    except (Gate3OracleError, OSError) as error:
        result = {
            "schema": "ai_mesh_gate3_observation_result_v1",
            "version": 1,
            "status": "FAIL",
            "detail": str(error),
        }
        code = 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
