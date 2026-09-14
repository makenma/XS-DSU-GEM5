import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "util" / "mesh_ir"))

from mesh_ir.gate5_contract import (  # noqa: E402
    MULTI_CACHED_OVERLAYS,
    prestart_terminal_gaps,
    CACHED_OVERLAY,
    DROP_OVERLAY,
    COPY_OVERLAY,
    COPY_PROGRAM,
    DROP_PROGRAM,
    QUAD_HOTSPOT_OVERLAY,
    QUAD_REPLAY_OVERLAY,
    E2E_ARCH,
    E2E_PROGRAM,
    E2E_SUBCASE,
    GATE5_CASES,
    GATE5_OPEN_GAPS,
    GATE5_PLACEHOLDER_IDS,
    QUAD_ARCH,
    QUAD_OVERLAY,
    QUAD_PROGRAM,
    STREAMED_OVERLAY,
    coverage_gaps,
    gate5_readiness_failures,
    manifest_subcase,
)

MANIFEST = REPO / "tests/gem5/ai_mesh/mandatory_case_manifest.yaml"
REGISTRY = REPO / "configs/example/ai_mesh/dummy_core_case_registry.py"


@pytest.fixture(scope="module")
def manifest():
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_gate5_contract_matches_the_manifest(manifest):
    assert gate5_readiness_failures(manifest, REGISTRY) == ()


def test_gate5_every_registered_pytest_node_resolves_exactly_once():
    import subprocess

    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "util/mesh_ir/tests/unit",
         "util/mesh_ir/tests/integration/test_gate5_e2e_determinism.py",
         "--collect-only", "-q"],
        cwd=REPO, capture_output=True, text=True)
    assert collected.returncode == 0, collected.stderr[-2000:]
    known = set()
    for line in collected.stdout.splitlines():
        node = line.split(" ")[0]
        for directory in ("tests/unit/", "tests/integration/"):
            if directory not in node:
                continue
            suffix = node.split(directory, 1)[1]
            known.add(f"util/mesh_ir/{directory}{suffix}")
    missing = []
    for case_id, requirements in GATE5_CASES.items():
        for requirement in requirements:
            if requirement.runner != "PYTEST":
                continue
            if requirement.node_id not in known:
                missing.append(f"{case_id}/{requirement.name}: "
                               f"{requirement.node_id}")
    assert missing == []


def test_gate5_e2e_subcase_uses_the_registered_backend_case(manifest):
    e2e = GATE5_CASES["MOE-30"][0]
    assert e2e.runner == "PYTEST"
    cases = {case["id"]: case for case in manifest["cases"]}
    subcase = cases["E2E-C"]["subcases"][0]
    args = tuple(subcase["execution"]["args"])
    assert subcase["execution"]["case_name"] == E2E_SUBCASE
    assert args == ("--mesh-program-dir", E2E_PROGRAM, "--arch", E2E_ARCH,
                    "--overlay-image", STREAMED_OVERLAY)
    assert args[1] == "$GOLDEN/moe_dual"


def test_gate5_cached_subcase_uses_the_cached_overlay_image(manifest):
    cases = {case["id"]: case for case in manifest["cases"]}
    cached = [subcase for subcase in cases["MOE-19"]["subcases"]
              if subcase["name"] == "cached_cold_fill"]
    assert len(cached) == 1
    args = tuple(cached[0]["execution"]["args"])
    assert args == ("--mesh-program-dir", E2E_PROGRAM, "--arch", E2E_ARCH,
                    "--overlay-image", CACHED_OVERLAY,
                    "--weight-policy", "cached")
    assert CACHED_OVERLAY != STREAMED_OVERLAY
    cached_arms = 0
    quad_arms = 0
    drop_arms = 0
    copy_arms = 0
    for case_id, subcases in GATE5_CASES.items():
        for subcase in subcases:
            if subcase.runner != "GEM5":
                continue
            args = subcase.manifest_args
            cached = "--weight-policy" in args and \
                args[args.index("--weight-policy") + 1] == "cached"
            if cached:
                assert subcase.overlay_image in (CACHED_OVERLAY,
                                                 MULTI_CACHED_OVERLAYS), \
                    (case_id, subcase.name)
                cached_arms += 1
                continue
            assert subcase.overlay_image in (
                STREAMED_OVERLAY, DROP_OVERLAY, COPY_OVERLAY, QUAD_OVERLAY,
                QUAD_REPLAY_OVERLAY,
                QUAD_HOTSPOT_OVERLAY), (case_id, subcase.name)
            if subcase.overlay_image in (QUAD_OVERLAY, QUAD_REPLAY_OVERLAY,
                                         QUAD_HOTSPOT_OVERLAY):
                assert subcase.program == QUAD_PROGRAM
                assert subcase.arch == QUAD_ARCH
                quad_arms += 1
            else:
                assert subcase.arch == E2E_ARCH
                if subcase.overlay_image == DROP_OVERLAY:
                    assert subcase.program == DROP_PROGRAM
                    drop_arms += 1
                elif subcase.overlay_image == COPY_OVERLAY:
                    assert subcase.program == COPY_PROGRAM
                    copy_arms += 1
                else:
                    assert subcase.program == E2E_PROGRAM
    assert cached_arms >= 2
    assert quad_arms >= 1
    assert drop_arms == 1
    assert copy_arms == 1


