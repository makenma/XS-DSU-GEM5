#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from axi_test_lib import UNIT_BINARIES, expected_unit_test_names


def fail(message):
    raise RuntimeError(message)


def load_manifest(path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        fail("unit manifest schema_version must be 1")
    entries = manifest.get("unit_binaries")
    if not isinstance(entries, list) or len(entries) != 10:
        fail("unit manifest must contain exactly 10 binaries")
    by_binary = {}
    for entry in entries:
        binary = entry.get("binary")
        if binary in by_binary:
            fail(f"duplicate unit binary {binary}")
        by_binary[binary] = entry
    if set(by_binary) != set(UNIT_BINARIES):
        fail("unit binary set does not match the implementation contract")
    expected_all = set()
    for binary, expected_suites in UNIT_BINARIES.items():
        entry = by_binary[binary]
        if set(entry.get("suites", ())) != set(expected_suites):
            fail(f"suite set mismatch for {binary}")
        expected = {
            f"{suite}.{test}"
            for suite, tests in expected_suites.items()
            for test in tests
        }
        if set(entry.get("tests", ())) != expected:
            fail(f"test-name set mismatch for {binary}")
        expected_all.update(expected)
    if expected_all != expected_unit_test_names():
        fail("unit manifest must exact-match the required unit tests")
    return entries


def parse_list_tests(output):
    tests = set()
    suite = None
    for raw in output.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("Running main()"):
            continue
        if not line.startswith(" ") and line.endswith("."):
            suite = line[:-1].split(" #", 1)[0]
            continue
        if line.startswith("  ") and suite:
            test = line.strip().split(" #", 1)[0]
            tests.add(f"{suite}.{test}")
    return tests


def xml_results(path):
    root = ET.parse(path).getroot()
    cases = root.findall(".//testcase")
    failed = sum(1 for case in cases if case.find("failure") is not None)
    skipped = sum(1 for case in cases if case.find("skipped") is not None)
    disabled = sum(
        1 for case in cases
        if case.attrib.get("status") in ("notrun", "disabled")
        or case.attrib.get("name", "").startswith("DISABLED_")
    )
    names = {
        f"{case.attrib['classname']}.{case.attrib['name']}" for case in cases
    }
    return {
        "names": names,
        "run": len(cases),
        "failed": failed,
        "skipped": skipped,
        "disabled": disabled,
    }


def run_binary(binary, expected, outdir, timeout_seconds):
    listed = subprocess.run(
        [str(binary), "--gtest_list_tests"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
        check=False,
    )
    if listed.returncode != 0:
        fail(f"{binary}: --gtest_list_tests exited {listed.returncode}")
    discovered = parse_list_tests(listed.stdout)
    if discovered != expected:
        fail(
            f"{binary}: discovered test set mismatch: "
            f"missing={sorted(expected - discovered)} "
            f"extra={sorted(discovered - expected)}"
        )
    if any(".DISABLED_" in name for name in discovered):
        fail(f"{binary}: disabled test discovered")

    case_dir = outdir / binary.stem
    case_dir.mkdir(parents=True, exist_ok=True)
    xml_path = case_dir / "gtest.xml"
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            [str(binary), "--gtest_color=no",
             f"--gtest_output=xml:{xml_path}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = completed.returncode
        output = completed.stdout
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        output = (exc.stdout or "") + (exc.stderr or "")
    duration = time.monotonic() - started
    (case_dir / "output.log").write_text(output, encoding="utf-8")
    if timed_out or exit_code != 0 or not xml_path.is_file():
        fail(f"{binary}: run failed exit={exit_code} timeout={timed_out}")
    parsed = xml_results(xml_path)
    if parsed["names"] != expected:
        fail(f"{binary}: executed test set differs from expected")
    if parsed["run"] == 0 or parsed["failed"] or parsed["skipped"] or \
            parsed["disabled"]:
        fail(f"{binary}: failed/skip/disabled/zero-test result")
    return {
        "binary": str(binary),
        "expected": sorted(expected),
        "discovered": sorted(discovered),
        "run": parsed["run"],
        "passed": parsed["run"],
        "failed": 0,
        "skipped": 0,
        "disabled": 0,
        "timed_out": 0,
        "duration_seconds": duration,
        "exit_code": exit_code,
        "xml": str(xml_path),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--variant", choices=("opt", "debug"), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--outdir", type=Path,
                        default=Path("m5out/axi-unit-suite"))
    args = parser.parse_args()

    try:
        entries = load_manifest(args.manifest)
        args.outdir.mkdir(parents=True, exist_ok=True)
        results = []
        for entry in entries:
            relative = entry["binary"]
            if args.variant != "opt":
                relative = relative.removesuffix(".opt") + f".{args.variant}"
            binary = args.build_dir / relative
            if not binary.is_file():
                fail(f"missing unit binary: {binary}")
            results.append(run_binary(
                binary,
                set(entry["tests"]),
                args.outdir,
                int(entry.get("timeout_seconds", 60)),
            ))
        aggregate = {
            "schema_version": 1,
            "status": "pass",
            "binary_count": len(results),
            "expected": sum(len(item["expected"]) for item in results),
            "discovered": sum(len(item["discovered"]) for item in results),
            "run": sum(item["run"] for item in results),
            "passed": sum(item["passed"] for item in results),
            "failed": 0,
            "skipped": 0,
            "disabled": 0,
            "timed_out": 0,
            "binaries": results,
        }
        required = len(expected_unit_test_names())
        if aggregate["expected"] != required or aggregate["run"] != required:
            fail("aggregate unit count must match the required tests")
        (args.outdir / "unit_suite_result.json").write_text(
            json.dumps(aggregate, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"AXI_UNIT_SUITE_PASS binaries={len(results)} tests={required}")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"AXI_UNIT_SUITE_FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
