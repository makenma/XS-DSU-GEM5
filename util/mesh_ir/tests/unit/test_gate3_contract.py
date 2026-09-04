from mesh_ir.acceptance import GATE_OF
from mesh_ir.gate3_contract import (
    GATE3_CASES,
    GATE3_IDS,
    gate3_readiness_failures,
)
from mesh_ir.gate3_oracle import CHECKS


def test_gate3_contract_exactly_covers_gate_membership():
    expected = tuple(case_id for case_id, gate in GATE_OF.items() if gate == 3)
    assert GATE3_IDS == expected
    assert tuple(GATE3_CASES) == expected
    assert len(GATE3_CASES) == 24


def test_gate3_subcase_and_backend_identities_are_unique():
    names = set()
    backends = set()
    for case_id, requirements in GATE3_CASES.items():
        local_names = [row.name for row in requirements]
        assert len(local_names) == len(set(local_names)), case_id
        for requirement in requirements:
            identity = (case_id, requirement.name)
            assert identity not in names
            names.add(identity)
            if requirement.runner == "GEM5":
                assert requirement.backend_case not in backends
                backends.add(requirement.backend_case)


def test_every_gem5_subcase_requires_independent_oracle_checks():
    for requirements in GATE3_CASES.values():
        for requirement in requirements:
            if requirement.runner == "GEM5":
                assert requirement.invariants[:len(CHECKS)] == CHECKS
                assert requirement.evidence
                assert len(requirement.invariants) == len(set(requirement.invariants))


def test_proto17_is_bound_only_to_existing_abi_unit_test_shapes():
    requirements = GATE3_CASES["PROTO-17"]
    assert sum(row.runner == "PYTEST" for row in requirements) == 10
    assert sum(row.runner == "GTEST" for row in requirements) == 4
    assert all(row.target for row in requirements)


def ready_manifest():
    cases = []
    for case_id, requirements in GATE3_CASES.items():
        subcases = []
        for requirement in requirements:
            if requirement.runner == "GEM5":
                execution = {
                    "runner": "GEM5",
                    "case_name": requirement.backend_case,
                    "args": [],
                }
            elif requirement.runner == "PYTEST":
                execution = {
                    "runner": "PYTEST",
                    "node_id": requirement.target,
                }
            else:
                execution = {
                    "runner": "GTEST",
                    "gtest_filter": requirement.target,
                }
            subcases.append(
                {
                    "name": requirement.name,
                    "terminal_class": requirement.terminal_class,
                    "execution": execution,
                }
            )
        cases.append({"id": case_id, "subcases": subcases})
    return {"cases": cases}


def test_runtime_readiness_accepts_exact_contract(tmp_path):
    registry = tmp_path / "registry.py"
    registry.write_text(
        "from mesh_ir.gate3_contract import GATE3_CASES\n"
        "CASES = {r.backend_case: r for rs in GATE3_CASES.values() for r in rs "
        "if r.runner == 'GEM5'}\n"
        "def invariant_registry(name, arguments):\n"
        "    return CASES[name].invariants\n",
        encoding="utf-8",
    )
    assert gate3_readiness_failures(ready_manifest(), registry) == ()


def test_runtime_readiness_rejects_invariant_drift(tmp_path):
    registry = tmp_path / "registry.py"
    registry.write_text(
        "from mesh_ir.gate3_contract import GATE3_CASES\n"
        "CASES = {r.backend_case: r for rs in GATE3_CASES.values() for r in rs "
        "if r.runner == 'GEM5'}\n"
        "def invariant_registry(name, arguments):\n"
        "    return ()\n",
        encoding="utf-8",
    )
    failures = gate3_readiness_failures(ready_manifest(), registry)
    assert failures
    assert all("invariant registry mismatch" in row for row in failures)
