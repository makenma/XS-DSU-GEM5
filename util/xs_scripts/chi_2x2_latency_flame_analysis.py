#!/usr/bin/env python3
"""Extended RNF-latency and guest-flame analysis for the 2x2 CHI run.

This script consumes only completed ROI artifacts.  It never invokes gem5 and
does not alter simulated state.  Run it with the small analysis virtual
environment documented in the generated README.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import configparser
import csv
from dataclasses import dataclass, field
import hashlib
import html
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "util/xs_scripts/chi_2x2_dual_proxy_experiment.py"
POLICIES = ("lru", "random", "srrip")
POLICY_LABEL = {"lru": "LRU", "random": "Random", "srrip": "SRRIP"}
POLICY_COLOR = {"lru": "#2563eb", "random": "#f59e0b", "srrip": "#16a34a"}
WORKLOADS = {
    0: {
        "slug": "libquantum",
        "name": "libquantum 0.2.4 Shor",
        "elf": Path(
            "/home/makenma/project/xs-gem5/workloads/"
            "libquantum-0.2.4/shor-rv64-static"),
    },
    1: {
        "slug": "omnetpp",
        "name": "OMNeT++ 3.3.2 Token Ring",
        "elf": Path(
            "/home/makenma/project/xs-gem5/workloads/omnetpp-3.3.2/"
            "rv64/bin/tokenring-rv64-static"),
    },
}
ADDR2LINE = shutil.which("riscv64-linux-gnu-addr2line")
READELF = shutil.which("riscv64-linux-gnu-readelf")
MODEL_COMPARISON_SOURCE = (
    ROOT / "results/chi-2x2-dual-public-proxy-direct-victim-v1/analysis")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def write_csv(path: Path, fields: list[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def load_runner_module():
    spec = importlib.util.spec_from_file_location("chi_dual_runner", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import runner: {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def nearest_rank(values: list[int], percentile: float) -> int:
    """Nearest-rank percentile, with rank ceil(p*N), for integer ticks."""
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def parse_sim_frequency(stats_path: Path) -> int:
    for line in stats_path.read_text(errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "simFreq":
            return int(float(parts[1]))
    raise RuntimeError(f"simFreq missing from {stats_path}")


def metric_units(ticks: float, clock_period: int, sim_frequency: int) -> dict:
    return {
        "ticks": ticks,
        "chi_cycles": ticks / clock_period,
        "ns": ticks / sim_frequency * 1.0e9,
    }


def latency_analysis(result: Path) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    payload = {
        "schema_version": 1,
        "definition": {
            "request_start": (
                "tick at which the RNF successfully injects the REQ into "
                "its CHI receive queue"),
            "snoop_start": (
                "tick at which the RNF accepts the TXSNP for handling"),
            "end": (
                "tick of the exactly matched terminal RSP/DAT completion "
                "and classic transaction retire, or final SNP response "
                "injection"),
            "identity": (
                "exact transaction_channel + source_id + transaction_id; "
                "the bridge transaction table performs response matching"),
            "completed_only": (
                "mean/P50/P95 and weighted averages exclude right-censored "
                "transactions outstanding at the exact ROI stop tick"),
            "percentile": "nearest-rank: sorted[ceil(p*N)-1]",
            "weighted_average": (
                "sum(count_type * mean_latency_type) / sum(count_type), "
                "where count_type is the completed count"),
            "cycle_conversion": "latency_ticks / chi_clock_period_ticks",
            "ns_conversion": "latency_ticks / simFreq * 1e9",
        },
        "policies": {},
    }

    for policy in POLICIES:
        policy_payload = {}
        sim_frequency = parse_sim_frequency(result / "runs" / policy / "stats.txt")
        for rnf in (0, 1):
            path = result / "runs" / policy / f"rnf{rnf}_transaction_latency_raw.csv"
            raw = read_csv(path)
            if not raw:
                raise RuntimeError(f"empty RNF latency trace: {path}")
            seen = set()
            groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
            accepted_groups: Counter[tuple[str, str]] = Counter()
            censored_groups: Counter[tuple[str, str]] = Counter()
            completed_latencies: list[int] = []
            accepted = completed = censored = 0
            trace_bounds = set()
            periods = set()
            retries = 0
            terminal_counts: Counter[str] = Counter()
            for item in raw:
                channel = item["transaction_channel"]
                source_id = int(item["source_id"])
                txn_id = int(item["transaction_id"])
                start = int(item["start_tick"])
                stop = int(item["trace_stop_tick"])
                key = (channel, source_id, txn_id)
                type_group = (channel, item["transaction_type"])
                if key in seen:
                    raise AssertionError(
                        f"duplicate exact RNF identity in {path}: {key}")
                seen.add(key)
                accepted += 1
                accepted_groups[type_group] += 1
                trace_bounds.add((int(item["trace_start_tick"]), stop))
                periods.add(int(item["chi_clock_period_ticks"]))
                retries += int(item["retry_count"])
                if not (int(item["trace_start_tick"]) <= start <= stop):
                    raise AssertionError(f"start outside ROI trace: {item}")
                if item["status"] == "completed":
                    end = int(item["end_tick"])
                    latency = int(item["latency_ticks"])
                    if end - start != latency or not (start <= end <= stop):
                        raise AssertionError(f"invalid completed latency: {item}")
                    completed += 1
                    completed_latencies.append(latency)
                    groups[(channel, item["transaction_type"])].append(item)
                    terminal_counts[
                        f"{item['terminal_channel']}:{item['terminal_type']}"] += 1
                elif item["status"] == "censored_roi_end":
                    censored += 1
                    censored_groups[type_group] += 1
                    if (int(item["end_tick"]) != stop or
                            item["latency_ticks"]):
                        raise AssertionError(
                            f"censored row has invalid ROI stop marker: {item}")
                else:
                    raise AssertionError(f"unknown trace status: {item['status']}")
            if len(trace_bounds) != 1 or len(periods) != 1:
                raise AssertionError(f"inconsistent trace metadata: {path}")
            clock_period = next(iter(periods))
            type_payload = {}
            for channel, txn_type in sorted(groups):
                instances = groups[(channel, txn_type)]
                latencies = [int(item["latency_ticks"]) for item in instances]
                mean = statistics.fmean(latencies)
                p50 = nearest_rank(latencies, 0.50)
                p95 = nearest_rank(latencies, 0.95)
                minimum = min(latencies)
                maximum = max(latencies)
                type_key = f"{channel}:{txn_type}"
                type_terminals = Counter(
                    f"{item['terminal_channel']}:{item['terminal_type']}"
                    for item in instances)
                terminal_names = ";".join(sorted(type_terminals))
                row = {
                    "policy": policy,
                    "policy_label": POLICY_LABEL[policy],
                    "rnf": rnf,
                    "cpu": rnf,
                    "workload": WORKLOADS[rnf]["name"],
                    "transaction_channel": channel,
                    "transaction_type": txn_type,
                    "transaction_type_key": type_key,
                    "terminal_response_types": terminal_names,
                    "terminal_response_counts_json": json.dumps(
                        dict(sorted(type_terminals.items())),
                        separators=(",", ":")),
                    "accepted_count": accepted_groups[(channel, txn_type)],
                    "completed_count": len(latencies),
                    "right_censored_count": censored_groups[
                        (channel, txn_type)],
                    "completion_fraction": (
                        len(latencies) / accepted_groups[(channel, txn_type)]),
                    "mean_latency_ticks": mean,
                    "p50_latency_ticks": p50,
                    "p95_latency_ticks": p95,
                    "min_latency_ticks": minimum,
                    "max_latency_ticks": maximum,
                    "chi_clock_period_ticks": clock_period,
                    "mean_latency_chi_cycles": mean / clock_period,
                    "p50_latency_chi_cycles": p50 / clock_period,
                    "p95_latency_chi_cycles": p95 / clock_period,
                    "min_latency_chi_cycles": minimum / clock_period,
                    "max_latency_chi_cycles": maximum / clock_period,
                    "sim_frequency_ticks_per_second": sim_frequency,
                    "mean_latency_ns": mean / sim_frequency * 1.0e9,
                    "p50_latency_ns": p50 / sim_frequency * 1.0e9,
                    "p95_latency_ns": p95 / sim_frequency * 1.0e9,
                    "min_latency_ns": minimum / sim_frequency * 1.0e9,
                    "max_latency_ns": maximum / sim_frequency * 1.0e9,
                }
                rows.append(row)
                type_payload[type_key] = row.copy()
                type_payload[type_key]["terminal_response_counts"] = dict(
                    sorted(type_terminals.items()))
            weighted_ticks = (
                sum(len(v) * statistics.fmean(
                    int(item["latency_ticks"]) for item in v)
                    for v in groups.values()) / completed)
            # This identity is intentionally checked because it catches count
            # or type-grouping mistakes in the weighted-average calculation.
            direct_mean = statistics.fmean(completed_latencies)
            if not math.isclose(weighted_ticks, direct_mean, rel_tol=1e-15):
                raise AssertionError("weighted latency identity failed")
            overall = {
                "accepted_count": accepted,
                "completed_count": completed,
                "right_censored_count": censored,
                "completion_fraction": completed / accepted,
                "weighted_mean_latency_ticks": weighted_ticks,
                "weighted_mean_latency_chi_cycles": weighted_ticks / clock_period,
                "weighted_mean_latency_ns": (
                    weighted_ticks / sim_frequency * 1.0e9),
                "p50_latency_ticks": nearest_rank(completed_latencies, 0.50),
                "p95_latency_ticks": nearest_rank(completed_latencies, 0.95),
                "p50_latency_chi_cycles": (
                    nearest_rank(completed_latencies, 0.50) / clock_period),
                "p95_latency_chi_cycles": (
                    nearest_rank(completed_latencies, 0.95) / clock_period),
                "p50_latency_ns": (
                    nearest_rank(completed_latencies, 0.50) /
                    sim_frequency * 1.0e9),
                "p95_latency_ns": (
                    nearest_rank(completed_latencies, 0.95) /
                    sim_frequency * 1.0e9),
                "chi_clock_period_ticks": clock_period,
                "sim_frequency_ticks_per_second": sim_frequency,
                "total_retry_count": retries,
                "trace_start_tick": next(iter(trace_bounds))[0],
                "trace_stop_tick": next(iter(trace_bounds))[1],
                "terminal_response_counts": dict(sorted(terminal_counts.items())),
                "transaction_types": type_payload,
                "exact_identity_unique": True,
            }
            policy_payload[f"rnf{rnf}"] = overall
        payload["policies"][policy] = policy_payload

    baseline = payload["policies"]["random"]
    for policy in POLICIES:
        for rnf in (0, 1):
            current = payload["policies"][policy][f"rnf{rnf}"]
            base = baseline[f"rnf{rnf}"]
            delta = (current["weighted_mean_latency_chi_cycles"] -
                     base["weighted_mean_latency_chi_cycles"])
            current["weighted_mean_delta_vs_random_chi_cycles"] = delta
            current["weighted_mean_percent_vs_random"] = (
                delta / base["weighted_mean_latency_chi_cycles"] * 100.0
                if base["weighted_mean_latency_chi_cycles"] else 0.0)
    row_lookup = {
        (row["policy"], row["rnf"], row["transaction_type_key"]): row
        for row in rows
    }
    for row in rows:
        base = row_lookup.get(
            ("random", row["rnf"], row["transaction_type_key"]))
        if base is None:
            row["mean_delta_vs_random_chi_cycles"] = None
            row["mean_percent_vs_random"] = None
        else:
            delta = (row["mean_latency_chi_cycles"] -
                     base["mean_latency_chi_cycles"])
            row["mean_delta_vs_random_chi_cycles"] = delta
            row["mean_percent_vs_random"] = (
                delta / base["mean_latency_chi_cycles"] * 100.0
                if base["mean_latency_chi_cycles"] else 0.0)
        type_payload = payload["policies"][row["policy"]][
            f"rnf{row['rnf']}"]["transaction_types"][
                row["transaction_type_key"]]
        type_payload["mean_delta_vs_random_chi_cycles"] = row[
            "mean_delta_vs_random_chi_cycles"]
        type_payload["mean_percent_vs_random"] = row[
            "mean_percent_vs_random"]
    return rows, payload


def latency_plots(result: Path, rows: list[dict], payload: dict) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/chi-latency-matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    analysis = result / "analysis"
    for rnf in (0, 1):
        selected = [row for row in rows if row["rnf"] == rnf]
        types = sorted({row["transaction_type_key"] for row in selected})
        terminal_by_type = {
            kind: sorted({terminal for row in selected
                          if row["transaction_type_key"] == kind
                          for terminal in row["terminal_response_types"].split(";")
                          if terminal})
            for kind in types
        }
        lookup = {
            (row["policy"], row["transaction_type_key"]): row
            for row in selected
        }
        width = 0.24
        x = np.arange(len(types), dtype=float)
        fig, ax = plt.subplots(
            figsize=(max(12.0, 1.65 * len(types)), 7.2),
            constrained_layout=True)
        for offset, policy in enumerate(POLICIES):
            ys = [lookup.get((policy, kind), {}).get(
                "mean_latency_chi_cycles", math.nan) for kind in types]
            p50 = [lookup.get((policy, kind), {}).get(
                "p50_latency_chi_cycles", math.nan) for kind in types]
            p95 = [lookup.get((policy, kind), {}).get(
                "p95_latency_chi_cycles", math.nan) for kind in types]
            counts = [lookup.get((policy, kind), {}).get(
                "completed_count", 0) for kind in types]
            positions = x + (offset - 1) * width
            weighted = payload["policies"][policy][f"rnf{rnf}"][
                "weighted_mean_latency_chi_cycles"]
            bars = ax.bar(
                positions, ys, width, color=POLICY_COLOR[policy], alpha=0.86,
                label=f"{POLICY_LABEL[policy]} (weighted={weighted:.2f} cyc)")
            ax.scatter(positions, p50, marker="D", s=24, color="#111827",
                       zorder=4)
            ax.scatter(positions, p95, marker="_", s=100, linewidths=2,
                       color="#7f1d1d", zorder=4)
            for bar, count in zip(bars, counts):
                if count:
                    ax.annotate(
                        f"n={count}",
                        (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", fontsize=7, rotation=90)
        type_labels = [
            kind + "\n→ " + "/".join(terminal_by_type[kind])
            for kind in types]
        ax.set_xticks(x, type_labels, rotation=28, ha="right")
        ax.set_ylabel("RNF transaction latency (CHI cycles)")
        ax.set_xlabel("Formal CHI channel and transaction type")
        ax.set_title(
            f"RNF{rnf} / CPU{rnf} — {WORKLOADS[rnf]['name']}", pad=13)
        ax.text(
            0.995, 0.985,
            "Bar = mean   ◆ = P50   red dash = P95   n = completed count",
            transform=ax.transAxes, ha="right", va="top", fontsize=9,
            color="#475569",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.78,
                  "pad": 2.0})
        ax.grid(axis="y", linestyle=":", alpha=0.45)
        ax.legend(loc="upper left", fontsize=9)
        fig.savefig(analysis / f"rnf{rnf}_transaction_latency.png", dpi=180)
        fig.savefig(analysis / f"rnf{rnf}_transaction_latency.svg")
        plt.close(fig)


def symbolize(elf: Path, addresses: list[str]) -> dict[str, dict[str, str]]:
    if ADDR2LINE is None:
        raise RuntimeError("riscv64-linux-gnu-addr2line is required")
    ordered = sorted(set(addresses), key=lambda value: int(value, 16))
    completed = subprocess.run(
        [ADDR2LINE, "-f", "-C", "-e", str(elf)],
        input="\n".join(ordered) + "\n", text=True,
        capture_output=True, check=True)
    output = completed.stdout.splitlines()
    if len(output) != 2 * len(ordered):
        raise RuntimeError(
            f"addr2line returned {len(output)} lines for {len(ordered)} PCs")
    mapping = {}
    for index, address in enumerate(ordered):
        function = output[2 * index].strip()
        source = output[2 * index + 1].strip()
        resolved = function not in ("", "??")
        mapping[address] = {
            "address": address,
            "function": function if resolved else f"[unknown@{address}]",
            "source": source,
            "resolved": resolved,
        }
    return mapping


def elf_symbol_metadata(elf: Path) -> dict:
    if READELF is None:
        raise RuntimeError("riscv64-linux-gnu-readelf is required")
    sections = subprocess.run(
        [READELF, "-S", str(elf)], text=True,
        capture_output=True, check=True).stdout
    notes = subprocess.run(
        [READELF, "-n", str(elf)], text=True,
        capture_output=True, check=True).stdout
    build_match = re.search(r"Build ID:\s*([0-9a-fA-F]+)", notes)
    return {
        "build_id": build_match.group(1) if build_match else None,
        "has_symtab": ".symtab" in sections,
        "has_eh_frame": ".eh_frame" in sections,
        "has_debug_info": ".debug_info" in sections,
        "has_debug_line": ".debug_line" in sections,
        "symbol_level": (
            "function names from .symtab; source lines unavailable"
            if ".symtab" in sections and ".debug_info" not in sections else
            "function and debug information when present"),
        "rebuilt_for_profile": False,
        "frame_pointer_or_dwarf_unwind_used": False,
    }


@dataclass
class FlameNode:
    name: str
    count: int = 0
    children: dict[str, "FlameNode"] = field(default_factory=dict)

    def add(self, frames: list[str], count: int) -> None:
        self.count += count
        if not frames:
            return
        child = self.children.setdefault(frames[0], FlameNode(frames[0]))
        child.add(frames[1:], count)


def function_color(name: str, depth: int) -> str:
    value = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
    hue = (value % 36) + 7
    # Warm flame palette with deterministic, readable variation.
    red = 225 + (value % 26)
    green = 78 + ((value >> 6) % 105)
    blue = 35 + ((value >> 13) % 45)
    if name.startswith("[ROI root"):
        return "#475569"
    return f"#{red:02x}{green:02x}{blue:02x}"


def render_flame_svg(
        path: Path, folded: Counter[tuple[str, ...]], title: str,
        subtitle: str) -> None:
    root = FlameNode("[all guest ROI samples]")
    for frames, count in folded.items():
        root.add(list(frames), count)
    max_depth = max((len(frames) for frames in folded), default=1) + 1
    width = 1800
    frame_height = 24
    top = 82
    bottom = 42
    height = top + max_depth * frame_height + bottom
    usable = width - 30
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fffaf3"/>',
        '<style>text{font-family:DejaVu Sans,Arial,sans-serif}'
        '.frame:hover{stroke:#111827;stroke-width:1.5}</style>',
        f'<text x="15" y="30" font-size="22" font-weight="700" '
        f'fill="#111827">{html.escape(title)}</text>',
        f'<text x="15" y="55" font-size="13" fill="#475569">'
        f'{html.escape(subtitle)}</text>',
    ]

    def draw(node: FlameNode, depth: int, x: float, node_width: float) -> None:
        y = top + (max_depth - depth - 1) * frame_height
        color = function_color(node.name, depth)
        label_capacity = max(0, int((node_width - 8) / 7.1))
        label = node.name
        if len(label) > label_capacity:
            label = label[:max(0, label_capacity - 1)] + "…"
        fraction = node.count / root.count if root.count else 0.0
        tooltip = f"{node.name} — {node.count} samples ({fraction:.2%})"
        elements.append(
            f'<g class="frame"><title>{html.escape(tooltip)}</title>'
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(0,node_width-0.7):.2f}" '
            f'height="{frame_height-1}" rx="2" fill="{color}" '
            f'stroke="#fff7ed" stroke-width="0.5"/>')
        if label_capacity >= 3:
            elements.append(
                f'<text x="{x+4:.2f}" y="{y+16:.2f}" font-size="12" '
                f'fill="#111827">{html.escape(label)}</text>')
        elements.append('</g>')
        cursor = x
        for child in sorted(
                node.children.values(), key=lambda item: (-item.count, item.name)):
            child_width = node_width * child.count / node.count
            draw(child, depth + 1, cursor, child_width)
            cursor += child_width

    draw(root, 0, 15.0, usable)
    elements.extend([
        f'<text x="15" y="{height-16}" font-size="12" fill="#64748b">'
        'Width is proportional to sampled retired guest instructions. '
        'This is not host gem5 profiling.</text>',
        '</svg>',
    ])
    path.write_text("\n".join(elements) + "\n", encoding="utf-8")


def build_flame_profiles(result: Path, base_summary: dict) -> tuple[list[dict], dict]:
    output = result / "analysis/flamegraphs"
    output.mkdir(parents=True, exist_ok=True)
    profiles = []
    top_rows = []
    for cpu in (0, 1):
        workload = WORKLOADS[cpu]
        elf = workload["elf"]
        if not elf.is_file():
            raise RuntimeError(f"guest ELF is missing: {elf}")
        elf_metadata = elf_symbol_metadata(elf)
        for policy in POLICIES:
            stem = f"{workload['slug']}-{policy}"
            raw_source = (
                result / "runs" / policy /
                f"cpu{cpu}_guest_stack_samples_raw.csv")
            metadata_source = (
                result / "runs" / policy /
                f"cpu{cpu}_guest_stack_samples_metadata.json")
            raw_target = output / f"{stem}.raw.csv"
            metadata_target = output / f"{stem}.metadata.json"
            shutil.copy2(raw_source, raw_target)
            shutil.copy2(metadata_source, metadata_target)
            raw = read_csv(raw_source)
            metadata = json.loads(metadata_source.read_text())
            addresses = []
            for item in raw:
                addresses.extend(
                    value for value in item["stack_addresses"].split(";")
                    if value)
            mapping = symbolize(elf, addresses)
            symbol_rows = [mapping[key] for key in sorted(
                mapping, key=lambda value: int(value, 16))]
            write_csv(
                output / f"{stem}.symbols.csv",
                ["address", "function", "source", "resolved"], symbol_rows)

            folded: Counter[tuple[str, ...]] = Counter()
            leaf_counts: Counter[str] = Counter()
            inclusive_counts: Counter[str] = Counter()
            unresolved_frame_instances = 0
            frame_instances = 0
            unresolved_leaf_samples = 0
            ticks = []
            for item in raw:
                ticks.append(int(item["tick"]))
                labels = ["[ROI root: pre-ROI ancestry unavailable]"]
                for address in item["stack_addresses"].split(";"):
                    if not address:
                        continue
                    record = mapping[address]
                    frame_instances += 1
                    unresolved_frame_instances += int(not record["resolved"])
                    label = record["function"].replace(";", ":")
                    if not labels or labels[-1] != label:
                        labels.append(label)
                if len(labels) == 1:
                    labels.append("[empty sample]")
                folded[tuple(labels)] += 1
                leaf_counts[labels[-1]] += 1
                for function in dict.fromkeys(labels[1:]):
                    inclusive_counts[function] += 1
                unresolved_leaf_samples += int(labels[-1].startswith("[unknown@"))
            folded_path = output / f"{stem}.folded"
            folded_path.write_text(
                "".join(
                    f"{';'.join(frames)} {count}\n"
                    for frames, count in sorted(folded.items())),
                encoding="utf-8")
            svg_path = output / f"{stem}.svg"
            title = f"{workload['name']} — {POLICY_LABEL[policy]}"
            pc_only = (
                metadata["calls_seen"] == 0 and
                metadata["max_shadow_depth"] <= 1)
            if pc_only:
                rendering_kind = "guest_roi_function_pc_profile_flame_layout"
                subtitle = (
                    "Guest ROI function/PC profile in flame layout; no calls "
                    "were observed, so this is NOT a call-stack flame graph; "
                    f"1 sample / {metadata['sample_period_insts']} retired "
                    "macro-instructions")
            else:
                rendering_kind = "guest_roi_shadow_call_stack_flame_graph"
                subtitle = (
                    "Guest ROI-local shadow-call-stack flame graph; 1 sample / "
                    f"{metadata['sample_period_insts']} retired macro-instructions; "
                    "pre-ROI ancestry is intentionally marked unavailable")
            render_flame_svg(svg_path, folded, title, subtitle)
            try:
                import cairosvg
                cairosvg.svg2png(
                    bytestring=svg_path.read_bytes(),
                    write_to=str(output / f"{stem}.png"), output_width=1800)
            except Exception as error:
                raise RuntimeError(f"cannot rasterize {svg_path}: {error}") from error

            roi_insts = base_summary["policies"][policy]["cores"][cpu][
                "instructions"]
            unique_unresolved = sum(
                1 for record in mapping.values() if not record["resolved"])
            tick_deltas = [b - a for a, b in zip(ticks, ticks[1:])]
            profile = {
                "policy": policy,
                "cpu": cpu,
                "workload": workload["name"],
                "profile_kind": metadata["profile_kind"],
                "rendering_kind": rendering_kind,
                "is_call_stack_flame_graph": not pc_only,
                "pc_profile_only": pc_only,
                "guest_profile": True,
                "host_gem5_profile": False,
                "roi_only": True,
                "raw_samples": str(raw_target.relative_to(result)),
                "raw_metadata": str(metadata_target.relative_to(result)),
                "folded_stacks": str(folded_path.relative_to(result)),
                "flame_svg": str(svg_path.relative_to(result)),
                "flame_png": str((output / f"{stem}.png").relative_to(result)),
                "symbol_map": str(
                    (output / f"{stem}.symbols.csv").relative_to(result)),
                "symbolizer": (
                    "riscv64-linux-gnu-addr2line -f -C -e <CPU-specific ELF>"),
                "elf": str(elf),
                "elf_sha256": sha256(elf),
                "elf_symbol_metadata": elf_metadata,
                "sample_period_retired_macro_insts": metadata[
                    "sample_period_insts"],
                "samples": len(raw),
                "retired_macro_insts_observed": metadata[
                    "retired_macro_insts_observed"],
                "roi_committed_insts": roi_insts,
                "commit_probe_coverage_fraction": (
                    metadata["retired_macro_insts_observed"] / roi_insts
                    if roi_insts else 0.0),
                "median_sample_interval_ticks": (
                    statistics.median(tick_deltas) if tick_deltas else None),
                "unique_addresses": len(mapping),
                "unique_unresolved_addresses": unique_unresolved,
                "unique_unresolved_ratio": (
                    unique_unresolved / len(mapping) if mapping else 0.0),
                "frame_instances": frame_instances,
                "unresolved_frame_instances": unresolved_frame_instances,
                "unresolved_frame_ratio": (
                    unresolved_frame_instances / frame_instances
                    if frame_instances else 0.0),
                "unresolved_leaf_samples": unresolved_leaf_samples,
                "unresolved_leaf_ratio": (
                    unresolved_leaf_samples / len(raw) if raw else 0.0),
                "calls_seen": metadata["calls_seen"],
                "returns_seen": metadata["returns_seen"],
                "return_underflows": metadata["return_underflows"],
                "max_shadow_depth": metadata["max_shadow_depth"],
                "pre_roi_ancestry_unwound": False,
                "roi_root_is_truncated": True,
                "semantic_timing_effect": False,
                "top_leaf_functions": [],
                "top_inclusive_functions": [],
            }
            for rank, (function, count) in enumerate(
                    leaf_counts.most_common(15), 1):
                item = {
                    "rank": rank,
                    "function": function,
                    "samples": count,
                    "sample_fraction": count / len(raw) if raw else 0.0,
                }
                profile["top_leaf_functions"].append(item)
                top_rows.append({
                    "policy": policy,
                    "cpu": cpu,
                    "workload": workload["name"],
                    "measure": "leaf",
                    **item,
                })
            for rank, (function, count) in enumerate(
                    inclusive_counts.most_common(15), 1):
                item = {
                    "rank": rank,
                    "function": function,
                    "samples": count,
                    "sample_fraction": count / len(raw) if raw else 0.0,
                }
                profile["top_inclusive_functions"].append(item)
                top_rows.append({
                    "policy": policy,
                    "cpu": cpu,
                    "workload": workload["name"],
                    "measure": "inclusive",
                    **item,
                })
            profiles.append(profile)
    write_csv(
        output / "top_guest_functions.csv",
        ["policy", "cpu", "workload", "measure", "rank", "function",
         "samples", "sample_fraction"], top_rows)
    payload = {
        "schema_version": 1,
        "method": {
            "kind": "guest_roi_shadow_call_stack",
            "source": "O3 retired guest macro-instruction Commit probe",
            "sampling": "deterministic instruction-count sampling",
            "boundary": "start/stop at exact measured ROI ticks",
            "symbolization": "CPU-specific guest ELF via addr2line",
            "symbol_scope": (
                "The supplied static ELFs contain .symtab and .eh_frame but "
                "no .debug_info/.debug_line. Function names are reliable; "
                "source-line fields may be ??:0. No workload rebuild was "
                "needed and no host symbols are used."),
            "host_gem5_profile": False,
            "limitation": (
                "The shared checkpoint predates profiler attachment. The "
                "shadow stack therefore learns calls/returns inside the ROI "
                "and labels its initial ancestry as unavailable; it is not a "
                "full DWARF unwind of pre-ROI frames."),
            "pc_only_rule": (
                "If no call is observed and shadow depth never exceeds one, "
                "the output is explicitly labeled a function/PC profile in "
                "flame layout, not a call-stack flame graph."),
        },
        "profiles": profiles,
    }
    write_json(output / "guest_profile_summary.json", payload)
    return profiles, payload


def extend_policy_comparison(result: Path, latency: dict, summary: dict) -> None:
    path = result / "analysis/policy_comparison.csv"
    existing = read_csv(path)
    new_metric_prefixes = ("rnf0_", "rnf1_")
    existing = [row for row in existing if not row["metric"].startswith(
        new_metric_prefixes)]
    for policy in POLICIES:
        for rnf in (0, 1):
            current = latency["policies"][policy][f"rnf{rnf}"]
            baseline = latency["policies"]["random"][f"rnf{rnf}"]
            metrics = {
                f"rnf{rnf}_transaction_accepted_count": "accepted_count",
                f"rnf{rnf}_transaction_completed_count": "completed_count",
                f"rnf{rnf}_transaction_right_censored_count": (
                    "right_censored_count"),
                f"rnf{rnf}_weighted_mean_latency_ticks": (
                    "weighted_mean_latency_ticks"),
                f"rnf{rnf}_weighted_mean_latency_chi_cycles": (
                    "weighted_mean_latency_chi_cycles"),
                f"rnf{rnf}_weighted_mean_latency_ns": (
                    "weighted_mean_latency_ns"),
                f"rnf{rnf}_overall_p50_latency_chi_cycles": (
                    "p50_latency_chi_cycles"),
                f"rnf{rnf}_overall_p95_latency_chi_cycles": (
                    "p95_latency_chi_cycles"),
            }
            for metric_name, key in metrics.items():
                value = current[key]
                base = baseline[key]
                existing.append({
                    "policy": policy,
                    "metric": metric_name,
                    "value": value,
                    "random_value": base,
                    "delta_vs_random": value - base,
                    "percent_vs_random": (
                        (value - base) / base * 100.0 if base else 0.0),
                })
    write_csv(
        path,
        ["policy", "metric", "value", "random_value", "delta_vs_random",
         "percent_vs_random"], existing)

    performance_metrics = {
        "cpu0_guest_cycles": lambda d: d["cores"][0]["cycles"],
        "cpu1_guest_cycles": lambda d: d["cores"][1]["cycles"],
        "cpu0_guest_instructions": lambda d: d["cores"][0]["instructions"],
        "cpu1_guest_instructions": lambda d: d["cores"][1]["instructions"],
        "cpu0_memory_stall_cycles": lambda d: d["cores"][0][
            "memory_stall_cycles"],
        "cpu1_memory_stall_cycles": lambda d: d["cores"][1][
            "memory_stall_cycles"],
        "cpu0_exec_stall_cycles": lambda d: d["cores"][0]["stalled_cycles"],
        "cpu1_exec_stall_cycles": lambda d: d["cores"][1]["stalled_cycles"],
        "dram_read_bursts": lambda d: d["memory"]["read_bursts"],
        "dram_write_bursts": lambda d: d["memory"]["write_bursts"],
        "noc_internal_output_stall_cycles": lambda d: d["noc"][
            "internal_output_stall_cycles"],
        "noc_local_injection_stall_cycles": lambda d: d["noc"][
            "local_injection_stall_cycles"],
        "noc_local_delivery_stall_cycles": lambda d: d["noc"][
            "local_delivery_stall_cycles"],
    }
    # Re-open after writing latency rows, append missing original-performance
    # metrics, and remain idempotent across report regeneration.
    augmented = read_csv(path)
    names = set(performance_metrics)
    augmented = [row for row in augmented if row["metric"] not in names]
    baseline_data = summary["policies"]["random"]
    for policy in POLICIES:
        data = summary["policies"][policy]
        for metric_name, getter in performance_metrics.items():
            value = float(getter(data))
            base = float(getter(baseline_data))
            augmented.append({
                "policy": policy, "metric": metric_name, "value": value,
                "random_value": base, "delta_vs_random": value - base,
                "percent_vs_random": (
                    (value - base) / base * 100.0 if base else 0.0),
            })
    write_csv(
        path,
        ["policy", "metric", "value", "random_value", "delta_vs_random",
         "percent_vs_random"], augmented)


def prepare_model_change_artifacts(result: Path, summary: dict) -> dict:
    """Carry forward the controlled old-buffer/direct-PoCQ comparison.

    The current run is first checked against the direct-PoCQ values used by
    that comparison.  This keeps the inherited presentation slides honest
    while retaining exact provenance for the source artifact.
    """
    source_csv = MODEL_COMPARISON_SOURCE / "model_change_comparison.csv"
    source_json = MODEL_COMPARISON_SOURCE / "model_change_summary.json"
    if not source_csv.is_file() or not source_json.is_file():
        raise RuntimeError("prior controlled model-comparison artifacts missing")
    rows = read_csv(source_csv)
    direct_lookup = {
        (row["policy"], row["metric"]): float(row["direct_pocq_model"])
        for row in rows
    }
    getters = {
        "cpu0_ipc": lambda d: d["cores"][0]["ipc"],
        "cpu1_ipc": lambda d: d["cores"][1]["ipc"],
        "aggregate_ipc": lambda d: d["aggregate"]["ipc"],
        "aggregate_instructions": lambda d: d["aggregate"]["instructions"],
        "total_slc_victims": lambda d: d["slc"]["totalSlcVictims"],
        "dirty_slc_victims": lambda d: d["slc"]["dirtySlcVictims"],
        "sf_victims": lambda d: d["slc"]["sfVictims"],
        "victims_per_ki": lambda d: d["slc"]["victims_per_ki"],
        "stale_token_replays": lambda d: d["slc"]["staleTokenReplays"],
        "seq_conflict_replays": lambda d: d["slc"]["seqConflictReplays"],
        "victim_buffer_full_replays": lambda d: d["slc"][
            "victimBufferFullReplays"],
        "total_replay_responses": lambda d: d["slc"][
            "totalReplayResponses"],
        "replays_per_ki": lambda d: d["slc"]["replays_per_ki"],
        "request_full_cycles": lambda d: d["slc"]["requestFullCycles"],
        "response_full_cycles": lambda d: d["slc"]["responseFullCycles"],
        "no_credit": lambda d: d["slc"]["noCredit"],
        "service_stalls": lambda d: d["slc"]["serviceStalls"],
        "service_stalls_per_ki": lambda d: d["slc"][
            "service_stalls_per_ki"],
        "accepted_to_visible_latency_cycles": lambda d: d["slc"][
            "accepted_to_visible_latency_cycles"],
        "slc_hit_rate": lambda d: d["slc"]["slc_hit_rate"],
        "sf_hit_rate": lambda d: d["slc"]["sf_hit_rate"],
        "noc_traffic_score": lambda d: d["noc"]["total_traffic_score"],
        "dram_reads": lambda d: d["memory"]["read_requests"],
        "dram_writes": lambda d: d["memory"]["write_requests"],
    }
    mismatches = []
    for policy in POLICIES:
        data = summary["policies"][policy]
        for name, getter in getters.items():
            expected = direct_lookup[(policy, name)]
            actual = float(getter(data))
            if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
                mismatches.append({
                    "policy": policy, "metric": name,
                    "current": actual, "prior_direct_pocq": expected,
                })
    if mismatches:
        raise AssertionError(
            "current run diverges from prior direct-PoCQ controlled values: "
            f"{mismatches[:5]}")
    shutil.copy2(source_csv, result / "analysis/model_change_comparison.csv")
    shutil.copy2(source_json, result / "analysis/model_change_summary.json")
    return {
        "source_result": str(MODEL_COMPARISON_SOURCE.parent),
        "source_csv_sha256": sha256(source_csv),
        "source_json_sha256": sha256(source_json),
        "current_direct_pocq_metric_mismatches": mismatches,
        "current_matches_prior_direct_pocq": True,
    }


def extract_private_cache_config(config_path: Path) -> str:
    """Return stable L1/L2-only config.ini sections for equality hashing."""
    section = ""
    include = False
    output = []
    patterns = (
        re.compile(r"system\.cpu[01]\.(?:[id]cache)(?:\.|$)"),
        re.compile(
            r"system\.l2_wrappers[01]\.slices[0-3]\.inner_cache(?:\.|$)"),
    )
    for line in config_path.read_text(errors="replace").splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            include = any(pattern.match(section) for pattern in patterns)
        if include:
            output.append(line)
    return "\n".join(output) + "\n"


def validate_and_hash(
        result: Path, base_summary: dict, latency: dict,
        profiles: list[dict], unit_test_summary: dict | None,
        ab_summary: dict | None) -> dict:
    manifest = json.loads((result / "manifest.json").read_text())
    checks = []

    checkpoint_hashes = {}
    checkpoint_dir = result / "warmup/common_full_cpt"
    for path in sorted(checkpoint_dir.iterdir()):
        if path.is_file():
            checkpoint_hashes[path.name] = sha256(path)
    checkpoint_source = Path(manifest["common_checkpoint_source"])
    source_checkpoint_hashes = {
        path.name: sha256(path) for path in sorted(checkpoint_source.iterdir())
        if path.is_file()
    }
    same_checkpoint = True
    common_paths = set()
    strict_full = {}
    for policy in POLICIES:
        phase = base_summary["policies"][policy]["phase"]
        common_paths.add(phase["common_checkpoint"])
        same_checkpoint &= phase["common_checkpoint"] == str(checkpoint_dir)
        restore = phase["phases"]["restore"]
        slc = base_summary["policies"][policy]["slc"]
        strict_full[policy] = {
            "restore_valid_lines": restore["slc_valid_lines"],
            "restore_capacity_lines": restore["slc_capacity_lines"],
            "roi_peak_valid_lines": int(slc["slcPeakValidLines"]),
            "max_valid_ways_per_set": int(slc["maxValidWaysPerSet"]),
            "full_slc_cycles": int(slc["fullSlcCycles"]),
        }
        same_checkpoint &= (
            restore["slc_valid_lines"] ==
            restore["slc_capacity_lines"] == 16384 and
            int(slc["slcPeakValidLines"]) == 16384 and
            int(slc["maxValidWaysPerSet"]) == 16 and
            int(slc["fullSlcCycles"]) > 0)
    checks.append({
        "name": "same_strict_full_checkpoint",
        "passed": same_checkpoint and len(common_paths) == 1 and
                  checkpoint_hashes == source_checkpoint_hashes and
                  checkpoint_hashes == {
                      name: item["sha256"] for name, item in
                      manifest["common_checkpoint_files"].items()},
        "checkpoint_paths": sorted(common_paths),
        "checkpoint_sha256": checkpoint_hashes,
        "source_checkpoint": str(checkpoint_source),
        "source_checkpoint_sha256": source_checkpoint_hashes,
        "strict_full_evidence": strict_full,
    })

    roi_ticks = {}
    active = {}
    private_hashes = {}
    fixed_conditions = {}
    for policy in POLICIES:
        phase_document = base_summary["policies"][policy]["phase"]
        phase = phase_document["phases"]
        roi_ticks[policy] = phase["roi_end"]["tick"] - phase["roi_start"]["tick"]
        active[policy] = phase["roi_end"]["cpu_active_threads"]
        private_text = extract_private_cache_config(
            result / "runs" / policy / "config.ini")
        private_hashes[policy] = hashlib.sha256(private_text.encode()).hexdigest()
        fixed_conditions[policy] = {
            "seed": phase_document["random_seed"],
            "policy": phase_document["policy"],
            "topology": phase_document["topology"],
            "processes": phase_document["processes"],
        }
        (result / "validation").mkdir(parents=True, exist_ok=True)
        (result / "validation" / f"private_l1_l2_config_{policy}.txt").write_text(
            private_text, encoding="utf-8")
    checks.extend([
        {
            "name": "exact_equal_roi_ticks",
            "passed": set(roi_ticks.values()) == {2_000_000_000},
            "by_policy": roi_ticks,
        },
        {
            "name": "both_cpus_active_at_roi_end",
            "passed": all(value == [1, 1] for value in active.values()),
            "by_policy": active,
        },
        {
            "name": "private_l1_l2_replacement_config_identical",
            "passed": len(set(private_hashes.values())) == 1,
            "sha256_by_policy": private_hashes,
        },
        {
            "name": "fixed_seed_workloads_and_topology",
            "passed": (
                all(item["seed"] == 20260730 for item in
                    fixed_conditions.values()) and
                all(fixed_conditions[p]["policy"] == p for p in POLICIES) and
                len({json.dumps(item["topology"], sort_keys=True)
                     for item in fixed_conditions.values()}) == 1 and
                len({json.dumps(item["processes"], sort_keys=True)
                     for item in fixed_conditions.values()}) == 1),
            "by_policy": fixed_conditions,
        },
    ])

    victim_detail = {}
    victim_pass = True
    buffer_pass = True
    for policy in POLICIES:
        data = base_summary["policies"][policy]
        slc = data["slc"]
        values = {
            "replacement_attempts": int(slc["replacementAttempts"]),
            "clean_victims": int(slc["cleanSlcVictims"]),
            "dirty_victims": int(slc["dirtySlcVictims"]),
            "total_victims": int(slc["totalSlcVictims"]),
            "victim_by_requester_sum": sum(data["victim_by_requester"].values()),
            "victim_by_set_sum": sum(data["victim_by_set"].values()),
            "victim_by_policy": data["victim_by_policy"].get(policy, 0),
            "victim_buffer_full_replays": int(slc["victimBufferFullReplays"]),
        }
        local = (
            values["replacement_attempts"] == values["total_victims"] ==
            values["clean_victims"] + values["dirty_victims"] ==
            values["victim_by_requester_sum"] ==
            values["victim_by_set_sum"] == values["victim_by_policy"])
        victim_pass &= local
        buffer_pass &= values["victim_buffer_full_replays"] == 0
        values["conserved"] = local
        victim_detail[policy] = values
    checks.extend([
        {
            "name": "slc_victim_conservation",
            "passed": victim_pass,
            "by_policy": victim_detail,
        },
        {
            "name": "direct_pocq_victim_buffer_full_replays_zero",
            "passed": buffer_pass,
            "by_policy": {
                p: victim_detail[p]["victim_buffer_full_replays"]
                for p in POLICIES},
        },
    ])

    latency_detail = {}
    latency_pass = True
    for policy in POLICIES:
        latency_detail[policy] = {}
        for rnf in (0, 1):
            item = latency["policies"][policy][f"rnf{rnf}"]
            local = (
                item["exact_identity_unique"] and
                item["completed_count"] > 0 and
                item["trace_stop_tick"] - item["trace_start_tick"] ==
                2_000_000_000)
            latency_pass &= local
            latency_detail[policy][f"rnf{rnf}"] = {
                "passed": local,
                "accepted_count": item["accepted_count"],
                "completed_count": item["completed_count"],
                "right_censored_count": item["right_censored_count"],
                "trace_start_tick": item["trace_start_tick"],
                "trace_stop_tick": item["trace_stop_tick"],
            }
    checks.append({
        "name": "rnf_exact_identity_and_roi_bounds",
        "passed": latency_pass,
        "by_policy": latency_detail,
    })

    profile_pass = True
    profile_detail = []
    for item in profiles:
        phase = base_summary["policies"][item["policy"]]["phase"]["phases"]
        metadata = json.loads((result / item["raw_metadata"]).read_text())
        local = (
            item["samples"] > 0 and
            metadata["start_tick"] == phase["roi_start"]["tick"] and
            metadata["stop_tick"] == phase["roi_end"]["tick"] and
            not item["host_gem5_profile"])
        profile_pass &= local
        profile_detail.append({
            "policy": item["policy"], "cpu": item["cpu"],
            "passed": local, "samples": item["samples"],
            "start_tick": metadata["start_tick"],
            "stop_tick": metadata["stop_tick"],
            "commit_probe_coverage_fraction": item[
                "commit_probe_coverage_fraction"],
        })
    checks.append({
        "name": "guest_profiles_roi_only_and_nonempty",
        "passed": profile_pass,
        "profiles": profile_detail,
    })
    if unit_test_summary is not None:
        checks.append({
            "name": "chi_hnf_slc_unit_tests",
            "passed": unit_test_summary.get("failed_executables") == [] and
                      unit_test_summary.get("total_tests") == 284,
            **unit_test_summary,
        })
    if ab_summary is not None:
        checks.append({
            "name": "instrumentation_timing_neutrality_ab",
            "passed": (
                ab_summary.get("non_host_numeric_stat_differences") == 0 and
                ab_summary.get(
                    "formal_non_host_numeric_stat_differences") == 0),
            **ab_summary,
        })

    source_paths = {
        "input": [
            ROOT.parent / "workloads/dual-core-se-dependencies.json",
            WORKLOADS[0]["elf"], WORKLOADS[1]["elf"],
            ROOT.parent / "workloads/omnetpp-3.3.2/rv64/share/tokenring/omnetpp.ini",
            *sorted(path for path in checkpoint_dir.iterdir() if path.is_file()),
            *sorted(path for path in checkpoint_source.iterdir()
                    if path.is_file()),
        ],
        "gem5": [ROOT / "build/RISCV/gem5.opt"],
        "config_and_runner": [
            ROOT / "configs/example/kmhv2_chi_2x2_hnf_se.py", RUNNER,
        ],
        "instrumentation": [
            ROOT / "src/mem/cache/CHI/Cache2ChiBridge.py",
            ROOT / "src/mem/cache/CHI/Cache2ChiBridge.hh",
            ROOT / "src/mem/cache/CHI/Cache2ChiBridge.cc",
            ROOT / "src/cpu/o3/probe/GuestCallStackProfiler.py",
            ROOT / "src/cpu/o3/probe/guest_call_stack_profiler.hh",
            ROOT / "src/cpu/o3/probe/guest_call_stack_profiler.cc",
            ROOT / "src/cpu/o3/probe/SConscript",
        ],
        "direct_pocq_model_source": [
            ROOT / "src/mem/cache/CHI/HnfCoherencyController.cc",
            ROOT / "src/mem/cache/CHI/HnfCoherencyController.hh",
            ROOT / "src/mem/cache/CHI/HnfSLCSF.cc",
            ROOT / "src/mem/cache/CHI/HnfSLCSF.hh",
            ROOT / "src/mem/cache/CHI/HnfSLCSFBackend.cc",
            ROOT / "src/mem/cache/CHI/HnfSLCSFBackend.hh",
            ROOT / "src/mem/cache/CHI/HnfSLCSFRequest.hh",
            ROOT / "src/mem/cache/CHI/HnfSLCSFResponse.hh",
            ROOT / "src/mem/cache/CHI/HomeNodeFull.py",
            ROOT / "src/mem/cache/CHI/SlcSnoopFilter.cc",
            ROOT / "src/mem/cache/CHI/SlcSnoopFilter.py",
        ],
        "analysis_and_charts": [
            Path(__file__).resolve(),
            ROOT / "util/xs_scripts/chi_2x2_instrumentation_ab.py",
            ROOT / "util/xs_scripts/chi_2x2_latency_flame_requirements.txt",
            ROOT / "util/xs_scripts/chi_2x2_dual_proxy_presentation.py",
            ROOT / "util/xs_scripts/chi_2x2_latency_flame_presentation.py",
        ],
        "controlled_model_comparison": [
            MODEL_COMPARISON_SOURCE / "model_change_comparison.csv",
            MODEL_COMPARISON_SOURCE / "model_change_summary.json",
        ],
        "execution_record": [
            result / "manifest.json",
            result / "dual-core-se-dependencies.json",
            *(result / "runs" / policy / name
              for policy in POLICIES
              for name in ("command.json", "phase_metadata.json")),
        ],
        "symbolization_tools": [Path(ADDR2LINE), Path(READELF)],
    }
    hash_entries = []
    for category, paths in source_paths.items():
        for path in paths:
            if not path.is_file():
                raise RuntimeError(f"hash input is missing: {path}")
            hash_entries.append({
                "category": category,
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            })
    hash_manifest = {
        "schema_version": 1,
        "algorithm": "SHA-256",
        "scope": (
            "all fixed inputs, guest ELFs, checkpoint files, gem5 binary, "
            "config, runner, instrumentation, analysis and chart/PPT scripts"),
        "entries": hash_entries,
    }
    write_json(result / "validation/sha256_manifest.json", hash_manifest)
    checks.append({
        "name": "required_sha256_recorded",
        "passed": True,
        "entries": len(hash_entries),
        "manifest": "validation/sha256_manifest.json",
    })

    validation = {
        "schema_version": 1,
        "status": "passed" if all(item["passed"] for item in checks) else "failed",
        "checks": checks,
        "all_passed": all(item["passed"] for item in checks),
    }
    write_json(result / "validation/validation_summary.json", validation)
    if not validation["all_passed"]:
        failures = [item["name"] for item in checks if not item["passed"]]
        raise AssertionError(f"validation failures: {failures}")
    return validation


def run_unit_tests(result: Path) -> dict:
    names = [
        "hnf_slcsf", "slc_snoop_filter", "hnf_slcsf_backend",
        "hnf_coherency_controller", "cache2chi_bridge",
        "chi2classic_mem_bridge", "hnf_seq_pocq_state_graph",
        "hnf_pocq_state_graph", "home_link_layer", "hnf_cc_types",
    ]
    directory = ROOT / "build/RISCV/mem/cache/CHI"
    records = []
    total = 0
    failed = []
    log_lines = []
    pattern = re.compile(r"\[==========\] (\d+) tests? from")
    for name in names:
        binary = directory / f"{name}.test.opt"
        completed = subprocess.run(
            [str(binary), "--gtest_brief=1"], cwd=ROOT,
            text=True, capture_output=True, check=False)
        output = completed.stdout + completed.stderr
        match = pattern.search(output)
        count = int(match.group(1)) if match else 0
        total += count
        if completed.returncode:
            failed.append(name)
        records.append({
            "name": name,
            "binary": str(binary),
            "binary_sha256": sha256(binary),
            "return_code": completed.returncode,
            "tests": count,
        })
        log_lines.extend([f"===== {name} =====", output.rstrip(), ""])
    log_path = result / "validation/chi_hnf_slc_unit_tests.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(log_lines), encoding="utf-8")
    summary = {
        "executables": len(names),
        "total_tests": total,
        "failed_executables": failed,
        "records": records,
        "log": str(log_path.relative_to(result)),
    }
    write_json(result / "validation/chi_hnf_slc_unit_tests.json", summary)
    return summary


def load_ab_summary(path: Path | None) -> dict | None:
    if path is None:
        return None
    if not path.is_file():
        raise RuntimeError(f"A/B summary is missing: {path}")
    return json.loads(path.read_text())


def record_analysis_environment(result: Path) -> dict:
    import cairosvg
    import matplotlib
    import PIL
    import pptx
    payload = {
        "python": sys.version,
        "packages": {
            "matplotlib": matplotlib.__version__,
            "Pillow": PIL.__version__,
            "python-pptx": pptx.__version__,
            "CairoSVG": cairosvg.__version__,
        },
        "addr2line": ADDR2LINE,
        "readelf": READELF,
        "requirements": (
            "util/xs_scripts/chi_2x2_latency_flame_requirements.txt"),
    }
    write_json(result / "validation/analysis_environment.json", payload)
    return payload


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(row) + " |" for row in rows),
    ])


def build_cross_domain_interpretation(
        summary: dict, latency: dict, profiles: list[dict]) -> dict:
    profile_lookup = {
        (item["cpu"], item["policy"]): item for item in profiles
    }
    workloads = {}
    for cpu in (0, 1):
        policies = {}
        for policy in POLICIES:
            core = summary["policies"][policy]["cores"][cpu]
            profile = profile_lookup[(cpu, policy)]
            top = profile["top_leaf_functions"][0]
            policies[policy] = {
                "ipc": core["ipc"],
                "l1_demand_mpki": core["l1_demand_mpki"],
                "l2_demand_mpki": core["l2_demand_mpki"],
                "memory_stall_cycles": core["memory_stall_cycles"],
                "rnf_weighted_latency_chi_cycles": latency["policies"][policy][
                    f"rnf{cpu}"]["weighted_mean_latency_chi_cycles"],
                "top_sampled_leaf_function": top["function"],
                "top_sample_fraction": top["sample_fraction"],
                "top_inclusive_functions": [
                    item["function"]
                    for item in profile["top_inclusive_functions"][:10]],
                "guest_profile_samples": profile["samples"],
            }
        workloads[f"cpu{cpu}"] = {
            "workload": WORKLOADS[cpu]["name"],
            "highest_ipc_policy": max(
                POLICIES, key=lambda p: policies[p]["ipc"]),
            "lowest_l2_mpki_policy": min(
                POLICIES, key=lambda p: policies[p]["l2_demand_mpki"]),
            "lowest_memory_stall_policy": min(
                POLICIES, key=lambda p: policies[p]["memory_stall_cycles"]),
            "lowest_rnf_weighted_latency_policy": min(
                POLICIES,
                key=lambda p: policies[p]["rnf_weighted_latency_chi_cycles"]),
            "top_function_stable_across_policies": len({
                policies[p]["top_sampled_leaf_function"] for p in POLICIES
            }) == 1,
            "policies": policies,
        }
    return {
        "scope_warning": (
            "Descriptive correspondence for three policies at one fixed "
            "checkpoint; it does not establish causality."),
        "workloads": workloads,
    }


def generate_reports(
        result: Path, summary: dict, latency: dict, profiles: list[dict],
        validation: dict, ab_summary: dict | None) -> None:
    overview_rows = []
    pressure_rows = []
    for policy in POLICIES:
        data = summary["policies"][policy]
        overview_rows.append([
            POLICY_LABEL[policy],
            f"{data['cores'][0]['ipc']:.5f}",
            f"{data['cores'][1]['ipc']:.5f}",
            f"{data['cores'][0]['l2_demand_mpki']:.3f}",
            f"{data['cores'][1]['l2_demand_mpki']:.3f}",
            str(int(data['slc']['totalSlcVictims'])),
            str(int(data['slc']['totalReplayResponses'])),
            str(int(data['slc']['serviceStalls'])),
            str(int(data['slc']['noCredit'])),
            f"{data['host']['seconds']:.2f}",
        ])
        pressure_rows.append([
            POLICY_LABEL[policy],
            str(int(data['cores'][0]['memory_stall_cycles'])),
            str(int(data['cores'][1]['memory_stall_cycles'])),
            str(int(data['memory']['read_requests'])),
            str(int(data['memory']['write_requests'])),
            str(int(data['memory']['read_bursts'])),
            str(int(data['memory']['write_bursts'])),
            str(int(data['noc']['total_traffic_score'])),
            str(int(data['noc']['internal_output_stall_cycles'] +
                    data['noc']['local_injection_stall_cycles'] +
                    data['noc']['local_delivery_stall_cycles'])),
        ])
    latency_rows = []
    for policy in POLICIES:
        for rnf in (0, 1):
            item = latency["policies"][policy][f"rnf{rnf}"]
            latency_rows.append([
                POLICY_LABEL[policy], f"RNF{rnf}/CPU{rnf}",
                str(item["accepted_count"]), str(item["completed_count"]),
                str(item["right_censored_count"]),
                f"{item['weighted_mean_latency_chi_cycles']:.3f}",
                f"{item['weighted_mean_latency_ticks']:.1f}",
                f"{item['weighted_mean_latency_ns']:.3f}",
                f"{item['p50_latency_chi_cycles']:.3f}",
                f"{item['p95_latency_chi_cycles']:.3f}",
                f"{item['weighted_mean_percent_vs_random']:+.3f}%",
            ])
    type_rows = []
    for policy in POLICIES:
        for rnf in (0, 1):
            items = latency["policies"][policy][f"rnf{rnf}"][
                "transaction_types"]
            for kind, item in sorted(items.items()):
                type_rows.append([
                    POLICY_LABEL[policy], f"RNF{rnf}", kind,
                    item["terminal_response_types"],
                    str(item["accepted_count"]),
                    str(item["completed_count"]),
                    str(item["right_censored_count"]),
                    f"{item['mean_latency_chi_cycles']:.3f}",
                    f"{item['p50_latency_chi_cycles']:.3f}",
                    f"{item['p95_latency_chi_cycles']:.3f}",
                    (f"{item['mean_percent_vs_random']:+.3f}%"
                     if item["mean_percent_vs_random"] is not None else "N/A"),
                ])
    top_rows = []
    for profile in profiles:
        functions = profile["top_leaf_functions"][:5]
        top_rows.append([
            POLICY_LABEL[profile["policy"]],
            f"CPU{profile['cpu']}/{WORKLOADS[profile['cpu']]['slug']}",
            ("call-stack" if profile["is_call_stack_flame_graph"] else
             "PC/function (flame layout)"),
            str(profile["samples"]),
            f"{profile['commit_probe_coverage_fraction']:.4%}",
            f"{profile['unresolved_leaf_ratio']:.3%}",
            ", ".join(
                f"`{item['function']}` ({item['sample_fraction']:.1%})"
                for item in functions),
            ", ".join(
                f"`{item['function']}` ({item['sample_fraction']:.1%})"
                for item in profile["top_inclusive_functions"][:5]),
        ])
    correspondence = []
    cross = summary["cross_domain_interpretation"]["workloads"]
    for cpu in (0, 1):
        item = cross[f"cpu{cpu}"]
        policy_bits = []
        for policy in POLICIES:
            values = item["policies"][policy]
            policy_bits.append(
                f"{POLICY_LABEL[policy]}: IPC {values['ipc']:.5f}, "
                f"L2 MPKI {values['l2_demand_mpki']:.3f}, "
                f"memory-stall {values['memory_stall_cycles']:.0f}, "
                f"RNF weighted {values['rnf_weighted_latency_chi_cycles']:.2f} cyc, "
                f"top `{values['top_sampled_leaf_function']}` "
                f"{values['top_sample_fraction']:.1%}")
        stability = (
            "三策略的第一热点函数相同" if
            item["top_function_stable_across_policies"] else
            "三策略的第一热点函数并不完全相同")
        correspondence.append(
            f"- CPU{cpu}/{WORKLOADS[cpu]['slug']}：{stability}。" +
            "；".join(policy_bits) + "。最高 IPC 为 " +
            f"{POLICY_LABEL[item['highest_ipc_policy']]}，最低 L2 MPKI 为 " +
            f"{POLICY_LABEL[item['lowest_l2_mpki_policy']]}，最低 memory-stall 为 " +
            f"{POLICY_LABEL[item['lowest_memory_stall_policy']]}，最低 RNF "
            f"weighted latency 为 " +
            f"{POLICY_LABEL[item['lowest_rnf_weighted_latency_policy']]}。")
    correspondence_text = "\n".join(correspondence)
    cpu0_tops = {
        cross["cpu0"]["policies"][policy]["top_sampled_leaf_function"]
        for policy in POLICIES}
    cpu1_inclusive = {
        function
        for policy in POLICIES
        for function in cross["cpu1"]["policies"][policy][
            "top_inclusive_functions"]
    }
    phase_notes = []
    if cpu0_tops == {"quantum_gate1"}:
        phase_notes.append(
            "CPU0 三策略的 sampled leaf 都是 `quantum_gate1`（100%），"
            "说明该固定 ROI 位于 Shor 量子门核心长循环；由于 ROI 内无 call，"
            "不能补造其 checkpoint 之前的 caller。")
    omnet_markers = {
        "cModule::buildInside()", "Computer::doBuildInside()",
        "cGate::connectTo(cGate*, cChannel*)", "_Unwind_RaiseException",
        "__cxa_throw",
    }
    present_markers = sorted(omnet_markers & cpu1_inclusive)
    if present_markers:
        phase_notes.append(
            "CPU1 inclusive 栈包含 " +
            "、".join(f"`{name}`" for name in present_markers) +
            "；因此该固定 ROI 的 OMNeT++ 热点主要仍在模块构建/连接及 C++ "
            "exception unwind 路径，而不是稳态 token-passing event loop。"
            "相应 IPC、MPKI 与 RNF latency 必须解释为这个 guest phase。")
    phase_note_text = "\n\n".join(phase_notes)

    if ab_summary:
        formal_overheads = ", ".join(
            f"{POLICY_LABEL[p]} "
            f"{ab_summary['formal_2b_comparison_to_uninstrumented_prior_direct_pocq'][p]['combined_instrumentation_host_overhead_percent']:+.2f}%"
            for p in POLICIES)
        ab_text = (
            f"20M-tick 短跑 A/B 的所有非 host 数值 stats 差异数为 "
            f"{ab_summary['non_host_numeric_stat_differences']}；latency-only、"
            f"profile-only、两者同时的 hostSeconds 开销依次为 "
            f"{ab_summary['latency_only_host_overhead_percent']:+.2f}%、"
            f"{ab_summary['profile_only_host_overhead_percent']:+.2f}%、"
            f"{ab_summary['combined_host_overhead_percent']:+.2f}%。"
            f"正式 2B-tick 相对未采样的上次 direct-PoCQ 同统计运行为："
            f"{formal_overheads}；其非 host stats 差异数为 "
            f"{ab_summary['formal_non_host_numeric_stat_differences']}。")
    else:
        ab_text = (
        "未向本脚本提供 instrumentation A/B 摘要；模拟时序中性仍由源码结构与正式统计检查验证。")
    report = f"""# 2×2 CHI 双核 SLC 替换策略实验报告

