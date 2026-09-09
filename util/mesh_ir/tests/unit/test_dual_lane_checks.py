from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("dual_lane_checks", REPO / "tests/gem5/ai_mesh/run_dual_lane_checks.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
verify_trace = checks.verify_trace
verify_resources = checks.verify_resources
compare_configs = checks.compare_configs


TRACE = """DL_SELECT cycle=0 router=0 ingress=0 lane0=0 lane1=1 vnet=4 packet=0 flit=0 count0=0 count1=0 selected=0 accepted=1
DL_SELECT cycle=1 router=0 ingress=0 lane0=0 lane1=1 vnet=4 packet=1 flit=0 count0=1 count1=0 selected=1 accepted=1
DL_SEND cycle=2 router=0 input=0 lane=0 outport=0 direction=East vnet=4 vc=0 packet=0 flit=0
DL_SEND cycle=2 router=0 input=1 lane=1 outport=1 direction=East_ext vnet=4 vc=0 packet=1 flit=0
DL_MERGE cycle=3 router=1 outport=0 req0=2 req1=3 selected=0 winner=2
DL_SEND cycle=3 router=1 input=2 lane=0 outport=0 direction=Local vnet=4 vc=0 packet=0 flit=0
DL_MERGE cycle=4 router=1 outport=0 req0=-1 req1=3 selected=1 winner=3
DL_SEND cycle=4 router=1 input=3 lane=1 outport=0 direction=Local vnet=4 vc=0 packet=1 flit=0
"""


def test_trace_links_selection_to_actual_lane_and_merge():
    result = verify_trace(TRACE)
    assert result["same_direction_dual_ejections"] == 1
    assert result["contended_local_merges"] == 1
    assert result["selector_accepts"] == 2


@pytest.mark.parametrize("change", (
    "no_selectors", "missing_accept", "wrong_count", "wrong_lane",
    "duplicate_accept", "two_accepts_per_cycle", "reject_after_accept",
    "missing_send", "bad_merge", "missing_merge", "malformed",
))
def test_trace_rejects_false_acceptance(change):
    lines = TRACE.splitlines()
    if change == "no_selectors":
        lines = lines[2:]
    elif change == "missing_accept":
        lines = lines[1:]
    elif change == "wrong_count":
        lines[1] = lines[1].replace("count0=1", "count0=0")
    elif change == "wrong_lane":
        lines[3] = lines[3].replace("lane=1", "lane=0")
    elif change == "duplicate_accept":
        lines.insert(2, lines[1].replace("cycle=1", "cycle=2"))
    elif change == "two_accepts_per_cycle":
        lines[1] = lines[1].replace("cycle=1", "cycle=0")
    elif change == "reject_after_accept":
        lines.append(lines[1].replace("cycle=1", "cycle=5").replace("accepted=1", "accepted=0"))
    elif change == "missing_send":
        lines.pop(3)
    elif change == "bad_merge":
        lines[6] = lines[6].replace("req0=-1", "req0=2").replace("selected=1 winner=3", "selected=0 winner=2")
    elif change == "missing_merge":
        lines.pop(4)
    else:
        lines[0] = lines[0].replace("count0=0 ", "")
    with pytest.raises(ValueError):
        verify_trace("\n".join(lines))


def test_retry_must_precede_acceptance():
    reject = TRACE.splitlines()[0].replace("accepted=1", "accepted=0")
    accepted = TRACE.replace("cycle=0 router=0 ingress", "cycle=1 router=0 ingress", 1)
    accepted = accepted.replace("cycle=1 router=0 ingress=0 lane0=0 lane1=1 vnet=4 packet=1", "cycle=2 router=0 ingress=0 lane0=0 lane1=1 vnet=4 packet=1")
    assert verify_trace(reject + "\n" + accepted)["selector_rejects_observed"] == 1


RESOURCE = """DL_SNAPSHOT tick=10
DL_CAPACITY link=0 vc=0 vnet=0 kind=1 receiver=0 port=0 direction=Local depth=2
DL_BUFFER router=0 input=0 ingress=0 lane=0 vc=0 vnet=0 depth=2 occupancy=0 high_water=1 enqueued=2 dequeued=2
DL_BUFFER router=0 input=1 ingress=0 lane=1 vc=0 vnet=0 depth=2 occupancy=0 high_water=1 enqueued=3 dequeued=3
DL_CREDIT link=0 vc=0 initial=2 sent=5 returned=5 current=2
DL_SNAPSHOT_END tick=10
"""


def test_resource_inventory_counts_both_local_banks():
    result = verify_resources(RESOURCE, dual=True)
    assert result["input_buffer_slots"] == 4
    assert result["physical_credit_vcs"] == 1
    assert result["local_injected_flits"] == 5


@pytest.mark.parametrize("change", ("duplicate_receiver", "missing_bank", "bad_ledger", "bad_enqueue", "incomplete"))
def test_resource_checker_rejects_broken_ownership(change):
    log = RESOURCE
    if change == "duplicate_receiver":
        log = log.replace("DL_BUFFER router=0 input=0", RESOURCE.splitlines()[1].replace("link=0", "link=1") + "\nDL_BUFFER router=0 input=0")
    elif change == "missing_bank":
        log = "\n".join(line for line in log.splitlines() if "input=1" not in line)
    elif change == "bad_ledger":
        log = log.replace("returned=5", "returned=4")
    elif change == "bad_enqueue":
        log = log.replace("enqueued=3 dequeued=3", "enqueued=2 dequeued=2")
    else:
        log = log.replace("DL_SNAPSHOT_END tick=10", "")
    with pytest.raises(ValueError):
        verify_resources(log, dual=True)


def test_comparison_requires_same_effective_network_and_devices():
    single = {"system": {"network": {"type": "GarnetNetwork", "path": "system.network",
        "dual_lane": False, "buffers_per_vnet": [1, 2, 1, 1, 2], "routers": [], "int_links": []},
        "core": {"type": "MeshDummyCore", "path": "system.core", "sram_bytes": 4096}}}
    dual = deepcopy(single)
    dual["system"]["network"]["dual_lane"] = True
    compare_configs(single, dual)
    dual["system"]["network"]["buffers_per_vnet"] = [4, 8, 4, 4, 8]
    with pytest.raises(ValueError):
        compare_configs(single, dual)
