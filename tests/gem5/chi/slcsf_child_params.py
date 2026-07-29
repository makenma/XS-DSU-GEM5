"""Validate HomeNodeFull-to-SlcSnoopFilter parameter compatibility.

This is an instantiate-only smoke. It reads the generated config.ini so the
checks cover final SimObject proxy resolution, not just Python assignments.
"""

import configparser
from pathlib import Path

import m5
from m5.objects import (
    Cache2ChiBridge,
    HomeNodeFull,
    Root,
    SlcSnoopFilter,
    SrcClockDomain,
    System,
    VoltageDomain,
)


def connect_hnf(system, name, hnf):
    setattr(system, name, hnf)
    requester = Cache2ChiBridge(wakeup_target=hnf)
    setattr(system, name + "_requester", requester)
    hnf.rxport = requester.chi_side


def section(config, name):
    path = "system.{}.slcsf".format(name)
    assert config.has_section(path), "missing config.ini section " + path
    return config[path]


def expect_values(actual, expected):
    for name, value in expected.items():
        assert actual[name] == str(value), (
            "{} resolved to {}, expected {}".format(
                name, actual[name], value
            )
        )


system = System(cache_line_size=64)
system.voltage_domain = VoltageDomain()
system.clk_domain = SrcClockDomain(
    clock="1GHz", voltage_domain=system.voltage_domain
)
system.slcsf_voltage_domain = VoltageDomain()
system.slcsf_clk_domain = SrcClockDomain(
    clock="2GHz", voltage_domain=system.slcsf_voltage_domain
)

connect_hnf(system, "default_hnf", HomeNodeFull())

parent_values = {
    "slc_num_sets": 64,
    "slc_num_ways": 2,
    "sf_num_sets": 32,
    "sf_num_ways": 4,
    "seq_entries": 3,
    "slcsf_lookup_latency": 5,
    "slcsf_fill_latency": 6,
    "slcsf_update_latency": 7,
    "slcsf_victim_latency": 8,
    "slcsf_sf_evict_latency": 9,
    "slcsf_replay_penalty": 10,
    "slcsf_req_queue_entries": 11,
    "slcsf_resp_queue_entries": 12,
    "slcsf_victim_buffer_entries": 13,
    "slcsf_lookup_issue_width": 2,
    "slcsf_fill_issue_width": 3,
    "slcsf_update_issue_width": 4,
    "slcsf_max_inflight": 5,
    "slcsf_response_consume_width": 6,
    "slcsf_enable_set_lock": "true",
}
connect_hnf(system, "parent_hnf", HomeNodeFull(**parent_values))

child_values = {
    "slc_num_sets": 128,
    "sf_num_ways": 8,
    "seq_entries": 7,
    "slcsf_lookup_latency": 14,
    "slcsf_req_queue_entries": 15,
    "slcsf_victim_buffer_entries": 16,
    "slcsf_fill_issue_width": 7,
    "slcsf_max_inflight": 8,
    "clk_domain": system.slcsf_clk_domain,
}
connect_hnf(
    system,
    "child_hnf",
    HomeNodeFull(
        **parent_values,
        slcsf=SlcSnoopFilter(**child_values),
    ),
)

root = Root(full_system=False, system=system)
m5.instantiate()

config = configparser.ConfigParser()
config.read(Path(m5.options.outdir) / "config.ini")

expect_values(
    section(config, "default_hnf"),
    {
        "block_size": 64,
        "slc_num_sets": 1024,
        "slc_num_ways": 16,
        "sf_num_sets": 1024,
        "sf_num_ways": 16,
        "seq_entries": 8,
        "slcsf_lookup_latency": 4,
        "slcsf_fill_latency": 4,
        "slcsf_update_latency": 3,
        "slcsf_victim_latency": 3,
        "slcsf_sf_evict_latency": 2,
        "slcsf_replay_penalty": 2,
        "slcsf_req_queue_entries": 8,
        "slcsf_resp_queue_entries": 8,
        "slcsf_victim_buffer_entries": 2,
        "slcsf_lookup_issue_width": 1,
        "slcsf_fill_issue_width": 1,
        "slcsf_update_issue_width": 1,
        "slcsf_max_inflight": 1,
        "slcsf_response_consume_width": 1,
        "slcsf_enable_set_lock": "false",
        "clk_domain": "system.clk_domain",
    },
)

expect_values(section(config, "parent_hnf"), parent_values)
assert section(config, "parent_hnf")["clk_domain"] == "system.clk_domain"

resolved_child = dict(parent_values)
resolved_child.update(child_values)
resolved_child["clk_domain"] = "system.slcsf_clk_domain"
expect_values(section(config, "child_hnf"), resolved_child)

print("SLCSF_CHILD_PARAMS_PASS")