## 结论摘要

本实验使用同一个严格满载 checkpoint，在 direct-PoCQ dirty-victim 模型下分别运行 LRU、Random、SRRIP；三组正式 ROI 均为 **2,000,000,000 simulated ticks**，seed 均为 **20260730**，结束时两个 CPU 均 active。全部验证项通过，包括 SLC victim 守恒、`victimBufferFullReplays == 0` 与 284/284 项 CHI/HNF/SLC 单测。

这里的 workload 是公开源码代理 workload，不是 SPEC CPU2006 合规跑分。所有策略比较都只对本固定 checkpoint 与固定 ROI 成立。

## 固定配置与边界

- CPU0：libquantum 0.2.4 Shor，参数 `1397 8`。
- CPU1：OMNeT++ 3.3.2 Token Ring，参数 `-f omnetpp.ini`。
- 2×2 CHI：2 RNF、1 HNF、1 SN；shared SLC 为 1 MiB、1024 sets、16 ways、64 B line。
- 只改变 shared SLC 替换策略；三组 L1/L2 配置抽取结果 SHA-256 完全一致。
- transaction 与 profile recorder 都在精确的 `roi_start` 启动，在精确的 `roi_end` 停止；不统计 restore、warm-up 或 drain。

## 性能、容量与压力指标

