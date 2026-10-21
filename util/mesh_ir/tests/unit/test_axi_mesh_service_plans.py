import ast
from pathlib import Path

import pytest


def load_planner():
    path = Path(__file__).resolve().parents[4] / "configs/ruby/AXI_MESH.py"
    module = ast.parse(path.read_text())
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_mesh_program_plans")

    def fatal(message, *args):
        raise ValueError(message % args)

    scope = {"fatal": fatal}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), scope)
    return scope["_mesh_program_plans"]


def test_service_error_and_response_delay_share_one_transaction():
    plans, responses = load_planner()({
        "mesh_planned_extra_latency": [{"target": 9, "uid": 3, "cycles": 17}],
        "mesh_planned_faults": [{"target": 9, "uid": 3, "resp": "slverr"}],
        "mesh_planned_b_ejection": [{"target": 9, "uid": 3, "cycles": 23}],
    }, [{"dst_node": 4}, {"dst_node": 9}])
    assert plans == [[], [(3, 17, "slverr")]]
    assert responses == [[], [(3, 23, "okay")]]


@pytest.mark.parametrize("kind", ["mesh_planned_extra_latency", "mesh_planned_faults", "mesh_planned_b_ejection"])
def test_duplicate_identity_within_one_phase_rejected(kind):
    record = {"target": 9, "uid": 3, "cycles": 17, "resp": "slverr"}
    with pytest.raises(ValueError, match="repeats a target/uid"):
        load_planner()({kind: [record, dict(record)]}, [{"dst_node": 9}])


def test_empty_targets_and_plans():
    assert load_planner()({}, []) == ([], [])
    assert load_planner()({}, [{"dst_node": 9}]) == ([[]], [[]])


def test_same_uid_at_distinct_targets_retains_both():
    records = [{"target": target, "uid": 3, "cycles": target} for target in (9, 4)]
    plans, responses = load_planner()({"mesh_planned_extra_latency": records}, [{"dst_node": 4}, {"dst_node": 9}])
    assert plans == [[(3, 4, "okay")], [(3, 9, "okay")]]
    assert responses == [[], []]


def test_unknown_target_rejected():
    with pytest.raises(ValueError, match="unknown target"):
        load_planner()({"mesh_planned_faults": [{"target": 10, "uid": 3}]}, [{"dst_node": 9}])
