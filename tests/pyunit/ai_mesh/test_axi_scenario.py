from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from axi_test_lib import (
    QUICK_CASES,
    build_negative_scenario,
    build_random_scenario,
    canonical_json_bytes,
    default_config_contract,
    full_case_names,
    semantic_config,
    validate_config_contract,
)
from run_axi_garnet_tests import DEFAULT_MANIFEST, validate_manifest


def manifest():
    return json.loads(Path(DEFAULT_MANIFEST).read_text(encoding="utf-8"))


def test_quick_manifest_exact_44():
    cases = validate_manifest(manifest())
    quick = {case["name"] for case in cases if "quick" in case["suites"]}
    assert len(quick) == 44
    assert quick == set(QUICK_CASES)


def test_full_manifest_exact_127():
    cases = validate_manifest(manifest())
    full = {case["name"] for case in cases if "full" in case["suites"]}
    assert len(full) == 127
    assert full == set(full_case_names())


def test_rejects_duplicate_missing_extra_cases():
    original = manifest()
    duplicate = deepcopy(original)
    duplicate["integration_cases"].append(
        deepcopy(duplicate["integration_cases"][0])
    )
    missing = deepcopy(original)
    missing["integration_cases"].pop()
    extra = deepcopy(original)
    extra["integration_cases"][0]["name"] = "unexpected_case"
    for malformed in (duplicate, missing, extra):
        with pytest.raises(ValueError):
            validate_manifest(malformed)


def test_validates_endpoint_router_map():
    scenario = build_negative_scenario("n3a_unaligned_decerr")
    config = default_config_contract()
    assert validate_config_contract(config, scenario)
    malformed = deepcopy(scenario)
    malformed["endpoint_to_router"]["targets"][0]["router_id"] = 0
    with pytest.raises(ValueError):
        validate_config_contract(config, malformed)


def test_random_domains_use_disjoint_ranges():
    scenario = build_random_scenario(42, 1000, "disjoint_s42")
    assert {transaction["qos"] for transaction in scenario["transactions"]} \
        == set(range(16))
    domains = {}
    for transaction in scenario["transactions"]:
        source = transaction["source_index"]
        direction = transaction["kind"]
        domain = (
            source, transaction["axi_id"], direction, transaction["dst_node"]
        )
        start = int(transaction["address"], 0)
        end = start + transaction["beat_count"] * (1 << transaction["size"])
        if domain not in domains:
            domains[domain] = [start, end]
        else:
            domains[domain][0] = min(domains[domain][0], start)
            domains[domain][1] = max(domains[domain][1], end)
    items = list(domains.items())
    for index, (lhs_domain, lhs) in enumerate(items):
        for rhs_domain, rhs in items[index + 1:]:
            if lhs_domain[-1] == rhs_domain[-1]:
                assert lhs[1] <= rhs[0] or rhs[1] <= lhs[0]


def test_stable_scenario_id_excludes_invocation_fields():
    first = build_random_scenario(42, 16, "stable_s42")
    second = deepcopy(first)
    first["name"] = "determinism_a"
    second["name"] = "determinism_b"
    lhs = semantic_config(first, {
        "seed": 42, "outdir": "/tmp/a", "invocation_id": "a",
    })
    rhs = semantic_config(second, {
        "seed": 42, "outdir": "/tmp/b", "invocation_id": "b",
    })
    assert canonical_json_bytes(lhs) == canonical_json_bytes(rhs)