{markdown_table(
    ['策略','CPU0 IPC','CPU1 IPC','CPU0 L2 MPKI','CPU1 L2 MPKI',
     'SLC victims','Replay','HNF service stall','No-credit','host 秒'],
    overview_rows)}

`cycles` 是 guest CPU 的 simulated cycles；IPC 是 guest committed instructions / guest cycles。`host 秒` 只表示运行 gem5 的宿主机 wall-clock 时间，不能与 guest simulated time 混用。DRAM 与 NoC 的完整计数、每核 L1/L2 MPKI、memory-stall、victim、Replay 与 HNF 压力指标见 `analysis/summary.json` 和 `analysis/policy_comparison.csv`。

{markdown_table(
    ['策略','CPU0 memory-stall cyc','CPU1 memory-stall cyc','DRAM reads',
     'DRAM writes','DRAM read bursts','DRAM write bursts','NoC traffic',
     'NoC stall cyc'], pressure_rows)}

## RNF transaction latency

定义如下：REQ latency 从 RNF 成功注入 REQ 开始，到相同 `{{SrcID, TxnID}}` 对应的 terminal RSP/DAT 完成并 retire；SNP latency 从 RNF 接受 TXSNP 开始，到相同 wire transaction ID 的最终 SnpResp/SnpRespData 注入。所有匹配都在 RNF bridge transaction table 内完成，不使用时间邻近或 opcode 猜测。ROI 末仍 outstanding 的 transaction 记为 right-censored，不进入 mean/P50/P95。

