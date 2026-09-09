import math
from collections import defaultdict

from .config import Workload


GROUP_FIELDS = ("topology", "profile_id", "backend_id", "workload", "distribution", "burst_beats",
                "workload_digest", "comparison_scope", "source_digest", "build_digest", "profile_digest")
COMMON_FIELDS = tuple(field for field in GROUP_FIELDS if field not in ("workload", "workload_digest"))


def quantile(values, fraction):
    values = sorted(values)
    if not values or not 0 <= fraction <= 1 or any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("quantile requires finite nonnegative samples and a valid fraction")
    return values[max(0, math.ceil(len(values) * fraction) - 1)]


def latency_summary(values):
    values = list(values)
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p95": None, "p99": None}
    return {"count": len(values), "mean": sum(values) / len(values),
            "p50": quantile(values, .50), "p95": quantile(values, .95), "p99": quantile(values, .99)}


def event_window(events, begin, end):
    if not begin < end:
        raise ValueError("ROI must have positive duration")
    return sum(value for tick, value in events if begin <= tick < end)


def outstanding_window(intervals, begin, end):
    if not begin < end or any(start > stop for start, stop in intervals):
        raise ValueError("invalid transaction or ROI interval")
    changes = defaultdict(int)
    initial = sum(start <= begin < stop for start, stop in intervals)
    final = sum(start <= end < stop for start, stop in intervals)
    for start, stop in intervals:
        if begin < start < end:
            changes[start] += 1
        if begin < stop < end:
            changes[stop] -= 1
    previous, occupancy, integral, peak = begin, initial, 0, initial
    for tick, delta in sorted(changes.items()):
        integral += occupancy * (tick - previous)
        occupancy += delta
        peak = max(peak, occupancy)
        previous = tick
    integral += occupancy * (end - previous)
    return {"average": integral / (end - begin), "peak": peak,
            "begin_inflight": initial, "end_inflight": final}


def scope_statistics(byte_events, burst_events, intervals, tile_completions,
                     tile_latencies, begin, end, ticks_per_second):
    seconds = (end - begin) / ticks_per_second
    counts = {direction: event_window(byte_events[direction], begin, end) for direction in ("read", "write")}
    cohort = [stop - start for group in intervals.values() for start, stop in group if begin <= start < end]
    return {"read_bytes": counts["read"], "write_bytes": counts["write"],
            "read_Bps": counts["read"] / seconds, "write_Bps": counts["write"] / seconds,
            "bandwidth_Bps": sum(counts.values()) / seconds,
            "read_bursts_per_second": event_window(burst_events["read"], begin, end) / seconds,
            "write_bursts_per_second": event_window(burst_events["write"], begin, end) / seconds,
            "tiles_per_second": sum(begin <= tick < end for tick in tile_completions) / seconds,
            "transaction_latency_ticks": latency_summary(cohort),
            "tile_latency_ticks": latency_summary(tile_latencies),
            "outstanding": {direction: outstanding_window(intervals[direction], begin, end)
                            for direction in ("read", "write")}}


def eligible(row):
    required = ("bandwidth_Bps", "router_buffer_bytes", "n_read", "n_write", "p99_ticks")
    return (row.get("status") == "valid" and row.get("stable") is True and
            row.get("drained") is True and row.get("full_timing") is True and
            all(isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool) and
                math.isfinite(row[key]) and row[key] >= 0 for key in required) and
            row["bandwidth_Bps"] > 0)


def active_outstanding(row):
    if row["workload"] == Workload.LOAD_ONLY:
        return row["n_read"]
    if row["workload"] == Workload.STORE_ONLY:
        return row["n_write"]
    return row["n_read"] + row["n_write"]


