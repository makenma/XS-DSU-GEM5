#!/usr/bin/env python3
"""Run and analyse 6x4 CHI HN-F replacement-policy experiments.

The runner intentionally uses only the Python standard library.  It discovers
weighted XiangShan GCPT slices for libquantum and omnetpp, runs every slice
under both SLC replacement policies, records an immutable manifest, and turns
gem5 statistics into CSV/JSON inputs for the presentation generator.

The ``run-se`` subcommand runs public, statically linked RISC-V binaries as
explicitly non-SPEC-compliant proxy workloads when licensed SPEC checkpoints
are unavailable.

Typical usage::

    python3 util/xs_scripts/chi_hnf_spec06.py run \
      --checkpoint-root /path/to/zstd-checkpoint-0-0-0 \
      --restorer /path/to/gcpt.bin \
      --output results/chi-hnf-spec06

    python3 util/xs_scripts/chi_hnf_spec06.py analyze \
      --output results/chi-hnf-spec06
"""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Iterable, Mapping, Sequence


DEFAULT_WORKLOADS = ("libquantum", "omnetpp")
DEFAULT_POLICIES = ("pseudo_random", "lru")
SUPPORTED_POLICIES = ("pseudo_random", "lru", "lsu")
STAT_LINE = re.compile(
    r"^(?P<name>\S+)\s+"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|nan|inf|-inf)"
    r"(?:\s|$)",
    re.IGNORECASE,
)
WEIGHT_IN_NAME = re.compile(r"_(?P<weight>0(?:\.\d+)?|1(?:\.0+)?)_?$")
HNF_STAT = re.compile(
    r"^system\.home_node(?P<index>\d*)\.slcsf\.stats\.(?P<metric>.+)$"
)
ROUTER_STAT = re.compile(
    r"^system\.chi_routers(?P<index>\d+)\.trafficStats\.(?P<metric>.+)$"
)


@dataclass(frozen=True)
class Slice:
    workload: str
    slice_id: str
    checkpoint: str
    weight: float


@dataclass(frozen=True)
class Run:
    workload: str
    slice_id: str
    checkpoint: str
    weight: float
    policy: str
    seed: int
    outdir: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    def strict_json(item):
        if isinstance(item, float) and not math.isfinite(item):
            return None
        if isinstance(item, Mapping):
            return {key: strict_json(element) for key, element in item.items()}
        if isinstance(item, (list, tuple)):
            return [strict_json(element) for element in item]
        return item

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            strict_json(value), indent=2, sort_keys=True, allow_nan=False
        ) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest_inputs(manifest: Mapping[str, object]) -> None:
    """Reject analysis when a hashed experiment input has changed.

    The manifest is the provenance boundary for public-source proxy runs.
    Recording hashes is not sufficient if analysis silently consumes results
    after the executable, configuration, or model input has been replaced.
    Older checkpoint manifests did not carry hashes and remain supported.
    """

    groups = (
        ("binary_sha256", "binaries"),
        ("input_sha256", "input_files"),
        ("tool_sha256", "tool_files"),
    )
    for hash_key, path_key in groups:
        expected_group = manifest.get(hash_key)
        if expected_group is None:
            continue
        if not isinstance(expected_group, Mapping):
            raise ValueError(f"manifest field {hash_key} must be an object")
        paths = manifest.get(path_key) if path_key is not None else manifest
        # Schema-v2 manifests written before tool_files stored the gem5 and
        # config paths at the top level. Preserve read compatibility.
        if hash_key == "tool_sha256" and paths is None:
            paths = manifest
        if not isinstance(paths, Mapping):
            raise ValueError(
                f"manifest field {path_key or 'root'} must provide paths for "
                f"{hash_key}"
            )
        for name, expected in expected_group.items():
            path_value = paths.get(name)
            if not isinstance(path_value, str):
                raise ValueError(
                    f"manifest has no path for {hash_key}.{name}"
                )
            path = Path(path_value)
            if not path.is_file():
                raise FileNotFoundError(
                    f"hashed experiment input is missing: {path}"
                )
            actual = file_sha256(path)
            if actual != expected:
                raise ValueError(
                    f"hashed experiment input changed: {name} ({path}); "
                    f"expected {expected}, got {actual}"
                )


def preserve_manifest(
    path: Path, manifest: Mapping[str, object], force: bool
) -> Mapping[str, object]:
    """Write a new manifest or verify that a resumed run is identical.

    A successful slice may be skipped during resume, so silently replacing the
    manifest with a different checkpoint set, policy, or binary would mix
    incompatible measurements.  Only the creation timestamp is allowed to
    differ.  ``--force`` intentionally starts a fresh experiment definition.
    """

    if path.is_file() and not force:
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"experiment manifest is invalid: {path}") from error
        previous_stable = dict(previous)
        current_stable = dict(manifest)
        previous_stable.pop("created_at", None)
        current_stable.pop("created_at", None)
        if previous_stable != current_stable:
            raise ValueError(
                "output already contains a different experiment manifest; "
                "choose another --output or pass --force"
            )
        return previous

    write_json(path, manifest)
    return manifest


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    if not cleaned:
        raise ValueError(f"cannot form a safe output component from {value!r}")
    return cleaned


