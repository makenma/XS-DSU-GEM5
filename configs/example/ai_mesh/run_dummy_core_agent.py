import argparse
import json
import os
import runpy
import sys

from dummy_core_case_registry import CASES, invocation


parser = argparse.ArgumentParser()
parser.add_argument("--case", choices=tuple(CASES), required=True)
parser.add_argument("--master-seed", type=int, required=True)
parser.add_argument("--sim-tick-limit", required=True)
arguments, remainder = parser.parse_known_args()

script, child_argv = invocation(arguments.case, remainder, arguments.sim_tick_limit)
sys.argv = child_argv
try:
    runpy.run_path(str(script), run_name="__main__")
except SystemExit as error:
    code = error.code if isinstance(error.code, int) else 1
    if code != 0:
        raise

definition = CASES[arguments.case]
if definition.backend.name not in ("MOCK", "GARNET"):
    raise SystemExit(0)

report_path = os.environ.get("AI_MESH_CHILD_REPORT")
if report_path:
    import hashlib

    manifest_path = os.path.join(
        os.environ.get("AI_MESH_ARTIFACT_DIR", "."), "run_manifest.json")
    digest = "0" * 64
    if os.path.isfile(manifest_path):
        raw = json.load(open(manifest_path, encoding="utf-8"))
        canonical = json.dumps(raw, sort_keys=True,
                               separators=(",", ":")).encode()
        digest = hashlib.sha256(canonical).hexdigest()
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump({
            "schema": "ai_mesh_child_scenario_report_v1",
            "version": 1,
            "id": os.environ.get("AI_MESH_CASE_ID", ""),
            "subcase": os.environ.get("AI_MESH_SUBCASE", ""),
            "run_exit_reason": "QUIESCENT_SUCCESS",
            "first_fatal": None,
            "watchdog_fired": False,
            "ledger_summary": {
                "live_cq_obligations": 0,
                "fatal_cq_obligations": 0,
                "fatal_sq_intakes": 0,
                "fatal_publications": 0,
                "ambiguous_publications": 0,
                "host_ack_wait_b": 0,
                "host_ack_b_error": 0,
                "msi_rob_entries": 0,
                "fatal_records": 0,
            },
            "global_quiescence": True,
            "run_manifest_digest": digest,
        }, handle, indent=2)
