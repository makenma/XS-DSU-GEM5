"""Gate 5 cumulative packet/flit traffic and the loader snapshot schema.

The healthy control is a real two-instance run: its archived Garnet traffic must
equal the independently derived admitted packetizer plan per virtual network,
and its loader install window must state the complete owner fact set with an
empty delta.  Every tamper is a single edit of that archive.
"""

import copy

import pytest

from mesh_ir.runtime_reconciliation import (
    ReconciliationError,
    expected_garnet_traffic,
    loader_control_plane_delta,
    verified_garnet_traffic,
)

from tests.integration.support.garnet_harness import (
    build_program,
    cause_of,
    expected_traffic,
    output_of,
    result_of,
    run_garnet,
    schedule,
)

CASE = "dma_basic"
PROGRAM = "single"
INSTANCES = 2
FLIT_BYTES = 16
VNETS = 5
PACKETS = 1664
FLITS = 4832


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    work = tmp_path_factory.mktemp("gate5-traffic")
    program_dir = build_program(work, PROGRAM)
    run = run_garnet(
        work, "m5out", CASE, program_dir,
        extra_args=("--instances", str(INSTANCES)),
    )
    assert run.returncode == 0, output_of(run)
    assert cause_of(run).startswith("MESH_PROGRAM_DONE"), output_of(run)
    assert "packet traffic: PASS" in output_of(run), output_of(run)
    return {"program_dir": program_dir, "result": result_of(program_dir)}


def _descriptors(program_dir):
    kinds = {
        row["descriptor_id"]: row["kind"]
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
    }
    return {
        row["identity"]["descriptor_id"]: {
            **row,
            **row["identity"],
            "kind": kinds[row["identity"]["descriptor_id"]],
        }
        for row in expected_traffic(program_dir)
    }


def _expected(baseline):
    return expected_garnet_traffic(_descriptors(baseline["program_dir"]), INSTANCES)


def _verify(traffic, expected):
    return verified_garnet_traffic(
        traffic, expected, flit_bytes=FLIT_BYTES, vnets=VNETS
    )


def test_the_archived_traffic_equals_the_admitted_packetizer_plan(baseline):
    traffic = baseline["result"]["garnet_traffic"]
    expected = _expected(baseline)
    assert _verify(traffic, expected) == {"vnets": VNETS, "packets": PACKETS}
    assert traffic["flit_bytes"] == FLIT_BYTES, traffic
    assert sum(traffic["injected"]["packets"]) == PACKETS, traffic
    assert sum(traffic["injected"]["flits"]) == FLITS, traffic
    assert traffic["received"] == traffic["injected"], traffic


def test_the_loader_install_window_is_complete_and_empty(baseline):
    window = baseline["result"]["loader_traffic"]
    assert loader_control_plane_delta(window) == {}
    assert window["end_tick"] >= window["begin_tick"], window


@pytest.mark.parametrize(
    "tamper, message",
    (
        ("packet-added", "the admitted plan expects"),
        ("flit-added", "the admitted plan expects"),
        ("group-dropped", "cover"),
        ("receive-short", "but received"),
        ("flit-width", "flit"),
        ("other-instance", "the admitted plan expects"),
        ("plan-write-group-zeroed", "the admitted plan expects 0"),
        ("plan-read-groups-zeroed", "the admitted plan expects 0"),
    ),
)
def test_the_traffic_entry_rejects_corruption(baseline, tamper, message):
    traffic = copy.deepcopy(baseline["result"]["garnet_traffic"])
    expected = _expected(baseline)
    if tamper == "packet-added":
        traffic["injected"]["packets"][0] += 1
        traffic["received"]["packets"][0] += 1
    elif tamper == "flit-added":
        traffic["injected"]["flits"][2] += 1
        traffic["received"]["flits"][2] += 1
    elif tamper == "group-dropped":
        for field in ("packets", "flits"):
            traffic["injected"][field] = traffic["injected"][field][:-1]
            traffic["received"][field] = traffic["received"][field][:-1]
    elif tamper == "receive-short":
        traffic["received"]["flits"][1] -= 1
    elif tamper == "flit-width":
        traffic["flit_bytes"] = 2 * FLIT_BYTES
    elif tamper == "other-instance":
        for field in ("packets", "flits"):
            traffic["injected"][field] = [
                value // INSTANCES for value in traffic["injected"][field]
            ]
            traffic["received"][field] = [
                value // INSTANCES for value in traffic["received"][field]
            ]
    elif tamper == "plan-write-group-zeroed":
        # The expectation forgets the write vnet the plan really uses.
        del expected[0]
    elif tamper == "plan-read-groups-zeroed":
        # The expectation claims a pure-write plan while the archive really
        # moved read traffic: the zero baseline must reject the AR/R groups.
        del expected[3]
        del expected[4]
    with pytest.raises(ReconciliationError, match=message):
        _verify(traffic, expected)