def analyze_candidates(rows, budgets=()):
    groups = {tuple(row[field] for field in GROUP_FIELDS) for row in rows}
    if len(groups) > 1:
        raise ValueError("candidate references require one frozen comparison group")
    valid = sorted((row for row in rows if eligible(row)), key=lambda row: row["case_id"])
    if not valid:
        return {"B_ref": None, "F95": [], "pareto": [], "peak": None,
                "resource_recommendation": None, "outstanding_recommendation": None,
                "conditional_N95": {}, "budgets": {}}
    reference = max(row["bandwidth_Bps"] for row in valid)
    near = [row for row in valid if row["bandwidth_Bps"] >= .95 * reference]
    resource_key = lambda row: (row["router_buffer_bytes"], active_outstanding(row),
                                row["p99_ticks"], row.get("complexity", 0), row["case_id"])
    outstanding_key = lambda row: (active_outstanding(row), row["router_buffer_bytes"],
                                   row["p99_ticks"], row.get("complexity", 0), row["case_id"])
    points = {row["case_id"]: (-row["bandwidth_Bps"], row["router_buffer_bytes"],
                               row["n_read"] if row["workload"] != Workload.STORE_ONLY else 0,
                               row["n_write"] if row["workload"] != Workload.LOAD_ONLY else 0,
                               row["p99_ticks"]) for row in valid}
    frontier = [name for name, point in points.items() if not any(
        all(left <= right for left, right in zip(other, point)) and
        any(left < right for left, right in zip(other, point))
        for candidate, other in points.items() if candidate != name)]
    by_map = defaultdict(list)
    for row in valid:
        by_map[row["map_digest"]].append(row)
    conditional = {key: min((active_outstanding(row) // 2 if row["workload"] == Workload.MIXED_1_1
                             else active_outstanding(row)) for row in group
                            if row["bandwidth_Bps"] >= .95 * max(item["bandwidth_Bps"] for item in group))
                   for key, group in by_map.items()}
    if any(row["workload"] == Workload.MIXED_1_1 and row["n_read"] != row["n_write"] for row in valid):
        conditional = {}
    budget_results = {}
    for budget in budgets:
        within = [row for row in valid if row["router_buffer_bytes"] <= budget]
        best = min(within, key=lambda row: (-row["bandwidth_Bps"], row["router_buffer_bytes"], row["case_id"])) if within else None
        budget_results[str(budget)] = {"budget_bytes": budget,
                                      "case_id": best["case_id"] if best else None,
                                      "actual_bytes": best["router_buffer_bytes"] if best else None,
                                      "unused_bytes": budget - best["router_buffer_bytes"] if best else None,
                                      "reaches_joint_f95": bool(best and best["bandwidth_Bps"] >= .95 * reference)}
    return {"B_ref": reference, "F95": [row["case_id"] for row in near], "pareto": frontier,
            "peak": min(valid, key=lambda row: (-row["bandwidth_Bps"], *resource_key(row)))["case_id"],
            "resource_recommendation": min(near, key=resource_key)["case_id"],
            "outstanding_recommendation": min(near, key=outstanding_key)["case_id"],
            "conditional_N95": conditional, "budgets": budget_results}


def common_hardware(rows):
    group_keys = {tuple(row[field] for field in COMMON_FIELDS) for row in rows}
    if len(group_keys) > 1:
        raise ValueError("common hardware requires one topology/profile/distribution")
    valid = sorted((row for row in rows if eligible(row)), key=lambda row: row["case_id"])
    references = {workload.value: max((row["bandwidth_Bps"] for row in valid
                                     if row["workload"] == workload), default=0)
                  for workload in Workload}
    hardware = defaultdict(dict)
    for row in valid:
        key = (row["map_digest"], row["n_read"], row["n_write"])
        previous = hardware[key].get(row["workload"])
        if previous is None or row["bandwidth_Bps"] < previous["bandwidth_Bps"]:
            hardware[key][row["workload"]] = row
    points = []
    for key, workloads in hardware.items():
        if set(workloads) != set(references):
            continue
        if len({row["router_buffer_bytes"] for row in workloads.values()}) != 1:
            raise ValueError("identical map digest has different buffer costs")
        point = {"map_digest": key[0], "n_read": key[1], "n_write": key[2],
                 "router_buffer_bytes": next(iter(workloads.values()))["router_buffer_bytes"],
                 "Q": min(row["bandwidth_Bps"] / references[workload] for workload, row in workloads.items()),
                 "p99_ticks": max(row["p99_ticks"] for row in workloads.values()),
                 "cases": {workload: row["case_id"] for workload, row in workloads.items()}}
        points.append(point)
    near = [row for row in points if row["Q"] >= .95]
    key = lambda row: (row["router_buffer_bytes"], row["n_read"] + row["n_write"], row["p99_ticks"], row["map_digest"])
    return {"references": references, "candidates": points,
            "recommendation": min(near, key=key) if near else None,
            "compromise": min(points, key=lambda row: (-row["Q"], *key(row))) if points else None}