每个 type 的 mean 是该 type 全部 completed instances 的算术平均；每个 RNF 的 weighted mean 定义为 `sum(count_type × mean_type) / sum(count_type)`。P50/P95 采用 nearest-rank。CHI cycle 用 raw trace 内的 clock period 换算；ns 用 `simFreq` 换算。

若 `SNP:SnpCleanInvalid → RSP:SnpResp` 显示 0 cycle，含义是当前 bridge 在接受 snoop 的同一 simulated tick 就成功注入了无数据响应；这是模型允许的同步快路径，不是漏采样。带数据或受 backpressure 的 snoop 会按实际结束 tick 计时。

{markdown_table(
    ['策略','RNF','accepted','completed','censored','weighted cyc','ticks','ns',
     'overall P50 cyc','overall P95 cyc','weighted vs Random'], latency_rows)}

### 按正式 CHI transaction type

{markdown_table(
    ['策略','RNF','CHI type','terminal response type','accepted','completed',
     'censored','mean cyc','P50 cyc','P95 cyc','mean vs Random'],
    type_rows)}

图：`analysis/rnf0_transaction_latency.png/.svg` 与 `analysis/rnf1_transaction_latency.png/.svg`。完整逐 type 机器可读结果在 `analysis/rnf_transaction_latency.csv` 和 `.json`；原始逐 transaction 记录在各 `runs/<policy>/rnf*_transaction_latency_raw.csv`。

