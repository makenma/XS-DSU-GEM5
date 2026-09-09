import math
from collections import defaultdict, deque

from ..acceptance import canonical_digest
from .config import BufferMap, CHANNELS, Workload


INITIAL_WINDOWS = (1, 2, 4, 8, 16, 32, 64, 128)
INITIAL_VECTORS = ((1, 1, 1, 1, 1), (2, 4, 2, 2, 4), (4, 8, 4, 4, 8), (8, 16, 8, 8, 16))
STAGE_BUDGETS = {"A": 48, "B": 48, "C": 24, "D": 12, "E": 12, "F": 36}


class FamilyCoverage:
    def __init__(self, families=()):
        self.requests = {row["family_id"]: {key: row[key] for key in (
            "family_id", "stage", "topology", "workload", "family", "unavailable_reason")} for row in families}

    def request(self, stage, topology, workload, family):
        key = "/".join((stage, topology, workload, family))
        self.requests.setdefault(key, {"family_id": key, "stage": stage, "topology": topology,
                                       "workload": workload, "family": family, "unavailable_reason": None})
        return key

    def unavailable(self, key, reason):
        self.requests[key]["unavailable_reason"] = reason

    def report(self, history, measurements=None):
        candidates = {row["case_id"]: row for row in history}
        measured = None if measurements is None else {row["case_id"]: row for row in measurements}
        families = []
        for key, request in sorted(self.requests.items()):
            selected = sorted((row for row in history if key in row.get("family_ids", ())), key=lambda row: row["case_id"])
            outcomes = defaultdict(int)
            reasons = defaultdict(int)
            evidence = set()
            for candidate in selected:
                target = candidate
                visited = set()
                while target["status"] == "duplicate":
                    if target["case_id"] in visited or target.get("duplicate_of") not in candidates:
                        raise ValueError("coverage duplicate reference is missing or cyclic")
                    visited.add(target["case_id"])
                    target = candidates[target["duplicate_of"]]
                status = target["status"]
                if status in ("valid", "unconverged", "failed"):
                    evidence.add(target["case_id"])
                    if measured is not None:
                        status = measured.get(target["case_id"], {}).get("status", "missing_measurement")
                outcomes[status] += 1
                if target.get("budget_reason"):
                    reasons[target["budget_reason"]] += 1
            status = ("unavailable" if request["unavailable_reason"] else "not_generated") if not selected else (
                next(iter(outcomes)) if len(outcomes) == 1 else "partial")
            families.append({**request, "status": status, "candidate_ids": [row["case_id"] for row in selected],
                             "evidence_case_ids": sorted(evidence), "outcomes": dict(sorted(outcomes.items())),
                             "not_run_reasons": dict(sorted(reasons.items())), "has_valid_evidence": outcomes.get("valid", 0) > 0})
        return {"schema": "ai_mesh_experiment_family_coverage_v1", "families": families,
                "status_source": "verified_measurements" if measured is not None else "candidate_history"}


def configuration_identity(candidate):
    if candidate.get("map"):
        capacities = BufferMap(tuple(tuple(row) for row in candidate["map"]["entries"]))
        depths = capacities.uniform_depths()
        buffer = {"uniform": depths} if depths is not None else {"entries": capacities.entries}
    else:
        buffer = {"uniform": tuple(candidate["depths"])}
    return canonical_digest({"buffer": buffer, **{key: candidate.get(key) for key in (
        "topology", "workload", "n", "distribution", "hotspot_target", "bytes_per_core", "active_cores", "profile")}})


def ranked_candidates(rows):
    return sorted(rows, key=lambda row: (-row["bandwidth_Bps"], row["router_buffer_bytes"],
                                         row["n_read"] + row["n_write"], row["p99_ticks"], row["case_id"]))