def test_gate5_coverage_gaps_track_open_capabilities(manifest):
    gaps = coverage_gaps(manifest)
    assert {gap.split("/")[0] for gap in gaps} == {
        case_id for case_id, _name, _detail in GATE5_OPEN_GAPS}
    for case_id, name, detail in GATE5_OPEN_GAPS:
        assert f"{case_id}/{name}: {detail}" in gaps
    if not GATE5_OPEN_GAPS:
        assert not gaps, "a fully wired gate must report no coverage gap"


def test_gate5_coverage_gaps_reject_a_vacuous_manifest():
    gaps = coverage_gaps({"cases": []})
    assert gaps
    assert "manifest: no cases registered" in gaps
    assert any(gap.endswith("missing from the manifest") for gap in gaps)


def test_gate5_manifest_subcase_projection_matches_the_manifest(manifest):
    cases = {case["id"]: case for case in manifest["cases"]}
    for case_id in ("MOE-1", "MOE-16", "MOE-27"):
        projected = [manifest_subcase(requirement)
                     for requirement in GATE5_CASES[case_id]]
        actual = cases[case_id]["subcases"]
        assert [row["name"] for row in projected] == [
            row["name"] for row in actual]
        for left, right in zip(projected, actual):
            assert left["execution"] == right["execution"]
            assert left["terminal_class"] == right["terminal_class"]


def test_gate5_placeholder_ids_stay_gate_five_only(manifest):
    cases = {case["id"]: case for case in manifest["cases"]}
    for case_id in GATE5_PLACEHOLDER_IDS:
        assert cases[case_id]["earliest_gate"] == 5
    assert cases["MOE-24"]["earliest_gate"] == 6
    assert "MOE-24" not in GATE5_CASES


def _prestart_document():
    return {
        "terminal": "PRESTART_FAILED",
        "error_drained": 1,
        "cache_reservation": {
            "waiting": 0, "attempts": 2, "identity_reassignments": 0,
            "commits": 2, "prestart_failures": 1, "starts": 1, "pending": [],
        },
        "cores": [
            {"core_id": 0, "live_commands": 0, "instance_error": 0,
             "dma_idle": 1},
            {"core_id": 1, "live_commands": 0, "instance_error": 0,
             "dma_idle": 1},
        ],
        "instances": [
            {"instance": 1, "cores": {"0": {"completed": 3, "errored": 0,
                                             "cancelled": 6},
                                      "1": {"completed": 2, "errored": 0,
                                            "cancelled": 3}}},
            {"instance": 2, "cores": {"0": {"completed": 0, "errored": 0,
                                            "cancelled": 0},
                                      "1": {"completed": 0, "errored": 0,
                                            "cancelled": 0}}},
        ],
        "moe": {
            "cache_state": [
                {"core_id": 0, "live_tokens": 0, "live_obligations": 0,
                 "pending_fills": 0, "pending_subscribers": 0},
                {"core_id": 1, "live_tokens": 0, "live_obligations": 0,
                 "pending_fills": 0, "pending_subscribers": 0},
            ],
            "regions": [{"layer_id": 1, "region_id": 1, "issued": 0,
                         "completed": 0}],
        },
    }


def test_gate5_prestart_terminal_accepts_a_consistent_failure():
    assert prestart_terminal_gaps(_prestart_document()) == ()


def test_gate5_prestart_terminal_rejects_missing_evidence():
    """A deleted lifecycle/resource entry is never read as empty or zero."""
    def tampered(mutate):
        document = _prestart_document()
        mutate(document)
        return prestart_terminal_gaps(document)

    assert tampered(lambda d: d.pop("cores"))
    assert tampered(lambda d: d.pop("instances"))
    assert tampered(lambda d: d.pop("moe"))
    assert tampered(lambda d: d.pop("cache_reservation"))
    assert tampered(lambda d: d["moe"].pop("cache_state"))
    assert tampered(lambda d: d["moe"].pop("regions"))
    assert tampered(lambda d: d["instances"][-1].pop("cores"))
    assert tampered(lambda d: d["cores"][0].pop("live_commands"))
    assert tampered(lambda d: d["cores"][0].pop("instance_error"))
    assert tampered(lambda d: d["moe"]["cache_state"][0].pop("live_tokens"))
    assert tampered(lambda d: d["moe"]["cache_state"][0].pop(
        "pending_subscribers"))
    assert tampered(lambda d: d["cache_reservation"].pop("pending"))
    assert tampered(lambda d: d["cache_reservation"].pop("starts"))
    assert tampered(lambda d: d["instances"][-1]["cores"]["0"].pop(
        "completed"))
    assert tampered(lambda d: d["moe"]["regions"][0].pop("issued"))