## Guest workload flame graph

这些图来自 guest O3 Commit probe 的 retired macro-instruction 采样，并按 CPU 对应的 guest ELF 离线符号化；它们不是宿主机 gem5 的 perf/call stack。采样周期为每 1000 条被 probe 观察到的 retired guest macro-instructions 一次。共享 checkpoint 在 profiler attachment 之前生成，因此 ROI 起点以前的祖先栈无法恢复：图中显式加入 `[ROI root: pre-ROI ancestry unavailable]`，只把 ROI 内观察到的 call/return 维护为 shadow call stack。CPU1 在 ROI 内观察到大量 call/return，因此标为 **guest ROI-local shadow-call-stack flame graph**；CPU0 在三策略 ROI 内都没有观察到 call、shadow depth 也未超过 1，所以其同名 SVG 明确标为 **guest function/PC profile in flame layout（不是 call-stack flame graph）**。两者都不是完整 DWARF unwind。

两个现有 static ELF 都带 `.symtab` 与 `.eh_frame`，但不带 `.debug_info/.debug_line`；因此本次没有另编译 `-g -fno-omit-frame-pointer` 版本，也没有改变正式 workload。`addr2line -f -C` 能可靠给出函数名，源码行可能显示 `??:0`。未解析比例在下表和每份 profile metadata 中按“函数名无法解析”统计。

