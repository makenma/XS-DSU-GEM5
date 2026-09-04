import copy
import importlib.util
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import jsonschema
import yaml

from mesh_ir.acceptance import expanded_schema


REPO = Path(__file__).resolve().parents[4]
HARNESS_DIR = REPO / "tests/gem5/ai_mesh"
MANIFEST_PATH = HARNESS_DIR / "mandatory_case_manifest.yaml"
SCHEMA_PATH = REPO / "schemas/ai_mesh/mandatory_manifest_v1.schema.json"
SELECTOR_PATH = HARNESS_DIR / "run_manifest_selector.py"
SELECTOR_SPEC = importlib.util.spec_from_file_location(
    "ai_mesh_run_manifest_selector", SELECTOR_PATH
)
SELECTOR = importlib.util.module_from_spec(SELECTOR_SPEC)
SELECTOR_SPEC.loader.exec_module(SELECTOR)


def test_mandatory_manifest_matches_v1_schema():
    assert SCHEMA_PATH.is_file()
    document = yaml.safe_load(MANIFEST_PATH.read_text())
    assert document["schema"] == "ai_mesh_mandatory_manifest_v1"
    assert set(document) == {"schema", "version", "cases"}
    schema = expanded_schema("mandatory_manifest_v1.schema.json")
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(document)


def test_mandatory_subcases_have_exact_expectation_and_execution_fields():
    document = yaml.safe_load(MANIFEST_PATH.read_text())
    expected = {
        "name",
        "terminal_class",
        "expected_exit_reason",
        "expected_first_fatal",
        "expected_ledgers",
        "timeout",
        "artifacts",
        "execution",
    }
    for case in document["cases"]:
        for subcase in case["subcases"]:
            assert set(subcase) == expected, (case["id"], subcase["name"])


def test_child_environment_base_is_exact():
    assert SELECTOR.CHILD_ENV_BASE == {
        "LC_ALL": "C",
        "TZ": "UTC",
        "PYTHONHASHSEED": "0",
        "OMP_NUM_THREADS": "1",
    }


def _pytest_subcase(name):
    document = yaml.safe_load(MANIFEST_PATH.read_text())
    subcase = next(
        subcase
        for case in document["cases"]
        for subcase in case["subcases"]
        if subcase["execution"]["runner"] == "PYTEST"
    )
    result = copy.deepcopy(subcase)
    result["name"] = name
    return result


def test_subcase_environment_has_exact_identity_and_report_path(
    tmp_path, monkeypatch
):
    captured = {}
    result = subprocess.CompletedProcess([], 0, "", "")

    def run_once(command, cwd, env, timeout):
        captured.update(env)
        return result, 0.0, None

    monkeypatch.setattr(SELECTOR, "run_once", run_once)
    golden = tmp_path / "golden"
    golden.mkdir()
    results = tmp_path / "results"
    SELECTOR.run_subcase(
        "DC-1",
        _pytest_subcase("environment"),
        golden,
        results,
    )
    artifact_dir = results / "DC-1" / "environment"
    assert captured == {
        "LC_ALL": "C",
        "TZ": "UTC",
        "PYTHONHASHSEED": "0",
        "OMP_NUM_THREADS": "1",
        "AI_MESH_CASE_ID": "DC-1",
        "AI_MESH_SUBCASE": "environment",
        "AI_MESH_ARTIFACT_DIR": str(artifact_dir),
        "AI_MESH_CHILD_REPORT": str(artifact_dir / "child_report.json"),
    }


def test_junit_testcase_identity_contains_logical_id(tmp_path):
    report = {
        "selected_subcase_count": 1,
        "subcase_fail_count": 0,
        "cases": [
            {
                "id": "DC-1",
                "subcases": [
                    {"name": "decode_closed_set", "status": "PASS"}
                ],
            }
        ],
    }
    path = tmp_path / "junit.xml"
    SELECTOR.write_junit(report, path)
    testcase = ET.parse(path).find(".//testcase")
    assert testcase.attrib["name"] == "DC-1/decode_closed_set"


def test_success_exit_without_child_report_is_not_accepted(tmp_path, monkeypatch):
    result = subprocess.CompletedProcess([], 0, "", "")
    monkeypatch.setattr(
        SELECTOR,
        "run_once",
        lambda command, cwd, env, timeout: (result, 0.0, None),
    )
    golden = tmp_path / "golden"
    golden.mkdir()
    accepted, _, _ = SELECTOR.run_subcase(
        "DC-1",
        _pytest_subcase("missing_report"),
        golden,
        tmp_path / "results",
    )
    assert not accepted
