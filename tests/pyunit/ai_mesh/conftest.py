from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
HELPERS = ROOT / "tests/gem5/axi_garnet"
sys.path.insert(0, str(HELPERS))

EXPECTED_NODE_IDS = {
    "tests/pyunit/ai_mesh/test_axi_config.py::test_accepts_default_configuration",
    "tests/pyunit/ai_mesh/test_axi_config.py::test_rejects_invalid_csv",
    "tests/pyunit/ai_mesh/test_axi_config.py::test_rejects_invalid_widths_and_datablock",
    "tests/pyunit/ai_mesh/test_axi_config.py::test_rejects_overlapping_or_empty_ranges",
    "tests/pyunit/ai_mesh/test_axi_config.py::test_rejects_quota_oversubscription",
    "tests/pyunit/ai_mesh/test_axi_config.py::test_rejects_zero_or_infinite_buffers",
    "tests/pyunit/ai_mesh/test_axi_config.py::test_rejects_non_strict_or_compact_mode",
    "tests/pyunit/ai_mesh/test_axi_scenario.py::test_quick_manifest_exact_44",
    "tests/pyunit/ai_mesh/test_axi_scenario.py::test_full_manifest_exact_127",
    "tests/pyunit/ai_mesh/test_axi_scenario.py::test_rejects_duplicate_missing_extra_cases",
    "tests/pyunit/ai_mesh/test_axi_scenario.py::test_validates_endpoint_router_map",
    "tests/pyunit/ai_mesh/test_axi_scenario.py::test_random_domains_use_disjoint_ranges",
    "tests/pyunit/ai_mesh/test_axi_scenario.py::test_stable_scenario_id_excludes_invocation_fields",
    "tests/pyunit/ai_mesh/test_axi_trace_checker.py::test_accepts_canonical_valid_trace",
    "tests/pyunit/ai_mesh/test_axi_trace_checker.py::test_rejects_duplicate_or_missing_beats",
    "tests/pyunit/ai_mesh/test_axi_trace_checker.py::test_rejects_bad_last_or_response_order",
    "tests/pyunit/ai_mesh/test_axi_trace_checker.py::test_rejects_bad_memory_data",
    "tests/pyunit/ai_mesh/test_axi_trace_checker.py::test_rejects_bad_credit_ledger",
    "tests/pyunit/ai_mesh/test_axi_determinism.py::test_splitmix64_golden_vectors",
    "tests/pyunit/ai_mesh/test_axi_determinism.py::test_canonical_jsonl_bytes_and_sha",
    "tests/pyunit/ai_mesh/test_axi_determinism.py::test_replay_hash_independent_of_invocation",
}


def pytest_collection_finish(session):
    if os.environ.get("AXI_MESH_ALLOW_PARTIAL_PYTEST") == "1":
        return
    collected = {
        item.nodeid for item in session.items
        if item.nodeid.startswith("tests/pyunit/ai_mesh/")
    }
    if collected != EXPECTED_NODE_IDS:
        missing = sorted(EXPECTED_NODE_IDS - collected)
        extra = sorted(collected - EXPECTED_NODE_IDS)
        raise pytest.UsageError(
            f"AI Mesh pytest collection mismatch: missing={missing}, extra={extra}"
        )


def pytest_runtest_logreport(report):
    if report.skipped:
        pytest.fail(f"AI Mesh tests may not skip/xfail: {report.nodeid}")