def test_gate5_prestart_terminal_rejects_incomplete_evidence():
    """Instance identity, value ranges and DMA idle are required evidence."""
    def tampered(mutate):
        document = _prestart_document()
        mutate(document)
        return prestart_terminal_gaps(document, expected_cores=(0, 1),
                                      expected_regions=((1, 1),),
                                      expected_instances=2)

    assert tampered(lambda d: d["instances"][-1].pop("instance"))
    assert tampered(lambda d: d["instances"][-1].update(instance=1))
    assert tampered(lambda d: d["instances"][-1].update(instance=999))
    assert tampered(lambda d: d["instances"][0].update(instance=None))
    assert tampered(lambda d: d.update(error_drained=True))
    assert tampered(lambda d: d.update(error_drained=-1))
    assert tampered(lambda d: d["instances"][0]["cores"]["0"].update(
        completed=-1))
    assert tampered(lambda d: d["instances"][0]["cores"]["1"].update(
        cancelled=-4))
    assert tampered(lambda d: d["cores"][0].update(dma_idle=0))
    assert tampered(lambda d: d["cores"][0].pop("dma_idle"))
    assert tampered(lambda d: d["cores"][1].update(dma_idle=None))
    assert tampered(lambda d: d["moe"]["cache_state"][0].update(
        live_tokens=-1))
    assert tampered(lambda d: d["cache_reservation"].update(attempts=-2))
    assert tampered(lambda d: d["instances"].append(
        {"instance": 3, "cores": {"0": {"completed": 0, "errored": 0,
                                        "cancelled": 0},
                                  "1": {"completed": 0, "errored": 0,
                                        "cancelled": 0}}}))
    # Swapping identities must not move the executed ledger onto the undone
    # batch: the undone instance is identified by starts + 1, not by position.
    def swap(d):
        first, last = d["instances"][0], d["instances"][-1]
        first["instance"], last["instance"] = (last["instance"],
                                               first["instance"])
    assert tampered(swap)
    assert tampered(lambda d: d["instances"].reverse())
    assert tampered(lambda d: d["instances"][0].update(instance=2))
    assert tampered(lambda d: d["instances"][-1].update(instance=0))


def test_gate5_prestart_terminal_rejects_wrong_identities_and_types():
    def tampered(mutate):
        document = _prestart_document()
        mutate(document)
        return prestart_terminal_gaps(document, expected_cores=(0, 1),
                                      expected_regions=((1, 1),))

    assert tampered(lambda d: d["cores"][0].update(core_id=7))
    assert tampered(lambda d: d["cores"][1].update(core_id=0))
    assert tampered(lambda d: d["moe"]["cache_state"][0].update(core_id=5))
    assert tampered(lambda d: d["instances"][-1]["cores"].pop("1"))
    assert tampered(lambda d: d["moe"]["regions"].append(
        {"layer_id": 1, "region_id": 9, "issued": 0, "completed": 0}))
    assert tampered(lambda d: d["cores"][0].update(live_commands="0"))
    assert tampered(lambda d: d["moe"]["cache_state"][0].update(live_tokens=None))
    assert tampered(lambda d: d["cache_reservation"].update(starts="1"))
    assert tampered(lambda d: d["cache_reservation"].update(pending=None))
    assert tampered(lambda d: d["instances"].append(
        {"instance": 3, "cores": {"0": {"completed": 0, "errored": 0,
                                        "cancelled": 0},
                                  "1": {"completed": 0, "errored": 0,
                                        "cancelled": 0}}}))


def test_gate5_prestart_terminal_rejects_tampered_terminal_and_live_state():
    def tampered(mutate):
        document = _prestart_document()
        mutate(document)
        return prestart_terminal_gaps(document)

    assert tampered(lambda d: d.update(terminal="DONE"))
    assert tampered(lambda d: d.update(error_drained=0))
    assert tampered(lambda d: d["cache_reservation"].update(starts=2))
    assert tampered(lambda d: d["cache_reservation"].update(prestart_failures=0))
    assert tampered(lambda d: d["cache_reservation"].update(commits=4))
    assert tampered(lambda d: d["cores"][0].update(live_commands=1))
    assert tampered(lambda d: d["cores"][0].update(instance_error=1))
    assert tampered(lambda d: d["moe"]["cache_state"][0].update(live_tokens=1))
    assert tampered(lambda d: d["instances"][1]["cores"]["0"].update(
        completed=1))
    assert tampered(lambda d: d["moe"]["regions"][0].update(issued=1))
    assert tampered(lambda d: d["cache_reservation"].update(pending=[
        {"core_id": 0, "request_id": 1, "layers": [1]}]))