def measure_runtime(plan, actual, *, roi=None, ticks_per_second=10**12,
                    command_issue_ticks=None, drain_verified=False,
                    network_verified=False, full_timing_verified=False, tolerance=.02,
                    subwindows=3, mixed_read_fraction=.5, mixed_fraction_tolerance=.02):
    if ticks_per_second <= 0 or not 0 <= tolerance <= .02 or type(subwindows) is not int or subwindows < 3 or \
            mixed_read_fraction != .5 or not 0 <= mixed_fraction_tolerance <= .02:
        raise ValueError("invalid measurement units or tolerance")
    expected = {}
    for tile in plan["tiles"]:
        channel = "AR" if tile["direction"] == "read" else "AW"
        for burst in tile["bursts"]:
            key = (tile["core_id"], channel, burst["beat_base"])
            if key in expected:
                raise ValueError("workload burst identity is not unique")
            expected[key] = (tile, burst)
    observations = {}
    for burst in actual.get("burst_timings", []):
        if any(type(burst[field]) is not int or burst[field] < 0 for field in (
                "ar_aw_tick", "response_tick", "commit_tick")):
            raise ValueError("burst ticks must be nonnegative integers")
        key = (burst["core_id"], burst["channel"], burst["address"])
        if key not in expected or key in observations:
            raise ValueError("unexpected or duplicate runtime burst")
        tile, want = expected[key]
        if burst["beats"] != want["beats"] or burst["beat_bytes"] != 32:
            raise ValueError("runtime burst shape disagrees with workload")
        if not 0 <= burst["ar_aw_tick"] <= burst["response_tick"]:
            raise ValueError("invalid AXI transaction timing")
        if tile["direction"] == "read" and burst["commit_tick"] < burst["response_tick"]:
            raise ValueError("read SRAM commit precedes RLAST")
        observations[key] = burst
    if observations.keys() != expected.keys():
        raise ValueError("runtime bursts do not exactly cover workload")
    events = defaultdict(list)
    hbm_events = defaultdict(list)
    hbm_intervals = defaultdict(list)
    read_pending_sram = defaultdict(list)
    tile_last_complete = defaultdict(int)
    intervals = defaultdict(list)
    burst_events = defaultdict(list)
    core_burst_events = defaultdict(list)
    hbm_burst_events = defaultdict(list)
    for key, row in observations.items():
        tile, burst = expected[key]
        direction = tile["direction"]
        core = tile["core_id"]
        complete = row["commit_tick"] if direction == "read" else row["response_tick"]
        tile_key = (core, tile["descriptor_id"])
        tile_last_complete[tile_key] = max(tile_last_complete[tile_key], complete)
        events[core, direction].append((complete, burst["useful_bytes"]))
        hbm_events[tile["target_index"], direction].append((complete, burst["useful_bytes"]))
        intervals[core, direction].append((row["ar_aw_tick"], row["response_tick"]))
        hbm_intervals[tile["target_index"], direction].append((row["ar_aw_tick"], row["response_tick"]))
        if direction == "read":
            read_pending_sram[core].append((row["response_tick"], row["commit_tick"]))
        burst_events[direction].append((row["response_tick"], 1))
        core_burst_events[core, direction].append((row["response_tick"], 1))
        hbm_burst_events[tile["target_index"], direction].append((row["response_tick"], 1))
    first = min(start for group in intervals.values() for start, stop in group)
    makespan_origin = "first_axi_address_incomplete_issue_evidence"
    issue_ticks = command_issue_ticks or {}
    if all(tile["command_id"] in issue_ticks for tile in plan["tiles"]):
        first = min(issue_ticks[tile["command_id"]] for tile in plan["tiles"])
        makespan_origin = "first_dma_command_issue"
    final = max(tick for group in events.values() for tick, value in group)
    done = {}
    for row in actual.get("dma_timings", []):
        if type(row["done_tick"]) is not int or row["done_tick"] < 0:
            raise ValueError("descriptor ticks must be nonnegative integers")
        key = (row["core_id"], row["descriptor_id"])
        if key in done:
            raise ValueError("duplicate descriptor completion")
        done[key] = row["done_tick"]
    for tile in plan["tiles"]:
        key = (tile["core_id"], tile["descriptor_id"])
        if key not in done:
            raise ValueError("missing tile completion")
        if done[key] < tile_last_complete[key]:
            raise ValueError("tile completion precedes its last burst completion")
        final = max(final, done[key])
    if final <= first:
        raise ValueError("nonpositive workload makespan")
    common_begin = max(min(start for start, stop in group) for group in intervals.values())
    common_end = min(max(start for start, stop in group) for group in intervals.values())
    if roi is None:
        warmup = (common_end - common_begin) // 5
        roi = (common_begin + warmup, common_end)
    begin, end = roi
    if not common_begin <= begin < end <= common_end:
        raise ValueError("ROI is outside common active issue interval")
    if end - begin < subwindows:
        raise ValueError("ROI cannot contain the declared positive subwindows")
    seconds = (end - begin) / ticks_per_second
    per_core = []
    per_hbm = []
    directional_bytes = {direction: sum(event_window(group, begin, end)
                                        for (core, kind), group in events.items() if kind == direction)
                         for direction in ("read", "write")}
    boundaries = [begin + (end - begin) * index // subwindows for index in range(subwindows + 1)]
    window_rates = []
    for left, right in zip(boundaries, boundaries[1:]):
        total = sum(event_window(group, left, right) for group in events.values())
        window_rates.append(total * ticks_per_second / (right - left))
    average_rate = sum(directional_bytes.values()) / seconds
    stable = average_rate > 0 and all(abs(rate / average_rate - 1) <= tolerance for rate in window_rates)
    share = directional_bytes["read"] / sum(directional_bytes.values()) if sum(directional_bytes.values()) else None
    ratio_ok = plan["spec"]["workload"] != Workload.MIXED_1_1 or (
        share is not None and abs(share - mixed_read_fraction) <= mixed_fraction_tolerance)
    tile_latencies = []
    tile_completions = defaultdict(list)
    hbm_tile_completions = defaultdict(list)
    core_tile_latencies = defaultdict(list)
    hbm_tile_latencies = defaultdict(list)
    for tile in plan["tiles"]:
        completion = done[tile["core_id"], tile["descriptor_id"]]
        tile_completions[tile["core_id"]].append(completion)
        hbm_tile_completions[tile["target_index"]].append(completion)
        issue = issue_ticks.get(tile["command_id"])
        if issue is not None:
            if type(issue) is not int or issue < 0 or completion < issue:
                raise ValueError("tile completion precedes issue or issue tick is invalid")
            if begin <= issue < end:
                latency = completion - issue
                tile_latencies.append(latency)
                core_tile_latencies[tile["core_id"]].append(latency)
                hbm_tile_latencies[tile["target_index"]].append(latency)
    for core in plan["spec"]["active_cores"]:
        statistics = scope_statistics(*[{direction: source[core, direction] for direction in ("read", "write")}
                                        for source in (events, core_burst_events, intervals)],
                                      tile_completions[core], core_tile_latencies[core], begin, end, ticks_per_second)
        per_core.append({"core_id": core, **statistics,
                         "read_pending_sram": outstanding_window(read_pending_sram[core], begin, end)})
    for target in sorted({tile["target_index"] for tile in plan["tiles"]}):
        statistics = scope_statistics(*[{direction: source[target, direction] for direction in ("read", "write")}
                                        for source in (hbm_events, hbm_burst_events, hbm_intervals)],
                                      hbm_tile_completions[target], hbm_tile_latencies[target], begin, end, ticks_per_second)
        per_hbm.append({"target_index": target, **statistics})
    throughput = [row["bandwidth_Bps"] for row in per_core]
    squares = sum(value * value for value in throughput)
    cohort = [stop - start for group in intervals.values() for start, stop in group if begin <= start < end]
    tiles_in_roi = sum(begin <= tick < end for group in tile_completions.values() for tick in group)
    reasons = []
    if not stable:
        reasons.append("unstable_subwindows")
    if not ratio_ok:
        reasons.append("ratio_unconverged")
    if not drain_verified:
        reasons.append("drain_unverified")
    if not network_verified:
        reasons.append("network_oracle_unverified")
    if not full_timing_verified:
        reasons.append("full_timing_unverified")
    if not tile_latencies:
        reasons.append("tile_issue_cohort_missing")
    if any(tile["command_id"] not in issue_ticks for tile in plan["tiles"]):
        reasons.append("tile_issue_evidence_incomplete")
    if actual.get("watchdog_fired"):
        reasons.append("watchdog")
    return {"status": "valid" if not reasons else "unconverged", "invalid_reasons": reasons,
            "full_timing": full_timing_verified, "drained": drain_verified, "stable": stable and ratio_ok,
            "roi_begin_tick": begin, "roi_end_tick": end,
            "roi_rule": "common_issue_interval_after_20_percent_warmup",
            "read_Bps": directional_bytes["read"] / seconds,
            "write_Bps": directional_bytes["write"] / seconds,
            "read_bytes": directional_bytes["read"], "write_bytes": directional_bytes["write"],
            "bandwidth_Bps": sum(directional_bytes.values()) / seconds,
            "read_share": share, "subwindow_Bps": window_rates,
            "read_bursts_per_second": event_window(burst_events["read"], begin, end) / seconds,
            "write_bursts_per_second": event_window(burst_events["write"], begin, end) / seconds,
            "tiles_per_second": tiles_in_roi / seconds,
            "transaction_latency_ticks": latency_summary(cohort),
            "tile_latency_ticks": latency_summary(tile_latencies),
            "p99_ticks": quantile(cohort, .99) if cohort else None,
            "makespan_ticks": final - first,
            "makespan_origin": makespan_origin,
            "makespan_Bps": sum(tile["useful_bytes"] for tile in plan["tiles"]) * ticks_per_second / (final - first),
            "per_core": per_core, "per_hbm_core_completion": per_hbm,
            "fairness": sum(throughput) ** 2 / (len(throughput) * squares) if squares else None,
            "core_min_Bps": min(throughput), "core_mean_Bps": sum(throughput) / len(throughput),
            "core_max_Bps": max(throughput)}
