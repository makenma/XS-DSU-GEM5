import argparse
import hashlib
import itertools
import json
import os
import subprocess
from pathlib import Path

from axi_test_lib import UNIT_BINARIES
from run_axi_unit_tests import parse_list_tests, xml_results


REPO = Path(__file__).resolve().parents[3]
CORPUS_SOURCES = Path("src/mem/axi/axi_behavior_sources.json")
BINARY = "mem/axi/axi_behavior_trace.test.opt"


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def corpus_source_identity(tree):
    manifest = tree / CORPUS_SOURCES
    document = json.loads(manifest.read_text())
    names = document.get("files")
    if document.get("schema_version") != 1 or not isinstance(names, list) or not names or \
            any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
        raise ValueError("invalid behavior corpus source manifest")
    if any(Path(name).name != name or Path(name).suffix not in (".cc", ".hh") for name in names):
        raise ValueError("behavior corpus sources must be local C++ files")
    files = {str(CORPUS_SOURCES): sha256(manifest)}
    for name in names:
        relative = CORPUS_SOURCES.parent / name
        files[str(relative)] = sha256(tree / relative)
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return dict(files=files, sha256=hashlib.sha256(encoded).hexdigest())


def run_corpus(binary, directory, timeout):
    directory.mkdir()
    traces = directory / "traces"
    traces.mkdir()
    expected = {
        f"{suite}.{test}"
        for suite, names in UNIT_BINARIES[BINARY].items()
        for test in names
    }
    environment = dict(os.environ, AXI_BEHAVIOR_TRACE_DIR=str(traces.resolve()))
    listed = subprocess.run(
        [str(binary), "--gtest_list_tests"], env=environment,
        capture_output=True, text=True, timeout=timeout, check=False)
    if listed.returncode or parse_list_tests(listed.stdout) != expected:
        raise RuntimeError(f"{binary}: behavior corpus discovery differs")
    xml = directory / "gtest.xml"
    process = subprocess.run(
        [str(binary), "--gtest_color=no", f"--gtest_output=xml:{xml.resolve()}"],
        env=environment, capture_output=True, text=True,
        timeout=timeout, check=False)
    (directory / "output.log").write_text(process.stdout + process.stderr)
    if process.returncode or not xml.is_file():
        raise RuntimeError(f"{binary}: corpus failed; see {directory / 'output.log'}")
    results = xml_results(xml)
    if results["names"] != expected or any(
            results[key] for key in ("failed", "skipped", "disabled")):
        raise RuntimeError(f"{binary}: behavior corpus did not pass completely")
    expected_files = {f"{name.split('.', 1)[1]}.tsv" for name in expected}
    actual_files = {path.name for path in traces.iterdir()}
    if actual_files != expected_files:
        raise RuntimeError(f"{binary}: trace file set differs")
    for path in traces.iterdir():
        if not path.stat().st_size:
            raise RuntimeError(f"{binary}: empty trace {path.name}")
    return traces


def compare_traces(baseline, candidate):
    files = []
    for original in sorted(baseline.iterdir()):
        updated = candidate / original.name
        rows = 0
        size = 0
        before_hash = hashlib.sha256()
        after_hash = hashlib.sha256()
        with original.open("rb") as left, updated.open("rb") as right:
            for rows, (before, after) in enumerate(
                    itertools.zip_longest(left, right), start=1):
                if before != after:
                    return files, dict(
                        file=original.name, line=rows,
                        baseline=repr(before) if before is not None else None,
                        candidate=repr(after) if after is not None else None)
                before_hash.update(before)
                after_hash.update(after)
                size += len(before)
        baseline_sha256 = before_hash.hexdigest()
        candidate_sha256 = after_hash.hexdigest()
        if baseline_sha256 != candidate_sha256:
            raise RuntimeError("behavior trace raw SHA256 differs")
        files.append(dict(file=original.name, rows=rows, bytes=size,
                          sha256=baseline_sha256, candidate_sha256=candidate_sha256))
    return files, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline-tree", type=Path, required=True)
    parser.add_argument("--candidate-tree", type=Path, default=REPO)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=False)
    report = dict(schema="axi_behavior_comparison_v2", status="fail")
    try:
        report["baseline_corpus_sources"] = corpus_source_identity(args.baseline_tree)
        report["candidate_corpus_sources"] = corpus_source_identity(args.candidate_tree)
        if report["baseline_corpus_sources"] != report["candidate_corpus_sources"]:
            raise RuntimeError("baseline and candidate corpus sources differ")
        report["baseline_binary_sha256"] = sha256(args.baseline)
        report["candidate_binary_sha256"] = sha256(args.candidate)
        baseline = run_corpus(args.baseline.resolve(), args.outdir / "baseline",
                              args.timeout_seconds)
        candidate = run_corpus(args.candidate.resolve(), args.outdir / "candidate",
                               args.timeout_seconds)
        report["traces"], mismatch = compare_traces(baseline, candidate)
        if mismatch is not None:
            report["first_difference"] = mismatch
            raise RuntimeError(f"trace differs: {mismatch['file']}:{mismatch['line']}")
        report["status"] = "pass"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    (args.outdir / "comparison.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    if report["status"] != "pass":
        parser.exit(1, report.get("error", "behavior comparison failed") + "\n")
    print(f"AXI behavior traces match: {len(report['traces'])} files")


if __name__ == "__main__":
    main()