def checkpoint_weight(path: Path) -> float:
    match = WEIGHT_IN_NAME.search(path.stem)
    if not match:
        raise ValueError(
            f"checkpoint filename does not contain a SimPoint weight: {path}"
        )
    weight = float(match.group("weight"))
    if not 0.0 < weight <= 1.0:
        raise ValueError(f"invalid SimPoint weight {weight} in {path}")
    return weight


def discover_slices(root: Path, workloads: Sequence[str]) -> list[Slice]:
    if not root.is_dir():
        raise FileNotFoundError(f"checkpoint root does not exist: {root}")

    found: list[Slice] = []
    all_checkpoints = sorted(
        path for path in root.rglob("*.zstd") if path.is_file()
    )
    for workload in workloads:
        workload_lower = workload.lower()
        candidates = [
            path
            for path in all_checkpoints
            if any(
                workload_lower == parent.name.lower()
                or parent.name.lower().startswith(workload_lower + "_")
                for parent in path.parents
                if parent != root.parent
            )
        ]
        if not candidates:
            raise FileNotFoundError(
                f"no .zstd checkpoint found for {workload} below {root}"
            )
        for path in candidates:
            relative = path.relative_to(root)
            slice_token = safe_component(
                "_".join(relative.with_suffix("").parts[-3:])
            )
            found.append(
                Slice(
                    workload=workload,
                    slice_id=slice_token,
                    checkpoint=str(path.resolve()),
                    weight=checkpoint_weight(path),
                )
            )

    identities = [(item.workload, item.slice_id) for item in found]
    if len(identities) != len(set(identities)):
        raise ValueError("checkpoint discovery produced duplicate slice IDs")
    return sorted(found, key=lambda item: (item.workload, item.slice_id))


def validate_slice_coverage(
    slices: Sequence[Slice], workloads: Sequence[str], tolerance: float = 1e-4
) -> None:
    """Require a complete SimPoint weight set for every requested workload."""

    for workload in workloads:
        selected = [item for item in slices if item.workload == workload]
        if not selected:
            raise ValueError(f"no checkpoint slices selected for {workload}")
        weight_sum = sum(item.weight for item in selected)
        if not math.isclose(weight_sum, 1.0, rel_tol=0.0, abs_tol=tolerance):
            raise ValueError(
                f"{workload} SimPoint weights sum to {weight_sum:.9f}, not 1; "
                "the checkpoint set is incomplete or contains extra slices"
            )


def canonical_policy(policy: str) -> str:
    """Treat the user-facing ``lsu`` spelling as the LRU compatibility alias."""

    if policy not in SUPPORTED_POLICIES:
        raise ValueError(f"unsupported SLC replacement policy: {policy}")
    return "lru" if policy == "lsu" else policy


def build_runs(
    slices: Sequence[Slice], output: Path, policies: Sequence[str], seed: int
) -> list[Run]:
    return [
        Run(
            workload=item.workload,
            slice_id=item.slice_id,
            checkpoint=item.checkpoint,
            weight=item.weight,
            policy=policy,
            seed=seed,
            outdir=str(
                (output / "runs" / policy / item.workload / item.slice_id).resolve()
            ),
        )
        for policy in policies
        for item in slices
    ]