def long_validation_points(rows, recommendations):
    points = {row["case_id"]: row for row in rows}
    selected = {}
    for role, name in (("recommendation", recommendations["resource_recommendation"]),
                       ("peak", recommendations["peak"])):
        row = points[name]
        hardware = row["map_digest"], row["n_read"], row["n_write"]
        selected.setdefault(hardware, {"point": row, "validation_roles": []})["validation_roles"].append(role)
    anchor = points[recommendations["resource_recommendation"]]
    neighbors = sorted((row for row in rows if (row["map_digest"], row["n_read"], row["n_write"]) not in selected),
                       key=lambda row: (row["map_digest"] != anchor["map_digest"],
                                        abs(row["n_read"] - anchor["n_read"]) + abs(row["n_write"] - anchor["n_write"]),
                                        abs(row["router_buffer_bytes"] - anchor["router_buffer_bytes"]), row["case_id"]))
    result = list(selected.values())
    if neighbors:
        result.append({"point": neighbors[0], "validation_roles": ["neighbor"], "neighbor_of": anchor["case_id"]})
    return result


def fair_candidates(candidates, fields=("topology", "workload")):
    groups = defaultdict(deque)
    for row in candidates:
        groups[tuple(str(row.get(field, "")) for field in fields)].append(row)
    result = []
    while groups:
        for key in sorted(list(groups)):
            result.append(groups[key].popleft())
            if not groups[key]:
                del groups[key]
    return result


def active_channels(workload):
    return (3, 4) if workload == Workload.LOAD_ONLY else (
        (0, 1, 2) if workload == Workload.STORE_ONLY else tuple(range(5)))


def uniform_vectors(workload, center, packet_depths=None):
    candidates = list(INITIAL_VECTORS)
    if packet_depths is not None:
        candidates.append(tuple(packet_depths))
        data_channel = 4 if workload != Workload.STORE_ONLY else 1
        changed = list(center)
        changed[data_channel] = packet_depths[data_channel]
        if tuple(changed) not in candidates:
            candidates.append(tuple(changed))
    for channel in active_channels(workload):
        for depth in (max(1, center[channel] // 2), min(32, center[channel] * 2)):
            changed = list(center)
            changed[channel] = depth
            if tuple(changed) != tuple(center) and tuple(changed) not in candidates:
                candidates.append(tuple(changed))
    return candidates


def coordinate_candidates(baseline, groups, budget_bytes, channels=tuple(range(5)), max_depth=32,
                          *, flit_bytes=16, vcs=4):
    result = []
    for name, ports in sorted(groups.items()):
        for channel in channels:
            selected = [row for row in baseline.entries if row[:2] in ports and row[2] == channel]
            for delta in (-1, 1):
                if not selected or any(not 1 <= row[3] + delta <= max_depth for row in selected):
                    continue
                changed = baseline.replace({row[:3]: row[3] + delta for row in selected})
                if changed.storage_bytes(flit_bytes, vcs) <= budget_bytes:
                    result.append({"map": changed, "reason": f"coordinate:{name}:{CHANNELS[channel]}:{delta:+d}"})
    return result


def equal_capacity_exchanges(baseline, groups, channels=tuple(range(5)), max_depth=32):
    result = []
    named = sorted(groups.items())
    for index, (left_name, left_ports) in enumerate(named):
        for right_name, right_ports in named[index + 1:]:
            if left_ports & right_ports:
                raise ValueError("exchange groups must be disjoint")
            for channel in channels:
                left = [row for row in baseline.entries if row[:2] in left_ports and row[2] == channel]
                right = [row for row in baseline.entries if row[:2] in right_ports and row[2] == channel]
                if not left or not right:
                    continue
                divisor = math.gcd(len(left), len(right))
                left_delta, right_delta = len(right) // divisor, len(left) // divisor
                for sign in (-1, 1):
                    changes = {row[:3]: row[3] + sign * left_delta for row in left}
                    changes.update({row[:3]: row[3] - sign * right_delta for row in right})
                    if any(not 1 <= value <= max_depth for value in changes.values()):
                        continue
                    changed = baseline.replace(changes)
                    if changed.slots() != baseline.slots():
                        raise ValueError("equal-capacity exchange changed storage cost")
                    result.append({"map": changed,
                                   "reason": f"equal_capacity:{left_name}:{right_name}:{CHANNELS[channel]}:{sign:+d}"})
    return result
