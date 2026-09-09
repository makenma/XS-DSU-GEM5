from mesh_ir.experiment.metrics import GROUP_FIELDS
from mesh_ir.experiment.plots import plot_results


def test_plots_do_not_join_different_source_provenance(tmp_path):
    row = {field: "fixed" for field in GROUP_FIELDS}
    row.update(case_id="first", topology="H5", workload="LOAD_ONLY",
               distribution="uniform", burst_beats=16, correctness="pass",
               bandwidth_Bps=10, n_read=4, p99_ticks=12,
               router_buffer_bytes=4096, map_digest="map", stable=True)
    changed = {**row, "case_id": "second", "source_digest": "different"}
    plot_results(tmp_path, [row, changed], {})
    assert len(list(tmp_path.glob("*.svg"))) == 6


def test_representative_link_and_port_plots_keep_physical_direction(tmp_path):
    row = {field: "fixed" for field in GROUP_FIELDS}
    row.update(case_id="case", topology="H5", workload="LOAD_ONLY", distribution="uniform",
               burst_beats=16, correctness="pass", bandwidth_Bps=10, n_read=4,
               p99_ticks=12, router_buffer_bytes=4096, map_digest="map", stable=True,
               router_buffer_map={"entries": [[0, 0, vnet, 1] for vnet in range(5)]},
               per_link=[{"link_id": 0, "route": "router:0->router:1", "utilization": .75,
                          "flits": 3, "AW": 0, "W": 0, "B": 0, "AR": 0, "R": 3}],
               per_router_inport_vnet=[{"router_id": 0, "inport_id": 0, "direction": "East", "channel": "R",
                                          "depth": 1, "average": .25, "full_fraction": .25,
                                          "credit_stalls": 4, "no_vc_stalls": 2, "sa_lost": 1}])
    plot_results(tmp_path, [row], {"group": {"resource_recommendation": "case"}})
    assert "router:0-&gt;router:1" in (tmp_path / "case/link_utilization.svg").read_text()
    assert "East" in (tmp_path / "case/port_pressure.svg").read_text()
    assert "router:0->router:1" in (tmp_path / "case/link_utilization.csv").read_text()
