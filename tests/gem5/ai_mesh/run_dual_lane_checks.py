import argparse
import json
import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CONFIG = REPO / "configs/example/ai_mesh/run_mesh_dma_garnet.py"
FIELDS = {
    "DL_SELECT": "cycle router ingress lane0 lane1 vnet packet flit count0 count1 selected accepted",
    "DL_SEND": "cycle router input lane outport direction vnet vc packet flit",
    "DL_MERGE": "cycle router outport req0 req1 selected winner",
    "DL_SNAPSHOT": "tick",
    "DL_SNAPSHOT_END": "tick",
    "DL_CAPACITY": "link vc vnet kind receiver port direction depth",
    "DL_BUFFER": "router input ingress lane vc vnet depth occupancy high_water enqueued dequeued",
    "DL_CREDIT": "link vc initial sent returned current",
}
DIRECTIONS = {"East", "West", "North", "South"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def trace_events(log):
    events = []
    for index, line in enumerate(log.splitlines()):
        match = re.search(r"\b(DL_\w+)\b(.*)", line)
        if not match:
            continue
        kind, payload = match.groups()
        require(kind in FIELDS, f"unknown trace event {kind}")
        pairs = [token.split("=", 1) for token in payload.split()]
        require(all(len(pair) == 2 for pair in pairs), f"malformed {kind}")
        values = dict(pairs)
        require(len(values) == len(pairs) and set(values) == set(FIELDS[kind].split()),
                f"missing or duplicated fields in {kind}")
        events.append({"event": kind, "index": index, **{
            key: value if key == "direction" else int(value)
            for key, value in values.items()}})
    return events


def verify_trace(log):
    events = trace_events(log)
    selections = [row for row in events if row["event"] == "DL_SELECT"]
    sends = [row for row in events if row["event"] == "DL_SEND"]
    merges = [row for row in events if row["event"] == "DL_MERGE"]
    require(selections and sends and merges, "selector/send/merge trace is missing")
    accepted, pending, pairs, decisions = {}, {}, {}, set()
    rejects = 0
    for row in selections:
        key = row["packet"], row["flit"]
        port = row["router"], row["ingress"]
        decision = port + (row["cycle"],)
        require(decision not in decisions, "multiple decisions on one P input in a cycle")
        decisions.add(decision)
        require(row["lane0"] == row["ingress"] and row["lane0"] != row["lane1"], "invalid P input pair")
        pair = row["lane0"], row["lane1"]
        require(pairs.setdefault(port, pair) == pair, "P input pair changed")
        require(min(row["count0"], row["count1"]) >= 0, "negative occupancy")
        require(row["selected"] == int(row["count0"] > row["count1"]), "incorrect count selection")
        require(row["accepted"] in (0, 1), "invalid acceptance")
        require(key not in accepted, "flit revisited after acceptance")
        if port in pending:
            previous = pending[port]
            require(key == (previous["packet"], previous["flit"]) and row["cycle"] > previous["cycle"],
                    "rejected P input flit was bypassed or not retried later")
        if row["accepted"]:
            accepted[key] = row
            pending.pop(port, None)
        else:
            pending[port] = row
            rejects += 1
    require(not pending, "rejected flit never accepted")
    paths = defaultdict(list)
    output_cycles, input_cycles, direction_cycles = {}, set(), defaultdict(set)
    input_lanes = {}
    for row in sends:
        key = row["packet"], row["flit"]
        paths[key].append(row)
        output = row["router"], row["outport"], row["cycle"]
        input_cycle = row["router"], row["input"], row["cycle"]
        require(output not in output_cycles and input_cycle not in input_cycles, "duplicate physical port transfer")
        output_cycles[output] = row
        input_cycles.add(input_cycle)
        identity = row["router"], row["input"]
        require(row["lane"] in (0, 1) and input_lanes.setdefault(identity, row["lane"]) == row["lane"],
                "input lane changed")
        direction = row["direction"]
        logical = direction.removesuffix("_ext")
        require(logical in DIRECTIONS | {"Local"}, "unknown physical direction")
        if logical != "Local":
            require(row["lane"] == int(direction.endswith("_ext")), "send crossed lanes")
            direction_cycles[row["router"], row["cycle"], logical].add(row["lane"])
    require(paths.keys() == accepted.keys(), "accepted and transmitted flit sets differ")
    for key, path in paths.items():
        row = accepted[key]
        first = path[0]
        require(first["router"] == row["router"] and first["input"] == row[f'lane{row["selected"]}'] and
                first["cycle"] >= row["cycle"] and first["index"] > row["index"],
                "selection does not match actual source input")
        require(all(send["lane"] == row["selected"] and send["vnet"] == row["vnet"] for send in path),
                "flit lane/vnet changed along route")
        require(path[-1]["direction"] == "Local" and all(send["direction"] != "Local" for send in path[:-1]),
                "packet did not terminate exactly once at Local")
    priorities, seen_merges = {}, set()
    contention = 0
    for row in merges:
        port = row["router"], row["outport"]
        key = port + (row["cycle"],)
        ready = [row["req0"] >= 0, row["req1"] >= 0]
        require(any(ready) and key not in seen_merges, "empty or repeated merge grant")
        seen_merges.add(key)
        priority = priorities.get(port, 0)
        selected = priority if ready[priority] else 1 - priority
        require(row["selected"] == selected and row["winner"] == row[f"req{selected}"],
                "Local merge violated lane round robin")
        send = output_cycles.get(key)
        require(send is not None and send["direction"] == "Local" and
                send["input"] == row["winner"] and send["lane"] == selected,
                "merge grant does not match actual send")
        for lane in (0, 1):
            if ready[lane]:
                require(input_lanes.get((row["router"], row[f"req{lane}"])) == lane,
                        "merge candidate belongs to wrong lane")
        priorities[port] = 1 - selected
        contention += all(ready)
    require(seen_merges == {key for key, send in output_cycles.items() if send["direction"] == "Local"},
            "Local transfer is missing its merge grant")
    parallel = sum(lanes == {0, 1} for lanes in direction_cycles.values())
    require(parallel > 0, "no same-direction parallel transfer observed")
    return {"selector_decisions": len(selections), "selector_accepts": len(accepted),
            "selector_rejects_observed": rejects, "same_direction_dual_ejections": parallel,
            "local_merges": len(merges), "contended_local_merges": contention,
            "lane1_injections": sum(row["selected"] for row in accepted.values())}


def verify_resources(log, dual):
    snapshot, complete, tick = None, None, None
    for row in trace_events(log):
        kind = row["event"]
        if kind == "DL_SNAPSHOT":
            require(snapshot is None, "nested resource snapshot")
            snapshot, tick = [], row["tick"]
        elif kind == "DL_SNAPSHOT_END":
            require(snapshot is not None and tick == row["tick"], "invalid resource snapshot boundary")
            complete, snapshot = snapshot, None
        elif kind in {"DL_BUFFER", "DL_CAPACITY", "DL_CREDIT"}:
            require(snapshot is not None, "resource record outside snapshot")
            snapshot.append(row)
    require(complete and snapshot is None, "complete resource snapshot is missing")
    capacities, receivers, credits, banks = {}, {}, {}, {}
    for row in complete:
        if row["event"] == "DL_CAPACITY":
            key = row["link"], row["vc"]
            receiver = row["kind"], row["receiver"], row["port"], row["vc"]
            require(key not in capacities and receiver not in receivers, "duplicated physical receiver")
            capacities[key], receivers[receiver] = row, row
        elif row["event"] == "DL_CREDIT":
            key = row["link"], row["vc"]
            require(key not in credits, "duplicate credit ledger")
            credits[key] = row
        elif row["event"] == "DL_BUFFER":
            key = row["router"], row["input"], row["vc"]
            require(key not in banks, "duplicate input bank")
            banks[key] = row
    require(capacities and banks and capacities.keys() == credits.keys(), "incomplete capacity/credit inventory")
    ingress_banks = defaultdict(list)
    for row in banks.values():
        receiver = 1, row["router"], row["ingress"], row["vc"]
        require(receiver in receivers, "input bank has no physical receiver")
        capacity = receivers[receiver]
        require(row["depth"] == capacity["depth"] and row["vnet"] == capacity["vnet"], "bank capacity mismatch")
        require(row["occupancy"] == 0 and row["enqueued"] == row["dequeued"] and
                0 <= row["high_water"] <= row["depth"], "bank did not drain within capacity")
        ingress_banks[receiver].append(row)
    for receiver, capacity in receivers.items():
        if receiver[0] != 1:
            continue
        rows = ingress_banks[receiver]
        expected = {0, 1} if dual and capacity["direction"] == "Local" else {int(capacity["direction"].endswith("_ext"))}
        require(len(rows) == len(expected) and {row["lane"] for row in rows} == expected, "missing or duplicated lane bank")
        for row in rows:
            if capacity["direction"] != "Local" or row["lane"] == 0:
                require(row["input"] == row["ingress"], "physical input identity mismatch")
        credit = credits[capacity["link"], capacity["vc"]]
        require(sum(row["enqueued"] for row in rows) == credit["sent"] and
                sum(row["dequeued"] for row in rows) == credit["returned"], "shared ingress credit accounting mismatch")
    for key, row in credits.items():
        require(row["initial"] == row["current"] == capacities[key]["depth"] and row["sent"] == row["returned"],
                "credit ledger failed conservation/restoration")
    return {"input_buffer_slots": sum(row["depth"] for row in banks.values()),
            "input_vcs": len(banks), "physical_credit_vcs": len(credits),
            "local_injected_flits": sum(
                row["enqueued"] for receiver, rows in ingress_banks.items()
                if receivers[receiver]["direction"] == "Local" for row in rows)}


def compare_configs(single, dual, roots=(None, None)):
    def normalize(value, root):
        if isinstance(value, list):
            return [normalize(item, root) for item in value]
        if isinstance(value, str) and root and value.startswith(str(root) + "/"):
            return "${RUN}/" + value[len(str(root)) + 1:]
        if not isinstance(value, dict):
            return value
        kind = value.get("type")
        ignored = {"dual_lane"} if kind in {"GarnetNetwork", "GarnetRouter"} else set()
        if kind == "GarnetNetwork":
            ignored.add("int_links")
        return {key: normalize(item, root) for key, item in value.items() if key not in ignored}
    require(normalize(single, roots[0]) == normalize(dual, roots[1]),
            "single/dual effective configurations differ")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", type=Path, default=REPO / "build/AXI_MESH/gem5.opt")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--case", default="load_saturation_contiguous")
    parser.add_argument("--program-dir", type=Path, required=True)
    parser.add_argument("--buffers", default="1,2,1,1,2")
    args = parser.parse_args()
    workdir = args.workdir.resolve()
    require(not workdir.exists() or not any(workdir.iterdir()), "workdir must be new or empty")
    workdir.mkdir(parents=True, exist_ok=True)
    common = [str(CONFIG), "--case", args.case, "--mesh-program-dir", str(args.program_dir.resolve()),
              "--axi-max-sim-ticks", "200000000", "--watchdog-ticks", "100000000",
              "--link-width-bits", "256", "--axi-data-header-sideband",
              "--garnet-buffers-per-vnet", args.buffers]
    results = {}
    for mode in ("dual", "single"):
        directory = workdir / mode
        directory.mkdir()
        command = [str(args.gem5.resolve()), f"--outdir={directory / 'm5out'}",
                   "--debug-flags=GarnetDualLane", *common]
        if mode == "dual":
            command.append("--garnet-dual-lane")
        env = os.environ.copy()
        env.update(AI_MESH_ARTIFACT_DIR=str(directory), PYTHONPATH=str(REPO / "util/mesh_ir"))
        (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        with (directory / "run.log").open("w") as handle:
            completed = subprocess.run(command, cwd=REPO, stdout=handle, stderr=subprocess.STDOUT,
                                       env=env, text=True, timeout=180)
        log = (directory / "run.log").read_text()
        require(completed.returncode == 0 and "MESH_DMA_GARNET_PASS" in log, f"{mode} simulation failed")
        actual = json.loads((directory / "actual_result.json").read_text())
        quiet = actual["garnet"]
        require(quiet["quiescent"] == 1 and quiet["credit_deficit"] == quiet["credit_link_pending_credits"] == 0,
                f"{mode} network did not drain")
        exit_match = re.search(r"Exiting @ tick (\d+)", log)
        require(exit_match is not None, "missing exit tick")
        results[mode] = {"exit_tick": int(exit_match.group(1)), **verify_resources(log, mode == "dual")}
        if mode == "dual":
            results[mode].update(verify_trace(log))
            require(results[mode]["selector_accepts"] == results[mode]["local_injected_flits"],
                    "selector trace does not cover the physical ingress ledger")
    configs = [json.loads((workdir / mode / "m5out/config.json").read_text()) for mode in ("single", "dual")]
    compare_configs(*configs, roots=(workdir / "single", workdir / "dual"))
    for filename in ("hbm_seed.txt", "hbm_verify.txt"):
        require((workdir / "single" / filename).read_bytes() == (workdir / "dual" / filename).read_bytes(),
                f"single/dual {filename} contents differ")
    results["same_effective_configuration"] = True
    (workdir / "checks.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    print("DUAL_LANE_CHECKS_PASS")


if __name__ == "__main__":
    main()
