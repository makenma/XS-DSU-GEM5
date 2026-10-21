import copy
import json

import pytest

from mesh_ir.runtime_reconciliation import ReconciliationError
from mesh_ir.workload_accounting import verified_tensor_class_traffic
from tests.integration.support.garnet_harness import output_of, run_garnet, schedule
from tests.integration.support.workload_harness import ARCH, compiled_workloads, descriptors


@pytest.fixture(scope="module")
def transformer_run(compiled_workloads, tmp_path_factory):
    program = compiled_workloads("transformer")
    root = tmp_path_factory.mktemp("transformer-run")
    run = run_garnet(root, "m5out", "transformer_prefill", program, arch=ARCH,
                     artifact_dir=root, extra_args=("--instances", "2"))
    assert run.returncode == 0, output_of(run)
    assert "tensor class traffic: PASS" in output_of(run)
    return program, json.loads((root / "actual_result.json").read_text())


def test_transformer_traffic_reconciles_every_instance(transformer_run):
    program, result = transformer_run
    roles = {row["tensor_id"]: row["role"] for row in schedule(program)["TENSORS"]}
    report = verified_tensor_class_traffic(descriptors(program), result["instances"], roles, instances=2)
    assert report["classes"] >= 3
    assert {row["instance"] for row in report["rows"]} == {1, 2}
    assert {row["core"] for row in report["rows"]} == {7, 2, 9, 13}
    assert all(row["expected_bytes"] == row["actual_bytes"] for row in report["rows"])


@pytest.mark.parametrize("mutation", ("relabel", "table", "bytes", "last_instance", "core", "duplicate"))
def test_tensor_traffic_rejects_corruption(transformer_run, mutation):
    program, original = transformer_run
    result = copy.deepcopy(original)
    expected = descriptors(program)
    roles = {row["tensor_id"]: row["role"] for row in schedule(program)["TENSORS"]}
    row = next(iter(expected.values()))
    if mutation == "relabel":
        row["tensor_role"] += 100
    elif mutation == "table":
        roles[row["tensor_id"]] += 100
    elif mutation == "bytes":
        row["useful_bytes"] -= 1
    elif mutation == "last_instance":
        result["instances"].pop()
    elif mutation == "core":
        result["instances"][1]["bursts"][0]["core_id"] = 65535
    else:
        result["instances"][1]["bursts"][1] = copy.deepcopy(result["instances"][1]["bursts"][0])
    with pytest.raises(ReconciliationError):
        verified_tensor_class_traffic(expected, result["instances"], roles, instances=2)