def run_command(
    run: Run,
    gem5: Path,
    config: Path,
    restorer: Path,
    extra_args: Sequence[str],
    force: bool,
) -> dict[str, object]:
    outdir = Path(run.outdir)
    status_path = outdir / "run.json"
    if not force and status_path.is_file() and (outdir / "stats.txt").is_file():
        prior = json.loads(status_path.read_text(encoding="utf-8"))
        if prior.get("returncode") == 0:
            return {**prior, "skipped": True}

    outdir.mkdir(parents=True, exist_ok=True)
    command = [
        str(gem5),
        f"--outdir={outdir}",
        str(config),
        f"--generic-rv-cpt={run.checkpoint}",
        f"--gcpt-restorer={restorer}",
        "--disable-difftest",
        f"--slc-replacement-policy={run.policy}",
        f"--slc-replacement-seed={run.seed}",
        *extra_args,
    ]
    started = utc_now()
    with (outdir / "simout").open("w", encoding="utf-8") as stdout, (
        outdir / "simerr"
    ).open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(
            command,
            cwd=config.parents[2],
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
    status: dict[str, object] = {
        **asdict(run),
        "command": command,
        "started_at": started,
        "finished_at": utc_now(),
        "returncode": completed.returncode,
        "skipped": False,
    }
    write_json(status_path, status)
    return status


def run_se_command(
    run: Run,
    gem5: Path,
    config: Path,
    workload_options: Mapping[str, str],
    extra_args: Sequence[str],
    force: bool,
) -> dict[str, object]:
    """Run one public static ELF through the syscall-emulation entry point."""

    outdir = Path(run.outdir)
    status_path = outdir / "run.json"
    if not force and status_path.is_file() and (outdir / "stats.txt").is_file():
        prior = json.loads(status_path.read_text(encoding="utf-8"))
        if prior.get("returncode") == 0:
            return {**prior, "skipped": True}

    outdir.mkdir(parents=True, exist_ok=True)
    command = [
        str(gem5),
        f"--outdir={outdir}",
        str(config),
        *extra_args,
        f"--cmd={run.checkpoint}",
        f"--options={workload_options.get(run.workload, '')}",
        f"--workload-cwd={outdir}",
        "--disable-difftest",
        f"--slc-replacement-policy={run.policy}",
        f"--slc-replacement-seed={run.seed}",
    ]
    started = utc_now()
    with (outdir / "simout").open("w", encoding="utf-8") as stdout, (
        outdir / "simerr"
    ).open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(
            command,
            cwd=config.parents[2],
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
    status: dict[str, object] = {
        **asdict(run),
        "input_mode": "public_static_elf_se",
        "workload_options": workload_options.get(run.workload, ""),
        "command": command,
        "started_at": started,
        "finished_at": utc_now(),
        "returncode": completed.returncode,
        "skipped": False,
    }
    write_json(status_path, status)
    return status


def command_run(args: argparse.Namespace) -> int:
    gem5 = args.gem5.resolve()
    config = args.config.resolve()
    restorer = args.restorer.resolve()
    for label, path in (("gem5", gem5), ("config", config), ("restorer", restorer)):
        if not path.is_file():
            raise FileNotFoundError(f"{label} file does not exist: {path}")
    if not os.access(gem5, os.X_OK):
        raise PermissionError(f"gem5 binary is not executable: {gem5}")

    policies = [canonical_policy(policy) for policy in args.policies]
    if len(policies) != len(set(policies)):
        raise ValueError("--policies contains duplicate policies after lsu→lru normalization")

    output = args.output.resolve()
    slices = discover_slices(args.checkpoint_root.resolve(), args.workloads)
    validate_slice_coverage(slices, args.workloads)
    runs = build_runs(slices, output, policies, args.seed)
    manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "gem5": str(gem5),
        "config": str(config),
        "restorer": str(restorer),
        "checkpoint_root": str(args.checkpoint_root.resolve()),
        "workloads": list(args.workloads),
        "policies": policies,
        "requested_policies": list(args.policies),
        "seed": args.seed,
        "min_measurement_insts": args.min_measurement_insts,
        "extra_args": list(args.gem5_arg),
        "runs": [asdict(run) for run in runs],
    }
    preserve_manifest(output / "manifest.json", manifest, args.force)

    failures: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {
            executor.submit(
                run_command,
                run,
                gem5,
                config,
                restorer,
                args.gem5_arg,
                args.force,
            ): run
            for run in runs
        }
        for future in as_completed(futures):
            run = futures[future]
            try:
                status = future.result()
            except Exception as error:  # Keep all independent slices running.
                status = {**asdict(run), "returncode": -1, "error": repr(error)}
            state = "SKIP" if status.get("skipped") else "PASS"
            if status.get("returncode") != 0:
                state = "FAIL"
                failures.append(status)
            print(
                f"[{state}] {run.policy}/{run.workload}/{run.slice_id}",
                flush=True,
            )

    write_json(
        output / "run_summary.json",
        {
            "finished_at": utc_now(),
            "total": len(runs),
            "passed": len(runs) - len(failures),
            "failed": failures,
        },
    )
    return 1 if failures else 0


def command_run_se(args: argparse.Namespace) -> int:
    gem5 = args.gem5.resolve()
    config = args.config.resolve()
    binaries = {
        "libquantum": args.libquantum_bin.resolve(),
        "omnetpp": args.omnetpp_bin.resolve(),
    }
    omnetpp_ini = args.omnetpp_ini.resolve()
    for label, path in (
        ("gem5", gem5),
        ("config", config),
        ("omnetpp ini", omnetpp_ini),
        *binaries.items(),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} file does not exist: {path}")
    if not os.access(gem5, os.X_OK):
        raise PermissionError(f"gem5 binary is not executable: {gem5}")
    for workload, binary in binaries.items():
        if not os.access(binary, os.X_OK):
            raise PermissionError(f"{workload} binary is not executable: {binary}")

    protected = (
        "--cmd",
        "--options",
        "--workload-cwd",
        "--slc-replacement-policy",
        "--slc-replacement-seed",
        "--disable-difftest",
    )
    for value in args.gem5_arg:
        if value == "--" or any(
            value == option or value.startswith(option + "=")
            for option in protected
        ):
            raise ValueError(
                f"--gem5-arg may not override runner-owned option {value!r}"
            )

    policies = [canonical_policy(policy) for policy in args.policies]
    if len(policies) != len(set(policies)):
        raise ValueError(
            "--policies contains duplicate policies after lsu→lru normalization"
        )

    output = args.output.resolve()
    repo_root = config.parents[2]
    tool_files = {
        "gem5": gem5,
        "config": config,
        "runner": Path(__file__).resolve(),
        "cache_config": repo_root / "configs/common/CacheConfig.py",
        "fs_config": repo_root / "configs/common/FSConfig.py",
        "mem_config": repo_root / "configs/common/MemConfig.py",
        "options": repo_root / "configs/common/Options.py",
        "simulation": repo_root / "configs/common/Simulation.py",
        "xiangshan": repo_root / "configs/common/xiangshan.py",
        "topology": repo_root / "configs/example/noc_config/chi_6x4_hnf.py",
    }
    for label, path in tool_files.items():
        if not path.is_file():
            raise FileNotFoundError(
                f"runtime tool dependency {label} does not exist: {path}"
            )
    slices = [
        Slice(
            workload=workload,
            slice_id="public_source_proxy",
            checkpoint=str(binary),
            weight=1.0,
        )
        for workload, binary in binaries.items()
    ]
    workload_options = {
        "libquantum": args.libquantum_options,
        "omnetpp": " ".join(
            part
            for part in (
                "-f",
                shlex.quote(str(omnetpp_ini)),
                args.omnetpp_options,
            )
            if part
        ),
    }
    runs = build_runs(slices, output, policies, args.seed)
    manifest = {
        "schema_version": 2,
        "created_at": utc_now(),
        "input_mode": "public_static_elf_se",
        "source_label": (
            "Public open-source SPEC-like proxy workloads; not SPEC CPU2006 "
            "binaries, runs, or reportable scores"
        ),
        "gem5": str(gem5),
        "config": str(config),
        "binaries": {name: str(path) for name, path in binaries.items()},
        "binary_sha256": {
            name: file_sha256(path) for name, path in binaries.items()
        },
        "input_files": {"omnetpp_ini": str(omnetpp_ini)},
        "input_sha256": {"omnetpp_ini": file_sha256(omnetpp_ini)},
        "tool_files": {
            name: str(path) for name, path in tool_files.items()
        },
        "tool_sha256": {
            name: file_sha256(path) for name, path in tool_files.items()
        },
        "workload_provenance": {
            "libquantum": args.libquantum_source_label,
            "omnetpp": args.omnetpp_source_label,
        },
        "workload_options": workload_options,
        "workloads": list(DEFAULT_WORKLOADS),
        "policies": policies,
        "requested_policies": list(args.policies),
        "seed": args.seed,
        "min_measurement_insts": args.min_measurement_insts,
        "extra_args": list(args.gem5_arg),
        "runs": [asdict(run) for run in runs],
    }
    preserve_manifest(output / "manifest.json", manifest, args.force)

    failures: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {
            executor.submit(
                run_se_command,
                run,
                gem5,
                config,
                workload_options,
                args.gem5_arg,
                args.force,
            ): run
            for run in runs
        }
        for future in as_completed(futures):
            run = futures[future]
            try:
                status = future.result()
            except Exception as error:
                status = {**asdict(run), "returncode": -1, "error": repr(error)}
            state = "SKIP" if status.get("skipped") else "PASS"
            if status.get("returncode") != 0:
                state = "FAIL"
                failures.append(status)
            print(
                f"[{state}] {run.policy}/{run.workload}/{run.slice_id}",
                flush=True,
            )

    write_json(
        output / "run_summary.json",
        {
            "finished_at": utc_now(),
            "total": len(runs),
            "passed": len(runs) - len(failures),
            "failed": failures,
        },
    )
    return 1 if failures else 0


def parse_stats(path: Path) -> dict[str, float]:
    if not path.is_file():
        raise FileNotFoundError(f"gem5 statistics file is missing: {path}")
    sections: list[dict[str, float]] = []
    current: dict[str, float] = {}
    saw_marker = False
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "Begin Simulation Statistics" in raw_line:
            if current:
                sections.append(current)
            current = {}
            saw_marker = True
            continue
        if "End Simulation Statistics" in raw_line:
            if current:
                sections.append(current)
                current = {}
            continue
        match = STAT_LINE.match(raw_line)
        if not match:
            continue
        value = float(match.group("value"))
        if math.isfinite(value):
            current[match.group("name")] = value
    if current:
        sections.append(current)
    if not sections:
        marker = "marked " if saw_marker else ""
        raise ValueError(f"no numeric statistics found in {marker}file {path}")
    # Warm-up/detailed runs can emit multiple dumps.  The final section is the
    # measurement interval produced by the XiangShan checkpoint flow.
    return sections[-1]


def first_stat(stats: Mapping[str, float], names: Iterable[str]) -> float:
    for name in names:
        if name in stats:
            return stats[name]
    return math.nan


def sum_matching(stats: Mapping[str, float], pattern: re.Pattern[str]) -> float:
    return sum(value for name, value in stats.items() if pattern.match(name))


def weighted_mean(pairs: Sequence[tuple[float, float]]) -> float:
    finite = [(value, weight) for value, weight in pairs if math.isfinite(value)]
    denominator = sum(weight for _, weight in finite)
    return (
        sum(value * weight for value, weight in finite) / denominator
        if denominator
        else math.nan
    )


def hnf_values(stats: Mapping[str, float]) -> dict[int, dict[str, float]]:
    result: dict[int, dict[str, float]] = {}
    for name, value in stats.items():
        match = HNF_STAT.match(name)
        if match:
            index = int(match.group("index") or 0)
            result.setdefault(index, {})[match.group("metric")] = value
    return result


def router_values(stats: Mapping[str, float]) -> dict[int, dict[str, float]]:
    result: dict[int, dict[str, float]] = {}
    for name, value in stats.items():
        match = ROUTER_STAT.match(name)
        if match:
            result.setdefault(int(match.group("index")), {})[
                match.group("metric")
            ] = value
    return result


def histogram_mean(metrics: Mapping[str, float], prefix: str) -> float:
    """Return a mean for either a Distribution or exact SparseHistogram."""

    direct = metrics.get(f"{prefix}::mean", math.nan)
    if math.isfinite(direct):
        return direct
    weighted_total = 0.0
    samples = 0.0
    bucket_prefix = prefix + "::"
    for name, count in metrics.items():
        if not name.startswith(bucket_prefix):
            continue
        bucket = name[len(bucket_prefix):]
        try:
            value = float(bucket)
        except ValueError:
            continue
        weighted_total += value * count
        samples += count
    return weighted_total / samples if samples else math.nan


def summarize_run(
    run: Mapping[str, object],
) -> tuple[
    dict[str, object],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    outdir = Path(str(run["outdir"]))
    stats = parse_stats(outdir / "stats.txt")
    committed = first_stat(
        stats,
        ("system.cpu0.committedInsts", "system.cpu.committedInsts"),
    )
    cycles = first_stat(stats, ("system.cpu0.numCycles", "system.cpu.numCycles"))
    ipc = first_stat(stats, ("system.cpu0.ipc", "system.cpu.ipc"))
    if not math.isfinite(ipc) and committed > 0 and cycles > 0:
        ipc = committed / cycles
    if not math.isfinite(ipc) or ipc <= 0:
        raise ValueError(f"no positive CPU IPC in {outdir / 'stats.txt'}")

    hnfs = hnf_values(stats)
    routers = router_values(stats)
    if len(hnfs) != 16:
        raise ValueError(f"expected statistics from 16 HN-Fs, found {len(hnfs)} in {outdir}")
    if len(routers) != 24:
        raise ValueError(f"expected statistics from 24 routers, found {len(routers)} in {outdir}")

    lookup_counts = [hnfs[index].get("lookupOperations", 0.0) for index in range(16)]
    lookup_mean = sum(lookup_counts) / len(lookup_counts)
    lookup_cv = (
        math.sqrt(sum((value - lookup_mean) ** 2 for value in lookup_counts) / 16)
        / lookup_mean
        if lookup_mean
        else 0.0
    )
    latency_pairs = [
        (
            histogram_mean(metrics, "acceptedToVisibleLatency"),
            metrics.get("acceptedToVisibleLatency::samples", 0.0),
        )
        for metrics in hnfs.values()
    ]
    row: dict[str, object] = {
        "policy": run["policy"],
        "workload": run["workload"],
        "slice_id": run["slice_id"],
        "weight": float(run["weight"]),
        "checkpoint": run["checkpoint"],
        "ipc": ipc,
        "cpi": 1.0 / ipc,
        "committed_insts": committed,
        "cycles": cycles,
        "sim_ticks": stats.get("simTicks", math.nan),
        "host_seconds": stats.get("hostSeconds", math.nan),
        "hnf_lookups": sum(lookup_counts),
        "hnf_lookup_cv": lookup_cv,
        "slc_hits": sum(
            value.get("slcHitLookups", 0.0)
            + value.get("slcSfHitLookups", 0.0)
            for value in hnfs.values()
        ),
        "sf_hits": sum(
            value.get("sfHitLookups", 0.0)
            + value.get("slcSfHitLookups", 0.0)
            for value in hnfs.values()
        ),
        "miss_miss": sum(value.get("missMissLookups", 0.0) for value in hnfs.values()),
        "clean_slc_victims": sum(value.get("cleanSlcVictims", 0.0) for value in hnfs.values()),
        "dirty_slc_victims": sum(value.get("dirtySlcVictims", 0.0) for value in hnfs.values()),
        "service_stalls": sum(value.get("serviceStalls", 0.0) for value in hnfs.values()),
        "accepted_to_visible_latency": weighted_mean(latency_pairs),
        "router_flits": sum(value.get("internalSentFlits::total", 0.0) for value in routers.values()),
        "router_stall_cycles": sum(value.get("internalOutputStallCycles::total", 0.0) for value in routers.values()),
    }

    hnf_rows = [
        {
            "policy": run["policy"],
            "workload": run["workload"],
            "slice_id": run["slice_id"],
            "weight": float(run["weight"]),
            "hnf": index,
            "lookup_operations": lookup_counts[index],
        }
        for index in range(16)
    ]
    router_rows: list[dict[str, object]] = []
    for index, values in routers.items():
        base = {
            "policy": run["policy"],
            "workload": run["workload"],
            "slice_id": run["slice_id"],
            "weight": float(run["weight"]),
            "router": index,
            "x": index % 6,
            "y": index // 6,
            "sent_flits": values.get("internalSentFlits::total", 0.0),
            "received_flits": values.get("internalReceivedFlits::total", 0.0),
            "output_stall_cycles": values.get("internalOutputStallCycles::total", 0.0),
            "local_injected_flits": values.get("localInjectedFlits::total", 0.0),
            "local_delivered_flits": values.get("localDeliveredFlits::total", 0.0),
        }
        for channel in ("REQ", "RSP", "SNP", "DAT"):
            for direction in ("E", "S", "W", "N"):
                base[f"sent_{channel}_{direction}"] = values.get(
                    f"internalSentFlits_{channel}::{direction}", 0.0
                )
                base[f"stall_{channel}_{direction}"] = values.get(
                    f"internalOutputStallCycles_{channel}::{direction}", 0.0
                )
        router_rows.append(base)
    return row, hnf_rows, router_rows


def require_successful_run(run: Mapping[str, object]) -> None:
    """Reject partial or failed run directories before consuming statistics."""

    outdir = Path(str(run["outdir"]))
    status_path = outdir / "run.json"
    if not status_path.is_file():
        raise FileNotFoundError(f"run status is missing: {status_path}")
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"run status is invalid: {status_path}") from error
    if status.get("returncode") != 0:
        raise ValueError(
            f"run did not complete successfully ({status.get('returncode')}): "
            f"{outdir}"
        )


def require_measurement_length(
    row: Mapping[str, object], minimum_instructions: int
) -> None:
    committed = float(row["committed_insts"])
    if not math.isfinite(committed) or committed < minimum_instructions:
        raise ValueError(
            f"measurement interval is too short for "
            f"{row['policy']}/{row['workload']}/{row['slice_id']}: "
            f"{committed:.0f} < {minimum_instructions} committed instructions"
        )


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"refusing to write empty CSV {path}")
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def normalize_weights(rows: Sequence[Mapping[str, object]]) -> dict[str, float]:
    total = sum(float(row["weight"]) for row in rows)
    if total <= 0:
        raise ValueError("SimPoint weights have a non-positive sum")
    return {str(row["slice_id"]): float(row["weight"]) / total for row in rows}


