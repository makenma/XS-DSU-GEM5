"""Run one focused bare-FS CBO reproducer on an explicitly selected gem5 ELF."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--gem5", type=Path, required=True)
parser.add_argument("--case", choices=("inval", "clean", "flush", "flush-fault"), default="flush")
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--expect-original-assert", action="store_true",
                    help="Only for the immutable old model: expect the proven retry assertion; never mark PASS")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
programs = root / "build/native-cbo-tlb-program"
manifest = json.loads((programs / "manifest.json").read_text())
variant = next(row for row in manifest["variants"] if row["case"] == args.case)
args.output.mkdir(parents=True, exist_ok=False)
binary = args.gem5.resolve()
firmware = programs / variant["elf"]
assert hashlib.sha256(firmware.read_bytes()).hexdigest() == variant["sha256"]
env = dict(os.environ)
env["CHI_GEM5_DIR"] = "/root/chi-flowgraph-work/gem5"
env["CHI_FLOWGRAPH_OUT"] = str((args.output / "raw.jsonl").resolve())
command = [str(binary), "--outdir=" + str(args.output.resolve()), "--remote-gdb-port=0",
           "--debug-flags=IQ,StorePipeline,Schedule", "--debug-file=cbo-tlb-debug.log",
           str(root / "native/gem5/native_l2_fs.py"), "--firmware", str(firmware),
           "--max-ticks", "100000000", "--progress-ticks", "100000000", "--zicbom"]
with (args.output / "run.log").open("w") as log:
    completed = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
runlog = (args.output / "run.log").read_text(errors="replace")
debugpath = args.output / "cbo-tlb-debug.log"
debug = debugpath.read_text(errors="replace") if debugpath.exists() else ""
terminal = args.output / "system.platform.terminal"
serial = terminal.read_text(errors="replace") if terminal.exists() else ""
rows = [json.loads(line.removeprefix("CBO_TLB_RESULT ")) for line in serial.splitlines()
        if line.startswith("CBO_TLB_RESULT ")]
cold_pc = int(variant["coldCboPc"], 16)
deferred = [line for line in debug.splitlines() if "Deferred mem inst" in line
            and "ready to execute" in line and hex(cold_pc)[2:] in line.lower()]
failure = "IssueQue::retryMem" in runlog and "!inst->isNonSpeculative()" in runlog
final_path = args.output / "native-linux-result.json"
final_result = json.loads(final_path.read_text()) if final_path.exists() else None
exit_evidence = None if final_result is None else {
    key: final_result.get(key) for key in ("tick", "cause", "exitCode", "gem5Sha256")}
digest = hashlib.sha256()
with binary.open("rb") as executable:
    for chunk in iter(lambda: executable.read(1024 * 1024), b""):
        digest.update(chunk)
gem5_sha256 = digest.hexdigest()
proper_exit = (exit_evidence is not None and exit_evidence["exitCode"] == 0
               and str(exit_evidence["cause"]).startswith("m5_exit instruction encountered")
               and exit_evidence["gem5Sha256"] == gem5_sha256)
maintenance_requests = []
trace_checked = False
if proper_exit:
    with (args.output / "raw.jsonl").open() as trace:
        trace_rows = [json.loads(line) for line in trace if line.strip()]
    trace_checked = (bool(trace_rows) and trace_rows[0].get("type") == "manifest"
                     and trace_rows[0].get("linux", {}).get("payload", {}).get("sha256") == variant["sha256"])
    maintenance_requests = [row for row in trace_rows if row.get("kind") == "flit"
                            and row.get("observation") == "TX" and row.get("channel") == "REQ"
                            and row.get("boundary") == "system.l20.chi_side"
                            and row.get("fields", {}).get("opcode") in (8, 9, 10)]
    if args.case == "flush-fault":
        trace_checked = trace_checked and not maintenance_requests
    else:
        expected_opcode = {"clean": 8, "flush": 9, "inval": 10}[args.case]
        trace_checked = trace_checked and len(maintenance_requests) == 1 and all(
            row["fields"]["opcode"] == expected_opcode and int(row["address"], 0) == 0x80020000
            for row in maintenance_requests)
result = {"kind": manifest["kind"], "notLinuxSuite": True,
          "case": args.case, "hostReturnCode": completed.returncode,
          "gem5": str(binary), "gem5Sha256": gem5_sha256,
          "firmwareSha256": variant["sha256"], "simulatorExit": exit_evidence,
          "coldCboPc": variant["coldCboPc"], "runtimeDeferredTranslation": bool(deferred),
          "deferredEvidence": deferred[-3:], "originalRetryAssertion": failure,
          "nativeMaintenanceTraceChecked": trace_checked,
          "nativeMaintenanceRequests": [{key: row.get(key) for key in ("id", "tick", "address", "opcode")}
                                        for row in maintenance_requests],
          "guestResults": rows,
          "passed": completed.returncode == 0 and proper_exit and trace_checked and len(rows) == 1 and rows[0].get("passed") is True
                    and rows[0].get("case") == args.case
                    and rows[0].get("expectedTrap") == variant["expectedTrap"]
                    and rows[0].get("pcChecked") is True and bool(deferred)
                    and (args.case != "flush-fault" or rows[0].get("tvalChecked") is True),
          "scope": "One active hart, four configured KMH/native L2; other harts park. Not a Linux boot or multicore suite result.",
          "command": command}
result["expectedOriginalAssertion"] = args.expect_original_assert
result["expectedOriginalAssertionMatched"] = (args.expect_original_assert and failure
                                              and completed.returncode != 0 and bool(deferred)
                                              and not rows and final_result is None)
(args.output / "cbo-tlb-result.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
raise SystemExit(0 if result["passed"] or result["expectedOriginalAssertionMatched"] else 1)
