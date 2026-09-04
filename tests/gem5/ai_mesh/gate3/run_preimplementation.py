#!/usr/bin/env python3

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[4]
PYTHON = str(Path(sys.executable).resolve())


def run(name, command):
    result = subprocess.run(
        command,
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return {
        "name": name,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def main():
    workdir = Path(tempfile.mkdtemp(prefix="ai-mesh-gate3-proto17-"))
    checks = [
        run(
            "agent_abi_generation",
            [PYTHON, "util/mesh_ir/mesh_ir/abi/generate_agent_abi.py", "--check"],
        ),
        run(
            "gate3_oracle_selftests",
            [
                PYTHON,
                "-m",
                "pytest",
                "util/mesh_ir/tests/unit/test_gate3_oracle.py",
                "util/mesh_ir/tests/unit/test_gate3_contract.py",
                "-q",
            ],
        ),
        run(
            "agent_abi_python",
            [
                PYTHON,
                "-m",
                "pytest",
                "util/mesh_ir/tests/unit/test_agent_protocol.py",
                "-q",
            ],
        ),
        run(
            "agent_abi_cpp",
            [
                "build/AXI_MESH/dev/ai_mesh/agent_protocol.test.opt",
                "--gtest_color=no",
            ],
        ),
        run(
            "mandatory_manifest",
            [PYTHON, "tests/gem5/ai_mesh/validate_manifest.py"],
        ),
        run(
            "proto17_selector",
            [
                PYTHON,
                "tests/gem5/ai_mesh/run_manifest_selector.py",
                "--gate",
                "3",
                "--id",
                "PROTO-17",
                "--workdir",
                str(workdir),
            ],
        ),
    ]
    readiness = run(
        "gate3_runtime_readiness",
        [PYTHON, "tests/gem5/ai_mesh/gate3/runtime_contract.py"],
    )
    checks.append(readiness)
    green = all(row["returncode"] == 0 for row in checks[:-1])
    try:
        readiness_result = json.loads(readiness["stdout"])
    except json.JSONDecodeError:
        readiness_result = None
    expected_red = (
        readiness["returncode"] == 1
        and readiness_result is not None
        and readiness_result["status"] == "RED"
        and readiness_result["failure_count"] > 0
    )
    status = "READY_FOR_IMPLEMENTATION" if green and expected_red else (
        "RUNTIME_READY" if green and readiness["returncode"] == 0 else "FAIL"
    )
    result = {
        "schema": "ai_mesh_gate3_preimplementation_result_v1",
        "version": 1,
        "status": status,
        "checks": checks,
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if status in ("READY_FOR_IMPLEMENTATION", "RUNTIME_READY") else 1


if __name__ == "__main__":
    raise SystemExit(main())