{markdown_table(
    ['策略','workload','profile 分类','样本数','Commit 覆盖率','未解析 leaf',
     'Top leaf functions','Top inclusive functions'],
    top_rows)}

每个 profile 的 raw CSV、metadata、folded stack、symbol map、SVG 与 PNG 都在 `analysis/flamegraphs/`。`commit_probe_coverage_fraction` 用 profiler 观察计数除以 gem5 `committedInsts`；两者因 Commit probe 对部分 SE 路径的可见性可能不完全一致，因此同时保留原始分母和分子。

{phase_note_text}

## 与 IPC、MPKI、memory-stall 的对应

函数热点给出“ROI 中退休 guest 指令落在哪些调用路径”，IPC/MPKI/memory-stall 给出同一模拟窗口的性能和存储系统压力。比较时应先看各策略的 sampled function distribution 是否稳定，再把 RNF weighted/type latency、L2 MPKI 与 `memstall_any_load` 的同向变化作为对应关系；只有三种策略、单一 checkpoint，不能从相关性推出因果。逐策略数据已并列放入 `analysis/summary.json`，PPT 中也采用这一限制。

{correspondence_text}

## Instrumentation 开销与语义

{ab_text} 其中负值不表示 instrumentation 加速了 gem5，而表示单次 host wall-clock 抖动已大于待测开销；因此这里只报告原始观测范围，不给出虚假的稳定正开销。两个 recorder 只做宿主侧 vector/CSV 记录，不调度新的 simulated event，不改变 CHI message、credit、queue 或 terminal 条件；A/B 对照的 guest/CHI/SLC/NoC/DRAM stats 完全一致时，才把它标记为 timing-neutral。host overhead 不是 guest latency。

