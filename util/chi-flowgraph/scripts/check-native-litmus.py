"""Check actual CPU samples, not merely a litmus program's PASS banner."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import configparser


def check(folder, variant, iterations, seed):
    errors = []
    def require(condition, message):
        if not condition:
            errors.append(message)
    result = json.loads((folder / "native-l2-result.json").read_text())
    log = (folder / "run.log").read_text()
    with (folder / "raw.jsonl").open(encoding="utf-8") as source:
        manifest = json.loads(next(source))
    program = manifest["program"]
    require(result.get("pass") is True and result.get("exitCode") == 0,
            "gem5 did not report successful CPU termination")
    require("exiting with last active thread context" in result.get("cause", ""),
            "all four CPUs must exit; a simulation time limit is not a pass")
    require(result.get("protocolOpcodeInjection") is False,
            "protocol opcode injection is prohibited")
    require("ChiNativeL2" in result.get("timedCpuPath", ""), "not the native L2 path")
    require(manifest.get("profile") == "h-native-256", "wrong wire profile")
    require(program.get("variant") == variant, "program variant mismatch")
    require(program.get("iterations") == iterations, "program iteration count mismatch")
    require(program.get("seed") == seed and result.get("simulationSeed") == seed,
            "workload or simulator seed mismatch")
    require(iterations > 0, "zero-iteration experiment")
    config = configparser.ConfigParser(interpolation=None)
    config.read(folder / "config.ini")
    types = Counter(config.get(section, "type", fallback="") for section in config.sections())
    require(types["ChiNativeL2"] == 4, "config.ini must contain four native L2 instances")
    require(types["Cache2ChiBridge"] == 0, "classic bridge present in actual configuration")
    require("NATIVE_LITMUS_FAIL" not in log and "_FAIL" not in log,
            "CPU workload reported a failure")
    hart_pass = [f"NATIVE_LITMUS_HART_{hart}_PASS" in log for hart in range(4)]
    require(all(hart_pass), "missing one or more CPU PASS banners")
    for hart in range(4):
        completed = re.findall(rf"^NATIVE_LITMUS_HART_{hart}_PASS completed=(\d+)$", log, re.MULTILINE)
        require(completed == [str(iterations)], f"hart {hart} completion count is missing or incorrect")
    summaries = [json.loads(line.removeprefix("NATIVE_LITMUS_RESULT "))
                 for line in log.splitlines() if line.startswith("NATIVE_LITMUS_RESULT ")]
    samples = [json.loads(line.removeprefix("NATIVE_LITMUS_SAMPLE "))
               for line in log.splitlines() if line.startswith("NATIVE_LITMUS_SAMPLE ")]
    require(len(summaries) == 1, "expected exactly one CPU summary")
    require(len(samples) == iterations, "missing or extra per-round CPU samples")
    require([sample.get("round") for sample in samples] == list(range(iterations)),
            "sample rounds must be unique, continuous and ordered")
    observed = Counter()
    placements = set()
    for sample in samples:
        values = sample.get("values", [])
        valid = len(values) == 4 and all(type(value) is int and value in (0, 1) for value in values)
        require(valid, f"round {sample.get('round')} has unknown, unwritten or invalid values")
        if valid:
            observed["".join(str(value) for value in values)] += 1
        readers = sample.get("readerHarts", [])
        require(len(readers) == 2 and len(set(readers)) == 2 and
                all(type(hart) is int and 0 <= hart < 4 for hart in readers),
                f"round {sample.get('round')} has invalid reader assignment")
        offsets = [int(sample[key], 0) if isinstance(sample[key], str) else sample[key]
                   for key in ("xOffset", "yOffset")]
        require(all(type(offset) is int and 0 <= offset < 65536 and offset % 64 == 0
                    for offset in offsets) and offsets[0] != offsets[1],
                "test variables must occupy separate, aligned shared cache lines")
        placements.add(tuple(offsets))
    histogram = {f"{value:04b}": observed[f"{value:04b}"] for value in range(16)}
    fenced = variant == "iriw-fenced"
    weak_count = histogram["1010"]
    if len(summaries) == 1:
        summary = summaries[0]
        for key, expected in (("variant", variant), ("iterations", iterations),
                              ("completed", iterations), ("seed", seed),
                              ("invalidValues", 0), ("errors", 0),
                              ("weakOutcomeCount", weak_count),
                              ("forbidden", weak_count if fenced else 0)):
            require(summary.get(key) == expected, f"CPU summary mismatch: {key}")
        require(summary.get("histogram") == histogram,
                "CPU histogram differs from independently counted samples")
        require(all(type(count) is int and count >= 0 for count in summary.get("histogram", {}).values()),
                "histogram counts must be nonnegative integers")
    require(sum(histogram.values()) == iterations, "histogram total does not match iterations")
    require(not fenced or weak_count == 0, "IRIW reader-fence forbidden outcome observed")
    report = dict(passed=not errors, variant=variant, iterations=iterations, seed=seed,
                  hartPass=hart_pass, tupleOrder=["P2.x", "P2.y", "P3.y", "P3.x"],
                  histogram=histogram, weakOutcomeCount=weak_count,
                  forbiddenCount=weak_count if fenced else 0,
                  completedSamples=len(samples), addressPlacements=len(placements),
                  cpuExitTick=result["cpuExitTick"], drainEndTick=result["drainEndTick"],
                  sharedPhysical=result["sharedPhysical"], errors=errors,
                  source=program, actualNativeL2Instances=types["ChiNativeL2"],
                  memoryModel="RISC-V RVWMO; relaxed IRIW 1010 is allowed; reader-fenced 1010 is forbidden",
                  scope="Observed CPU executions only; absence of an outcome does not prove all interleavings.",
                  logSha256=hashlib.sha256((folder / "run.log").read_bytes()).hexdigest())
    (folder / "litmus-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n",
                                               encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("passed", "variant", "iterations", "histogram",
                                                   "forbiddenCount", "cpuExitTick", "errors")}, indent=2))
    return not errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--variant", required=True, choices=["iriw", "iriw-fenced"])
    parser.add_argument("--iterations", type=int, default=128)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    try:
        ok = check(args.folder, args.variant, args.iterations, args.seed)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        print(f"Invalid litmus experiment: {error}", file=sys.stderr)
        ok = False
    sys.exit(0 if ok else 1)