def _burst_expectation(result, descriptors):
    """The wrapper's derivation: the admitted per-channel packetizer
    projection times the bursts the run really archived, over a zero baseline
    for every legal vnet."""
    projection = {}
    for row in descriptors.values():
        for channel in row.get("channels") or ():
            if channel.get("messages"):
                projection[channel["channel"]] = (
                    channel["vnet"], channel["flits"] // channel["messages"])
    accepted = {}
    for frame in result["instances"]:
        for row in frame.get("bursts", ()):
            key = (row["command_id"], row["generation"], row["descriptor_id"])
            entry = accepted.setdefault(
                key, {"AR": 0, "AR_beats": 0, "AW": 0, "AW_beats": 0})
            if row["channel"] == "AR":
                entry["AR"] += 1
                entry["AR_beats"] += row["beats"]
            else:
                entry["AW"] += 1
                entry["AW_beats"] += row["beats"]
    expected = {vnet: {"packets": 0, "flits": 0} for vnet in range(VNETS)}
    for entry in accepted.values():
        messages = {0: entry["AW"], 1: entry["AW_beats"], 2: entry["AW"],
                    3: entry["AR"], 4: entry["AR_beats"]}
        for channel, count in messages.items():
            if not count:
                continue
            vnet, per_message = projection[channel]
            expected[vnet]["packets"] += count
            expected[vnet]["flits"] += count * per_message
    return expected


def _carrier(tmp_path, name, case, program):
    program_dir = build_program(tmp_path, program)
    run = run_garnet(tmp_path, "m5out-%s" % name, case, program_dir)
    assert run.returncode == 0, output_of(run)
    assert "packet traffic: PASS" in output_of(run), output_of(run)
    return {"program_dir": program_dir, "result": result_of(program_dir)}


def test_a_read_only_carrier_rejects_unplanned_write_traffic(tmp_path):
    """G5-R23-05: the read-error drain uses no write channel, so one extra AW
    packet on the write vnet is traffic no plan explains."""
    carrier = _carrier(tmp_path, "read-error", "read_error", "dma_error")
    result = carrier["result"]
    expected = _burst_expectation(
        result, _descriptors(carrier["program_dir"]))
    victim = next(
        vnet for vnet in range(VNETS)
        if result["garnet_traffic"]["injected"]["packets"][vnet] == 0)
    traffic = copy.deepcopy(result["garnet_traffic"])
    traffic["injected"]["packets"][victim] += 1
    traffic["received"]["packets"][victim] += 1
    traffic["injected"]["flits"][victim] += 2
    traffic["received"]["flits"][victim] += 2
    with pytest.raises(ReconciliationError, match="the admitted plan expects 0"):
        _verify(traffic, expected)


def test_a_zero_dma_carrier_rejects_any_packet(tmp_path):
    carrier = _carrier(tmp_path, "dma-zero", "dma_zero", "zero_dma")
    result = carrier["result"]
    expected = _burst_expectation(result, _descriptors(carrier["program_dir"]))
    assert not any(entry["packets"] for entry in expected.values()), expected
    traffic = copy.deepcopy(result["garnet_traffic"])
    traffic["injected"]["packets"][2] += 1
    traffic["received"]["packets"][2] += 1
    with pytest.raises(ReconciliationError, match="the admitted plan expects 0"):
        _verify(traffic, expected)


@pytest.mark.parametrize(
    "tamper, message",
    (
        ("empty-pair", "dropped the required fields"),
        ("paired-deletion", "dropped the required fields"),
        ("counter-rollback", "went backwards"),
        ("non-integer", "is"),
    ),
)
def test_the_loader_window_schema_rejects_thinning(baseline, tamper, message):
    window = copy.deepcopy(baseline["result"]["loader_traffic"])
    if tamper == "empty-pair":
        window["begin"] = {}
        window["end"] = {}
    elif tamper == "paired-deletion":
        for edge in ("begin", "end"):
            window[edge].pop("packets_injected")
            window[edge].pop("flits_received")
    elif tamper == "counter-rollback":
        window["begin"]["packets_injected"] = 1
        window["end"]["packets_injected"] = 0
    elif tamper == "non-integer":
        window["end"]["ar_accepted"] = "0"
    with pytest.raises(ReconciliationError, match=message):
        loader_control_plane_delta(window)
