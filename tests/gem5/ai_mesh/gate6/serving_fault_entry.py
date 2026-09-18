"""Gate 6 R3 real-error execution on the serving path.

Injects one real AXI fault (or extra latency) into the E2E-D serving run at a
predicted mesh transaction UID and reports what the run produced, so the F5
error lifecycle can be checked on real hardware behaviour instead of component
stubs.
"""

from __future__ import annotations

import argparse
import json
import re
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "configs/example/ai_mesh"))
sys.path.insert(0, str(REPO / "util/mesh_ir"))

from dma_uid_predict import predict  # noqa: E402
from mesh_ir.abi.decoder import decode_program  # noqa: E402
from mesh_ir.agent_config import (  # noqa: E402
    load_agent_runtime_config,
    release_policy_of,
)
from mesh_ir.agent_workload import load_workload_plan  # noqa: E402
from mesh_ir.builder import load_arch  # noqa: E402
from mesh_ir.gate3_oracle import read_facts_document  # noqa: E402
from mesh_ir.serving_error_oracle import (  # noqa: E402
    frozen_request_identity,
    verify_error_drain,
)

GEM5 = REPO / "build/AXI_MESH/gem5.opt"
PROBE = REPO / "tests/gem5/ai_mesh/gate6/serving_fault_probe.py"
FIXTURES = REPO / "tests/gem5/ai_mesh/fixtures/gate6"
ARCH_YAML = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"
RUNTIME_CONFIG = FIXTURES / "serving_runtime_config.yaml"


def arch_dict(manifest) -> dict:
    return {
        "core_ids": list(manifest.core_ids),
        "axi_data_bytes": manifest.axi_data_bytes,
        "axi_max_burst_beats": manifest.axi_max_burst_beats,
        "region_bases": [region.base for region in manifest.regions],
        "region_tile_strides": [
            region.tile_stride if region.tile_stride else 0
            for region in manifest.regions
        ],
    }


