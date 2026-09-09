import json
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[4]
GEM5 = REPO / "build/AXI_MESH/gem5.opt"
CONFIG = REPO / "configs/example/axi_garnet_test.py"


@pytest.mark.parametrize("shared", [False, True])
def test_core_and_memory_have_distinct_local_ports(tmp_path, shared):
    scenario = json.loads((REPO / "configs/example/axi_garnet_scenarios/burst_read.json").read_text())
    scenario["endpoint_to_router"]["targets"][0]["router_id"] = 0
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(scenario))
    argv = [str(GEM5), f"--outdir={tmp_path / 'm5out'}", str(CONFIG),
            "--axi-scenario", str(scenario_path),
            "--axi-max-sim-ticks", "1000000000"]
    if shared:
        argv.append("--axi-shared-router-endpoints")
    run = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, timeout=60)
    (tmp_path / "run.log").write_text(run.stdout + run.stderr)
    if not shared:
        assert run.returncode != 0
        assert "router IDs must be unique" in run.stdout + run.stderr
        return
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AXI_MESH_FUNCTIONAL_SCENARIO_PASS" in run.stdout
    config = json.loads((tmp_path / "m5out/config.json").read_text())
    links = config["system"]["ruby"]["network"]["ext_links"]
    assert len(links) == 2
    assert len({link["ext_node"] for link in links}) == 2
    assert {link["int_node"] for link in links} == {"system.ruby.network.routers0"}
    result = json.loads((tmp_path / "m5out/axi_result.json").read_text())
    assert result["status"] == "pass"