def aggregate_slices(slice_rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    keys = sorted({(str(row["policy"]), str(row["workload"])) for row in slice_rows})
    for policy, workload in keys:
        selected = [row for row in slice_rows if row["policy"] == policy and row["workload"] == workload]
        weights = normalize_weights(selected)
        cpi = sum(weights[str(row["slice_id"])] * float(row["cpi"]) for row in selected)
        summary: dict[str, object] = {
            "policy": policy,
            "workload": workload,
            "slice_count": len(selected),
            "weight_sum": sum(float(row["weight"]) for row in selected),
            "weighted_cpi": cpi,
            "weighted_ipc": 1.0 / cpi,
            "arithmetic_weighted_ipc": sum(weights[str(row["slice_id"])] * float(row["ipc"]) for row in selected),
        }
        for metric in (
            "committed_insts",
            "hnf_lookup_cv",
            "slc_hits",
            "sf_hits",
            "miss_miss",
            "clean_slc_victims",
            "dirty_slc_victims",
            "service_stalls",
            "accepted_to_visible_latency",
            "router_flits",
            "router_stall_cycles",
            "host_seconds",
        ):
            summary[f"weighted_{metric}"] = sum(
                weights[str(row["slice_id"])] * float(row[metric])
                for row in selected
            )
        result.append(summary)
    return result


def compare_policies(workload_rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    by_workload: dict[str, dict[str, dict[str, object]]] = {}
    for row in workload_rows:
        by_workload.setdefault(str(row["workload"]), {})[str(row["policy"])] = row
    comparisons: list[dict[str, object]] = []
    ratios: list[float] = []
    for workload, policies in sorted(by_workload.items()):
        if "pseudo_random" not in policies or "lru" not in policies:
            raise ValueError(f"{workload} is missing pseudo_random or lru results")
        random_ipc = float(policies["pseudo_random"]["weighted_ipc"])
        lru_ipc = float(policies["lru"]["weighted_ipc"])
        random_victims = float(
            policies["pseudo_random"]["weighted_clean_slc_victims"]
        ) + float(policies["pseudo_random"]["weighted_dirty_slc_victims"])
        lru_victims = float(
            policies["lru"]["weighted_clean_slc_victims"]
        ) + float(policies["lru"]["weighted_dirty_slc_victims"])
        random_insts = float(
            policies["pseudo_random"]["weighted_committed_insts"]
        )
        lru_insts = float(policies["lru"]["weighted_committed_insts"])
        ratio = lru_ipc / random_ipc
        ratios.append(ratio)
        comparisons.append(
            {
                "workload": workload,
                "pseudo_random_ipc": random_ipc,
                "lru_ipc": lru_ipc,
                "lru_vs_pseudo_random_percent": (ratio - 1.0) * 100.0,
                "pseudo_random_slc_victims": random_victims,
                "lru_slc_victims": lru_victims,
                "replacement_activated": random_victims > 0 and lru_victims > 0,
                "measurement_insts_relative_delta": (
                    abs(lru_insts - random_insts)
                    / max(lru_insts, random_insts)
                    if max(lru_insts, random_insts) > 0
                    else math.nan
                ),
            }
        )
    if ratios:
        geomean = math.exp(sum(math.log(value) for value in ratios) / len(ratios))
        comparisons.append(
            {
                "workload": "geomean",
                "pseudo_random_ipc": None,
                "lru_ipc": None,
                "lru_vs_pseudo_random_percent": (geomean - 1.0) * 100.0,
            }
        )
    return comparisons


def aggregate_detail(
    rows: Sequence[dict[str, object]], dimension: str, metrics: Sequence[str]
) -> list[dict[str, object]]:
    keys = sorted(
        {
            (str(row["policy"]), str(row["workload"]), int(row[dimension]))
            for row in rows
        }
    )
    result: list[dict[str, object]] = []
    for policy, workload, value in keys:
        selected = [
            row
            for row in rows
            if row["policy"] == policy
            and row["workload"] == workload
            and int(row[dimension]) == value
        ]
        weights = normalize_weights(selected)
        aggregated: dict[str, object] = {
            "policy": policy,
            "workload": workload,
            dimension: value,
        }
        if dimension == "router":
            aggregated.update({"x": value % 6, "y": value // 6})
        for metric in metrics:
            aggregated[metric] = sum(
                weights[str(row["slice_id"])] * float(row[metric])
                for row in selected
            )
        result.append(aggregated)
    return result


def command_analyze(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    manifest_path = output / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"experiment manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verify_manifest_inputs(manifest)
    runs = manifest.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("experiment manifest has no runs")

    slice_rows: list[dict[str, object]] = []
    hnf_rows: list[dict[str, object]] = []
    router_rows: list[dict[str, object]] = []
    minimum_instructions = int(manifest.get("min_measurement_insts", 0))
    for run in runs:
        require_successful_run(run)
        row, per_hnf, per_router = summarize_run(run)
        require_measurement_length(row, minimum_instructions)
        slice_rows.append(row)
        hnf_rows.extend(per_hnf)
        router_rows.extend(per_router)

    analysis = output / "analysis"
    workload_rows = aggregate_slices(slice_rows)
    comparisons = compare_policies(workload_rows)
    inactive_workloads = [
        str(row["workload"])
        for row in comparisons
        if row["workload"] != "geomean"
        and not bool(row.get("replacement_activated"))
    ]
    hnf_summary = aggregate_detail(hnf_rows, "hnf", ("lookup_operations",))
    router_metrics = [
        key
        for key in router_rows[0]
        if key
        not in {"policy", "workload", "slice_id", "weight", "router", "x", "y"}
    ]
    router_summary = aggregate_detail(router_rows, "router", router_metrics)

    write_csv(analysis / "slices.csv", slice_rows)
    write_csv(analysis / "workload_summary.csv", workload_rows)
    write_csv(analysis / "policy_comparison.csv", comparisons)
    write_csv(analysis / "hnf_balance.csv", hnf_summary)
    write_csv(analysis / "router_hotspots.csv", router_summary)
    experiment_keys = (
        "gem5",
        "config",
        "checkpoint_root",
        "binaries",
        "binary_sha256",
        "input_files",
        "input_sha256",
        "tool_sha256",
        "tool_files",
        "workload_provenance",
        "workload_options",
        "extra_args",
        "input_mode",
        "source_label",
        "workloads",
        "policies",
        "seed",
        "min_measurement_insts",
    )
    summary = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "experiment": {
            key: manifest[key] for key in experiment_keys if key in manifest
        },
        "workloads": workload_rows,
        "comparison": comparisons,
        "validity": {
            "replacement_activated_for_all_workloads": not inactive_workloads,
            "inactive_workloads": inactive_workloads,
            "note": (
                "Replacement-policy attribution requires SLC victims in both "
                "policy runs; workloads listed as inactive are descriptive only."
            ),
        },
        "artifacts": {
            "slices": str((analysis / "slices.csv").resolve()),
            "hnf_balance": str((analysis / "hnf_balance.csv").resolve()),
            "router_hotspots": str((analysis / "router_hotspots.csv").resolve()),
        },
    }
    write_json(analysis / "summary.json", summary)
    print(f"analysis written to {analysis}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="discover and run all benchmark slices")
    run.add_argument("--checkpoint-root", type=Path, required=True)
    run.add_argument("--restorer", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument(
        "--gem5", type=Path, default=Path("build/RISCV/gem5.opt")
    )
    run.add_argument(
        "--config",
        type=Path,
        default=Path("configs/example/kmhv2_chi_6x4_hnf.py"),
    )
    run.add_argument("--workloads", nargs="+", default=list(DEFAULT_WORKLOADS))
    run.add_argument(
        "--policies",
        nargs="+",
        choices=SUPPORTED_POLICIES,
        default=list(DEFAULT_POLICIES),
    )
    run.add_argument("--seed", type=int, default=1)
    run.add_argument(
        "--min-measurement-insts",
        type=int,
        default=19_000_000,
        help=(
            "minimum committed instructions required after the default 20M "
            "warm-up/stat reset (default: 19000000)"
        ),
    )
    run.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="concurrent gem5 processes (default: 1; each 6x4 run is memory-heavy)",
    )
    run.add_argument(
        "--gem5-arg",
        action="append",
        default=[],
        help="extra argument passed after the config path; repeat as needed",
    )
    run.add_argument("--force", action="store_true")
    run.set_defaults(function=command_run)

    run_se = subparsers.add_parser(
        "run-se",
        help="run public static RISC-V ELF proxy workloads (non-SPEC)",
    )
    run_se.add_argument("--libquantum-bin", type=Path, required=True)
    run_se.add_argument("--omnetpp-bin", type=Path, required=True)
    run_se.add_argument("--omnetpp-ini", type=Path, required=True)
    run_se.add_argument("--libquantum-options", default="1397 8")
    run_se.add_argument("--omnetpp-options", default="")
    run_se.add_argument(
        "--libquantum-source-label",
        default="libquantum 0.2.4 upstream Shor proxy (fixed RNG seed)",
    )
    run_se.add_argument(
        "--omnetpp-source-label",
        default="OMNeT++ 3.3.2 public Token Ring sample proxy",
    )
    run_se.add_argument("--output", type=Path, required=True)
    run_se.add_argument(
        "--gem5", type=Path, default=Path("build/RISCV/gem5.opt")
    )
    run_se.add_argument(
        "--config",
        type=Path,
        default=Path("configs/example/kmhv2_chi_6x4_hnf_se.py"),
    )
    run_se.add_argument(
        "--policies",
        nargs="+",
        choices=SUPPORTED_POLICIES,
        default=list(DEFAULT_POLICIES),
    )
    run_se.add_argument("--seed", type=int, default=1)
    run_se.add_argument(
        "--min-measurement-insts",
        type=int,
        default=4_500_000,
        help="minimum committed instructions after the warm-up reset",
    )
    run_se.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="concurrent gem5 processes (default: 1)",
    )
    run_se.add_argument(
        "--gem5-arg",
        action="append",
        default=[
            "--mem-type=DDR4_2400_8x8",
            "--mem-channels=2",
            "--mem-size=2GB",
            "--maxinsts=5100000",
            "--warmup-insts-no-switch=100000",
        ],
        help="extra argument passed after the SE config path; repeat as needed",
    )
    run_se.add_argument("--force", action="store_true")
    run_se.set_defaults(function=command_run_se)

    analyze = subparsers.add_parser("analyze", help="aggregate completed runs")
    analyze.add_argument("--output", type=Path, required=True)
    analyze.set_defaults(function=command_analyze)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if getattr(args, "jobs", 1) < 1:
        raise ValueError("--jobs must be positive")
    if getattr(args, "seed", 0) < 0 or getattr(args, "seed", 0) > 2**64 - 1:
        raise ValueError("--seed must fit UInt64")
    if getattr(args, "min_measurement_insts", 0) < 0:
        raise ValueError("--min-measurement-insts must be non-negative")
    return args.function(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FileNotFoundError, PermissionError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)