def predicted_uids(program: str, core_ids) -> dict:
    schedule_dir = FIXTURES / ("%s_schedule" % program)
    schedule_dir.mkdir(exist_ok=True)
    (schedule_dir / "schedule.mesh.json").write_bytes(
        (FIXTURES / ("%s_schedule.mesh.json" % program)).read_bytes())
    manifest = load_arch(ARCH_YAML)
    src_nodes = {core_id: index for index, core_id in enumerate(core_ids)}
    return predict(schedule_dir, arch_dict(manifest), src_nodes)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--program", default="serving_two_tokens")
    parser.add_argument("--fault-uid", type=lambda value: int(value, 0),
                        default=0)
    parser.add_argument("--fault-core", type=int, default=0)
    parser.add_argument("--fault-kind", default="write")
    parser.add_argument("--fault-index", type=int, default=-1)
    parser.add_argument("--src-port", type=int, default=0)
    parser.add_argument("--debug-flags", default="")
    parser.add_argument("--expect-error", action="store_true")
    parser.add_argument("--expect-phases", type=int, default=2)
    parser.add_argument("--delay-core", type=int, default=-1)
    parser.add_argument("--delay-kind", default="write")
    parser.add_argument("--delay-index", type=int, default=-1)
    parser.add_argument("--delay-cycles", type=int, default=0)
    parser.add_argument("--expect-tick-greater", type=int, default=-1)
    parser.add_argument("--baseline-metadata-tick", type=int, default=-1)
    parser.add_argument("--baseline-semantic-digest", default="")
    parser.add_argument("--baseline-output-digest", default="")
    parser.add_argument("--delay-uid", default="")
    parser.add_argument("--tamper-instance-count", type=int, default=-1)
    parser.add_argument("--drop-binding-profile", action="store_true")
    parser.add_argument("--binding-extra-kind", type=int, default=-1)
    parser.add_argument("--expect-binding-reject", action="store_true")
    parser.add_argument("--arena-binding-undercount", action="store_true")
    parser.add_argument("--expect-allocation-reject", action="store_true")
    parser.add_argument("--expect-expectation-reject", action="store_true")
    parser.add_argument("--expect-runtime-instance-count", type=int,
                        default=-1)
    parser.add_argument("--arena-shift", type=lambda value: int(value, 0),
                        default=0)
    parser.add_argument("--expect-input-address", type=lambda value: int(value, 0),
                        default=-1)
    parser.add_argument("--expect-output-address", type=lambda value: int(value, 0),
                        default=-1)
    parser.add_argument("--expect-output-span", type=lambda value: int(value, 0),
                        default=-1)
    parser.add_argument("--expect-post-drain-error", action="store_true")
    parser.add_argument("--kv-policy-override", type=int, default=-1)
    parser.add_argument("--cached-tokens-override", type=int, default=-1)
    parser.add_argument("--item-id-override", type=int, default=-1)
    parser.add_argument("--host-wire-mode", default="")
    parser.add_argument("--expect-workload-mismatch", action="store_true")
    parser.add_argument("--expect-chunk-mismatch", action="store_true")
    parser.add_argument("--expect-profile-key-reject", action="store_true")
    parser.add_argument("--expect-reuse-reject", action="store_true")
    parser.add_argument("--expect-cached-reject", action="store_true")
    parser.add_argument("--expect-item-reject", action="store_true")
    parser.add_argument("--baseline-phases", type=int, default=0)
    parser.add_argument("--list-uids", action="store_true")
    arguments = parser.parse_args()

    manifest = load_arch(ARCH_YAML)
    uids = predicted_uids(arguments.program, manifest.core_ids)
    if arguments.list_uids:
        print(json.dumps({str(core): {kind: [hex(value) for value in values]
                                      for kind, values in per.items()}
                          for core, per in uids.items()}, indent=2))
        return 0

    outdir = Path(arguments.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for name in ("AI_MESH_CHILD_REPORT",):
        env.pop(name, None)
    env["AI_MESH_ARTIFACT_DIR"] = str(outdir)
    env["AI_MESH_CASE_ID"] = "E2E-D"
    env["AI_MESH_SUBCASE"] = "default"
    env["GATE6_FAULT_UID"] = hex(arguments.fault_uid) if arguments.fault_uid \
        else ""
    env["GATE6_FAULT_ENABLE"] = "1" if arguments.fault_index >= 0 else "0"
    env["GATE6_FAULT_CORE"] = str(arguments.fault_core)
    env["GATE6_FAULT_KIND"] = arguments.fault_kind
    env["GATE6_FAULT_INDEX"] = str(arguments.fault_index)
    env["GATE6_SCHEDULE_DIR"] = str(FIXTURES / ("%s_schedule"
                                                % arguments.program))
    env["GATE6_CORE_IDS"] = json.dumps(list(manifest.core_ids))
    env["GATE6_ARCH_DICT"] = json.dumps(arch_dict(manifest))
    env["GATE6_SRC_PORT"] = str(arguments.src_port)
    env["GATE6_DELAY_ENABLE"] = "1" if arguments.delay_index >= 0 else "0"
    env["GATE6_DELAY_CORE"] = str(arguments.delay_core)
    env["GATE6_DELAY_KIND"] = arguments.delay_kind
    env["GATE6_DELAY_INDEX"] = str(arguments.delay_index)
    env["GATE6_DELAY_UID"] = arguments.delay_uid
    env["GATE6_DELAY_CYCLES"] = str(arguments.delay_cycles)
    env["GATE6_BINDING_TAMPER_INSTANCE_COUNT"] = (
        str(arguments.tamper_instance_count)
        if arguments.tamper_instance_count >= 0 else "")
    env["GATE6_BINDING_DROP_PROFILE"] = (
        "1" if arguments.drop_binding_profile else "0")
    env["GATE6_ARENA_BINDING_UNDERCOUNT"] = (
        "1" if arguments.arena_binding_undercount else "0")
    env["GATE6_KV_POLICY_OVERRIDE"] = str(arguments.kv_policy_override)
    env["GATE6_CACHED_TOKENS_OVERRIDE"] = str(arguments.cached_tokens_override)
    env["GATE6_ITEM_ID_OVERRIDE"] = str(arguments.item_id_override)
    env["GATE6_HOST_WIRE_MODE"] = arguments.host_wire_mode
    if arguments.host_wire_mode:
        wire_config = load_agent_runtime_config(RUNTIME_CONFIG)
        env["GATE6_SERVING_WORKLOAD_PLAN"] = str(
            RUNTIME_CONFIG.parent /
            wire_config.document["agent"]["workload_plan"])
    env["GATE6_POST_DRAIN_ERROR"] = (
        "1" if arguments.expect_post_drain_error else "0")
    env["GATE6_ARENA_SHIFT"] = str(arguments.arena_shift)
    env["GATE6_BINDING_EXTRA_KIND"] = (
        str(arguments.binding_extra_kind)
        if arguments.binding_extra_kind >= 0 else "")
    argv = [
        str(GEM5), "--outdir=" + str(outdir),
        *(["--debug-flags=" + arguments.debug_flags,
           "--debug-file=" + str((outdir / "debug.log").resolve())]
          if arguments.debug_flags else []),
        str(PROBE),
        "--case", "gate6_serving_e2e_d", "--master-seed", "20260901",
        "--sim-tick-limit", "10000000000",
        "--serving-program",
        str(FIXTURES / ("%s.mshb" % arguments.program)),
        "--runtime-config", str(RUNTIME_CONFIG),
        "--surrogate-profiles",
        str(FIXTURES / "serving_surrogate_profiles.json"),
        "--weight-image-digest",
        os.environ["GATE6_WEIGHT_DIGEST"],
    ]

    with (outdir / "console.log").open("w") as handle:
        process = subprocess.run(argv, env=env, stdout=handle,
                                 stderr=subprocess.STDOUT, timeout=1800)
    console = (outdir / "console.log").read_text()
    result_path = outdir / "mesh_result.json"
    result = json.loads(result_path.read_text()) if result_path.exists() else {}
    facts = (outdir / "gate6_facts.tsv")
    facts_text = facts.read_text() if facts.exists() else ""
    phases = facts_text.count("SERVING_PHASE")
    failures = []
    if process.returncode != 0 and not (
            arguments.expect_expectation_reject or
            arguments.expect_allocation_reject or
            arguments.expect_reuse_reject or
            arguments.expect_cached_reject or
            arguments.expect_item_reject or
            arguments.expect_workload_mismatch or
            arguments.expect_chunk_mismatch or
            arguments.expect_profile_key_reject):
        failures.append("gem5 exit code %d" % process.returncode)
    exit_line = next((line for line in console.splitlines()
                      if line.startswith("Exiting @")), "")
    exit_tick = 0
    if exit_line.startswith("Exiting @ tick "):
        exit_tick = int(exit_line.split()[3])
    metadata_tick = 0
    for line in facts_text.splitlines():
        if line.startswith("EVENT|") and "METADATA_READ" in line:
            metadata_tick = int(line.split("|")[1])
            break
    semantic = (result.get("output_span") or {}).get("semantic_digest", "")
    output_digest = ""
    output_read = None
    for line in facts_text.splitlines():
        if "|LOCAL_READ|GENERATED_CODE_READ|" in line:
            fields = line.split("|")
            output_digest = fields[-1]
            match = re.search(r"\|(\d+)\|-\|-\|(\d+)\|-\|", line)
            if match:
                output_read = (int(match.group(1)), int(match.group(2)))
            break
    validated = 0
    for line in facts_text.splitlines():
        if line.startswith("METRIC|metadata_reads_validated|"):
            validated = int(line.split("|")[2])
    if arguments.baseline_metadata_tick >= 0:
        if metadata_tick <= arguments.baseline_metadata_tick:
            failures.append("metadata did not move with the delay")
        if arguments.baseline_semantic_digest and \
                semantic != arguments.baseline_semantic_digest:
            failures.append("delayed run changed the semantic content digest: "
                            "%s != %s" % (semantic,
                                          arguments.baseline_semantic_digest))
        if arguments.baseline_output_digest and \
                output_digest != arguments.baseline_output_digest:
            failures.append("delayed run changed the Host output payload "
                            "digest: %s != %s"
                            % (output_digest,
                               arguments.baseline_output_digest))
        if validated != 1:
            failures.append("the Host did not validate the mesh metadata "
                            "digest against its own expectation (%d)" % validated)
    if arguments.expect_tick_greater >= 0:
        if exit_tick <= arguments.expect_tick_greater:
            failures.append("delayed run exited at %d, not after %d"
                            % (exit_tick, arguments.expect_tick_greater))
        if result.get("terminal") != "DONE":
            failures.append("delayed run did not reach the DONE terminal")
    if arguments.expect_error:
        if result.get("terminal") != "ERROR_DRAINED":
            failures.append("dispatcher terminal is not ERROR_DRAINED")
        if result.get("error_drained") != 1:
            failures.append("dispatcher did not record the error drain")
        if phases != arguments.expect_phases:
            failures.append("committed %d serving phases, expected %d"
                            % (phases, arguments.expect_phases))
        if "BUSINESS_FAILED" not in facts_text:
            failures.append("no failed task terminal was recorded")
    elif arguments.expect_tick_greater >= 0:
        if result.get("terminal") != "DONE":
            failures.append("delayed run did not reach the DONE terminal")
        if phases != arguments.expect_phases:
            failures.append("committed %d serving phases, expected %d"
                            % (phases, arguments.expect_phases))
        if result.get("watchdog_fired"):
            failures.append("watchdog fired")
    if "GENERATE_TERMINAL_LATCHED" not in facts_text and not (
            arguments.expect_allocation_reject or
            arguments.expect_reuse_reject or
            arguments.expect_cached_reject or
            arguments.expect_item_reject or
            arguments.expect_workload_mismatch or
            arguments.expect_chunk_mismatch or
            arguments.expect_profile_key_reject):
        failures.append("no generate terminal was latched")
    prompt_dma = [line for line in facts_text.splitlines()
                  if line.startswith("EVENT|") and
                  "|LOCAL_READ|PROMPT|" in line]
    if prompt_dma:
        failures.append("the serving path still pulls the prompt payload "
                        "over its own DMA")
    output_read = None
    for line in facts_text.splitlines():
        if "|LOCAL_READ|GENERATED_CODE_READ|" in line:
            match = re.search(r"\|(\d+)\|-\|-\|(\d+)\|-\|",
                              line)
            if match:
                output_read = (int(match.group(1)), int(match.group(2)))
            break
    if output_read is not None and arguments.expect_output_span >= 0:
        if output_read[0] != arguments.expect_output_span:
            failures.append("the Host read the output backing at %d instead "
                            "of the request output binding %d"
                            % (output_read[0], arguments.expect_output_span))
        if output_read[1] != 256:
            failures.append("the Host output read covered %d bytes"
                            % output_read[1])
    if arguments.expect_input_address >= 0 or arguments.expect_output_address >= 0:
        burst = result.get("burst_timings") or []
        addresses = {row.get("address") for row in burst}
        if arguments.expect_input_address >= 0 and \
                arguments.expect_input_address not in addresses:
            failures.append("the mesh input load did not use the request "
                            "binding %d (saw %s)"
                            % (arguments.expect_input_address,
                               sorted(addresses)[:4]))
        if arguments.expect_output_address >= 0:
            stores = [row for row in burst if row.get("channel") == "AW"]
            published = {row["address"] for row in stores}
            if arguments.expect_output_address not in published:
                failures.append("the mesh did not publish to the request "
                                "output binding %d (saw %s)"
                                % (arguments.expect_output_address,
                                   sorted(published)))
            if arguments.arena_shift:
                old = arguments.expect_output_address - arguments.arena_shift
                for row in stores:
                    if old <= row["address"] < old + 256:
                        failures.append("the mesh still published to the "
                                        "pre-shift address %d" % row["address"])
    if result.get("watchdog_fired") and arguments.expect_error:
        failures.append("watchdog fired")
    if arguments.expect_allocation_reject:
        if "overruns the Host allocation" not in console:
            failures.append("the Host did not reject the undersized "
                            "parameter allocation")
        if validated != 0 or phases != 0 or metadata_tick != 0:
            failures.append("the undersized allocation still admitted work")
        if process.returncode == 0:
            failures.append("the undersized allocation did not stop the run")
    if arguments.expect_post_drain_error:
        runtime_config = load_agent_runtime_config(RUNTIME_CONFIG)
        workload = load_workload_plan(
            RUNTIME_CONFIG.parent /
            runtime_config.document["agent"]["workload_plan"])
        frozen_request, frozen_cookie = frozen_request_identity(
            workload, release_policy_of(runtime_config))
        failures.extend(verify_error_drain(
            result,
            decode_program((FIXTURES / ("%s.mshb" % arguments.program)).
                           read_bytes()),
            list(manifest.core_ids),
            arguments.fault_core,
            phases,
            read_facts_document(facts).events if facts.exists() else [],
            frozen_request,
            frozen_cookie,
        ))
    if arguments.expect_binding_reject:
        if validated != 0:
            failures.append("a rejected binding proof still produced %d "
                            "validated metadata records" % validated)
        if "PARAMETER_REJECT|PARAMETER" not in facts_text or \
                "E_BINDING_ROLE" not in facts_text:
            failures.append("no E_BINDING_ROLE parameter rejection was "
                            "recorded")
        if phases != 0:
            failures.append("the rejected request still committed %d phases"
                            % phases)
        if metadata_tick != 0:
            failures.append("the rejected request still published metadata")
    if (arguments.expect_reuse_reject or arguments.expect_cached_reject or
            arguments.expect_item_reject):
        if arguments.expect_reuse_reject:
            expected_detail = "E_KV_FLAG_COMBINATION"
        elif arguments.expect_cached_reject:
            expected_detail = "E_KV_TOKEN_MISMATCH"
        else:
            expected_detail = "E_REQUEST_PROFILE"
        if process.returncode == 0 and (
                "PARAMETER_REJECT|PARAMETER" not in facts_text or
                expected_detail not in facts_text):
            failures.append("the frozen-projection mismatch was neither "
                            "rejected before the run nor by %s"
                            % expected_detail)
        if phases != 0:
            failures.append("the rejected request still committed %d phases"
                            % phases)
        if any(line.split("|")[3] == "CORE_START"
               for line in facts_text.splitlines() if line.startswith("EVENT|")):
            failures.append("the rejected request still started a core")
        if "GENERATED_CODE_READ" in facts_text:
            failures.append("the rejected request still pulled payload")
        for line in facts_text.splitlines():
            if line.startswith("METRIC|kv_session_records|") and \
                    int(line.split("|")[2]) != 0:
                failures.append("the rejected request still admitted a KV "
                                "session")
            if line.startswith("METRIC|core_starts|") and \
                    int(line.split("|")[2]) != 0:
                failures.append("the rejected request still started a core")
    if arguments.expect_workload_mismatch or \
            arguments.expect_chunk_mismatch or \
            arguments.expect_profile_key_reject:
        expected_detail = (
            "E_OUTPUT_CHUNK_MISMATCH"
            if arguments.expect_chunk_mismatch else
            "E_REQUEST_PROFILE_KEY"
            if arguments.expect_profile_key_reject else
            "E_WORKLOAD_PLAN_MISMATCH")
        if process.returncode != 0:
            failures.append("the wire mismatch escalated to exit %d"
                            % process.returncode)
        if "PARAMETER_REJECT|PARAMETER" not in facts_text or \
                expected_detail not in facts_text:
            failures.append("no %s rejection was recorded" % expected_detail)
        if "FATAL|" in facts_text or "fatal:" in console:
            failures.append("the request-level rejection escalated to an "
                            "infrastructure fatal")
        if phases != 0:
            failures.append("the rejected request still committed %d phases"
                            % phases)
        if any(line.split("|")[3] == "CORE_START"
               for line in facts_text.splitlines() if line.startswith("EVENT|")):
            failures.append("the rejected request still started a core")
        if "GENERATED_CODE_READ" in facts_text:
            failures.append("the rejected request still pulled payload")
        for line in facts_text.splitlines():
            if line.startswith("METRIC|kv_session_records|") and \
                    int(line.split("|")[2]) != 0:
                failures.append("the rejected request still admitted a KV "
                                "session")
        if not any(line.startswith("EVENT|") and "CQ_ASSIGN" in line and
                   line.rstrip().endswith("ERROR")
                   for line in facts_text.splitlines()):
            failures.append("the rejected request produced no ERROR CQ")
    if arguments.expect_runtime_instance_count >= 0:
        runtime_count = -1
        for line in facts_text.splitlines():
            if line.startswith("METRIC|serving_instance_count|"):
                runtime_count = int(line.split("|")[2])
        if runtime_count != arguments.expect_runtime_instance_count:
            failures.append("the runtime reported instance count %d, not the "
                            "forged %d" % (runtime_count,
                                           arguments.expect_runtime_instance_count))
    if arguments.expect_expectation_reject:
        if validated != 0:
            failures.append("the Host validated %d metadata records" % validated)
        rejected = ("FATAL|%s" % "E_AGENT_PROTOCOL_FATAL") in facts_text or (
            "PARAMETER_REJECT|PARAMETER" in facts_text and
            "E_BINDING_ROLE" in facts_text)
        if not rejected:
            failures.append("the Host did not reject the tampered frozen "
                            "expectation")
        if arguments.baseline_phases and phases != arguments.baseline_phases:
            failures.append("tampered plan changed the committed phases from "
                            "%d to %d" % (arguments.baseline_phases, phases))
    summary = {
        "exit_code": process.returncode,
        "exit_line": next((line for line in console.splitlines()
                           if line.startswith("Exiting @")), None),
        "fatal": [line for line in console.splitlines()
                  if line.startswith("fatal:")],
        "exit_tick": exit_tick,
        "metadata_tick": metadata_tick,
        "semantic_digest": semantic,
        "serving_phases": phases,
        "terminal": result.get("terminal"),
        "error_drained": result.get("error_drained"),
        "watchdog_fired": result.get("watchdog_fired"),
        "pass": not failures,
        "failures": failures,
    }
    (outdir / "probe_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
