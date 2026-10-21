import json

import pytest
import yaml

from mesh_ir.abi.decoder import decode_program
from mesh_ir.serving_profiles import serving_capacity_required

from .serving_runtime import (
    FIXTURES, PROBE, run_serving_fixture, write_serving_fixture,
)


@pytest.mark.parametrize("boundary", ["runtime_config", "loader"])
@pytest.mark.parametrize("field", [
    "batch_weight_binding_entries_per_context",
    "instance_member_binding_entries_per_context",
    "batch_interval_entries_per_context",
])
def test_each_binding_capacity_is_proven_before_any_execution(tmp_path, field, boundary):
    program = decode_program((FIXTURES / "serving_two_tokens.mshb").read_bytes())
    work = write_serving_fixture(tmp_path, program)
    required = serving_capacity_required(program).fields()
    under = required[field] - 1
    entry = PROBE
    if boundary == "runtime_config":
        config_path = work / "config.yaml"
        config = yaml.safe_load(config_path.read_text())
        config["serving"][field] = under
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    else:
        entry = work / "loader_capacity_probe.py"
        entry.write_text(
            "import runpy\n"
            "from m5.objects import MeshProgramLoader\n"
            "original = MeshProgramLoader.__init__\n"
            "def capacity_override(self, *args, **kwargs):\n"
            f"    kwargs[{field!r}] = {under}\n"
            "    original(self, *args, **kwargs)\n"
            "MeshProgramLoader.__init__ = capacity_override\n"
            f"runpy.run_path({str(PROBE)!r}, run_name='__m5_main__')\n")
    run_serving_fixture(work, "rejected", entry=entry,
                        rejection_code="E_CAPACITY_PLAN")


def test_exact_binding_capacity_runs_and_publishes_the_plan(tmp_path):
    program = decode_program((FIXTURES / "serving_two_tokens.mshb").read_bytes())
    work = write_serving_fixture(tmp_path, program)
    required = serving_capacity_required(program).fields()
    config_path = work / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["serving"].update(required)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    _, result, metrics = run_serving_fixture(work, "exact")
    assert result["terminal"] == "DONE"
    assert metrics["metadata_reads_validated"] == 1
    plan = json.loads((work / "exact/serving_capacity_plan.json").read_text())
    assert plan["configured"] == plan["required"] == required
    assert set(plan["headroom"].values()) == {0}