源码位置：

- `src/mem/cache/CHI/Cache2ChiBridge.cc/.hh/.py`：RNF exact-ID latency。
- `src/cpu/o3/probe/guest_call_stack_profiler.cc/.hh` 与 `GuestCallStackProfiler.py`：guest ROI sampler。
- `configs/example/kmhv2_chi_2x2_hnf_se.py`：精确 ROI start/stop。

## 验证与可复现性

`validation/validation_summary.json` 状态为 `{validation['status']}`。SHA-256 清单在 `validation/sha256_manifest.json`，包含输入、两个 ELF、OMNeT++ ini、checkpoint、gem5、config、runner、instrumentation 和图表/PPT 脚本。

完整复现命令：

```bash
cd {ROOT}
python3 -m venv /tmp/chi-analysis-venv-20260801
/tmp/chi-analysis-venv-20260801/bin/python -m pip install -r util/xs_scripts/chi_2x2_latency_flame_requirements.txt
scons build/RISCV/gem5.opt -j8
scons --unit-test build/RISCV/mem/cache/CHI/hnf_slcsf.test.opt build/RISCV/mem/cache/CHI/slc_snoop_filter.test.opt build/RISCV/mem/cache/CHI/hnf_slcsf_backend.test.opt build/RISCV/mem/cache/CHI/hnf_coherency_controller.test.opt build/RISCV/mem/cache/CHI/cache2chi_bridge.test.opt build/RISCV/mem/cache/CHI/chi2classic_mem_bridge.test.opt build/RISCV/mem/cache/CHI/hnf_seq_pocq_state_graph.test.opt build/RISCV/mem/cache/CHI/hnf_pocq_state_graph.test.opt build/RISCV/mem/cache/CHI/home_link_layer.test.opt build/RISCV/mem/cache/CHI/hnf_cc_types.test.opt -j8
python3 util/xs_scripts/chi_2x2_dual_proxy_experiment.py run {result.relative_to(ROOT)} --skip-warmup --checkpoint-source results/chi-2x2-dual-public-proxy-direct-victim-v1/warmup/common_full_cpt --roi-ticks 2000000000 --seed 20260730 --guest-stack-sample-period-insts 1000
python3 util/xs_scripts/chi_2x2_instrumentation_ab.py {result.relative_to(ROOT)} --ticks 20000000
/tmp/chi-analysis-venv-20260801/bin/python util/xs_scripts/chi_2x2_latency_flame_analysis.py {result.relative_to(ROOT)} --run-unit-tests --ab-summary {result.relative_to(ROOT)}/validation/instrumentation_ab_summary.json
/tmp/chi-analysis-venv-20260801/bin/python util/xs_scripts/chi_2x2_latency_flame_presentation.py {result.relative_to(ROOT)}
```

结果目录：`{result}`。
"""
    (result / "EXPERIMENT_REPORT_ZH.md").write_text(report, encoding="utf-8")

    readme = f"""# CHI 2×2 dual-public-proxy RNF latency + guest flame v1

Validated result directory for the fixed 2,000,000,000-tick LRU/Random/SRRIP experiment. Workloads are public-source proxies, not SPEC CPU2006 scores.

## Key artifacts

- Chinese report: [EXPERIMENT_REPORT_ZH.md](EXPERIMENT_REPORT_ZH.md)
- Main machine summary: [analysis/summary.json](analysis/summary.json)
- Policy comparison: [analysis/policy_comparison.csv](analysis/policy_comparison.csv)
- RNF latency: [analysis/rnf_transaction_latency.csv](analysis/rnf_transaction_latency.csv), [JSON](analysis/rnf_transaction_latency.json)
- RNF plots: [RNF0 PNG](analysis/rnf0_transaction_latency.png), [RNF1 PNG](analysis/rnf1_transaction_latency.png)
- Guest profiles: [analysis/flamegraphs](analysis/flamegraphs)
- Validation: [validation/validation_summary.json](validation/validation_summary.json)
- SHA-256 manifest: [validation/sha256_manifest.json](validation/sha256_manifest.json)
- Chinese presentation: [中文汇报_RNF延迟与Guest火焰图.pptx](中文汇报_RNF延迟与Guest火焰图.pptx)

## Terminology guardrail

- simulated ticks/cycles: modeled gem5 time;
- guest workload IPC: committed guest instructions per guest CPU cycle;
- host wall-clock: time spent running gem5 on the host;
- RNF transaction latency: exact-ID matched CHI transaction duration inside the ROI;
- guest flame graph: CPU-specific guest ELF symbols from ROI retired-instruction samples;
- host gem5 profiling: not used for these workload flame graphs.

See the Chinese report for methodology, limitations, results, and full reproduction commands.
"""
    (result / "README.md").write_text(readme, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--run-unit-tests", action="store_true")
    parser.add_argument("--ab-summary", type=Path)
    args = parser.parse_args()
    result = args.result.resolve()
    try:
        runner = load_runner_module()
        # Rebuild the original performance summary first, making this script
        # idempotent even after artifacts are copied or reports regenerated.
        runner.analyze(result)
        summary_path = result / "analysis/summary.json"
        summary = json.loads(summary_path.read_text())
        summary["controlled_model_comparison_provenance"] = (
            prepare_model_change_artifacts(result, summary))
        latency_rows, latency = latency_analysis(result)
        write_csv(
            result / "analysis/rnf_transaction_latency.csv",
            list(latency_rows[0]), latency_rows)
        write_json(result / "analysis/rnf_transaction_latency.json", latency)
        latency_plots(result, latency_rows, latency)
        profiles, profile_payload = build_flame_profiles(result, summary)
        extend_policy_comparison(result, latency, summary)
        unit_tests = run_unit_tests(result) if args.run_unit_tests else None
        ab_summary = load_ab_summary(args.ab_summary)
        summary["analysis_environment"] = record_analysis_environment(result)
        summary["rnf_transaction_latency"] = latency
        summary["guest_workload_profiles"] = profile_payload
        summary["cross_domain_interpretation"] = (
            build_cross_domain_interpretation(summary, latency, profiles))
        summary["definitions"] = {
            "simulated_ticks_cycles": "gem5 modeled time",
            "guest_workload_ipc": "guest committedInsts / guest numCycles",
            "host_wall_clock": "hostSeconds; not simulated time",
            "rnf_transaction_latency": latency["definition"],
            "guest_flame_graph": profile_payload["method"],
            "host_gem5_profiling": "not used for workload flame graphs",
        }
        validation = validate_and_hash(
            result, summary, latency, profiles, unit_tests, ab_summary)
        summary["extended_validation"] = validation
        write_json(summary_path, summary)
        generate_reports(
            result, summary, latency, profiles, validation, ab_summary)
        print(json.dumps({
            "status": "ok", "result": str(result),
            "latency_rows": len(latency_rows),
            "profiles": len(profiles),
            "validation": validation["status"],
        }, ensure_ascii=False, indent=2))
    except (AssertionError, OSError, RuntimeError, subprocess.SubprocessError,
            ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
