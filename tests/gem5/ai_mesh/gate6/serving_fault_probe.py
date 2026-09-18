import json
import os
import sys
import types
from pathlib import Path

root = Path.cwd()
for extra in ("configs", "configs/example/ai_mesh"):
    sys.path.insert(0, str(root / extra))

import argparse
from dummy_core_case_registry import invocation

parser = argparse.ArgumentParser()
parser.add_argument("--case", required=True)
parser.add_argument("--master-seed", required=True)
parser.add_argument("--sim-tick-limit", required=True)
arguments, remainder = parser.parse_known_args()

source_path = root / "configs/example/ai_mesh/run_gate6_serving.py"
runner = types.ModuleType("gate6_serving_fault_runner")
runner.__file__ = str(source_path)
sys.modules[runner.__name__] = runner
source = source_path.read_text()
assert source.rstrip().endswith("main()")
exec(compile(source.rsplit("main()", 1)[0], str(source_path), "exec"),
     runner.__dict__)

from dma_uid_predict import predict
from gate6_serving_scenario import AGENT_INITIATOR_COUNT

fault_core = int(os.environ.get("GATE6_FAULT_CORE", "0"))
fault_kind = os.environ.get("GATE6_FAULT_KIND", "write")
fault_index = int(os.environ.get("GATE6_FAULT_INDEX", "0"))
fault_uid = int(os.environ.get("GATE6_FAULT_UID") or "0", 0)
fault_resp = os.environ.get("GATE6_FAULT_RESP", "slverr")
delay_uid = os.environ.get("GATE6_DELAY_UID", "")
delay_cycles = int(os.environ.get("GATE6_DELAY_CYCLES", "0"))

original_scenario = runner.combined_scenario


def injected_scenario(*args, **kwargs):
    global fault_uid, delay_uid
    scenario = original_scenario(*args, **kwargs)
    target = scenario["target_ranges"][-1]["dst_node"]
    if (not fault_uid and os.environ.get("GATE6_SCHEDULE_DIR")
            and os.environ.get("GATE6_FAULT_ENABLE") == "1"):
        mesh = scenario["endpoint_to_router"]["initiators"][
            AGENT_INITIATOR_COUNT:]
        core_ids = json.loads(os.environ["GATE6_CORE_IDS"])
        src_nodes = {core_id: mesh[index]["src_node"]
                     for index, core_id in enumerate(core_ids)}
        arch = json.loads(os.environ["GATE6_ARCH_DICT"])
        predicted = predict(Path(os.environ["GATE6_SCHEDULE_DIR"]), arch,
                            src_nodes,
                            src_port=int(os.environ.get("GATE6_SRC_PORT", "0")))
        resolved = predicted[fault_core][fault_kind][fault_index]
        globals()["fault_uid"] = resolved
    if fault_uid:
        scenario["mesh_planned_faults"].append(
            {"target": target, "uid": fault_uid, "resp": fault_resp})
    if (not delay_uid and os.environ.get("GATE6_SCHEDULE_DIR")
            and os.environ.get("GATE6_DELAY_ENABLE") == "1"):
        mesh = scenario["endpoint_to_router"]["initiators"][
            AGENT_INITIATOR_COUNT:]
        core_ids = json.loads(os.environ["GATE6_CORE_IDS"])
        src_nodes = {core_id: mesh[index]["src_node"]
                     for index, core_id in enumerate(core_ids)}
        arch = json.loads(os.environ["GATE6_ARCH_DICT"])
        predicted = predict(Path(os.environ["GATE6_SCHEDULE_DIR"]), arch,
                            src_nodes,
                            src_port=int(os.environ.get("GATE6_SRC_PORT", "0")))
        delay_uid = hex(predicted[int(os.environ["GATE6_DELAY_CORE"])][
            os.environ["GATE6_DELAY_KIND"]][int(os.environ["GATE6_DELAY_INDEX"])])
    if delay_uid:
        scenario["mesh_planned_extra_latency"].append(
            {"target": target, "uid": int(delay_uid, 0),
             "cycles": delay_cycles})
    dump = os.environ.get("GATE6_SCENARIO_DUMP")
    if dump:
        Path(dump).write_text(json.dumps(
            {"scenario": scenario, "resolved_uid": globals().get("fault_uid")},
            indent=2) + "\n")
    return scenario


runner.combined_scenario = injected_scenario

binding_tamper = os.environ.get("GATE6_BINDING_TAMPER_INSTANCE_COUNT", "")
binding_drop = os.environ.get("GATE6_BINDING_DROP_PROFILE") == "1"
binding_extra_kind = os.environ.get("GATE6_BINDING_EXTRA_KIND", "")
if binding_tamper or binding_drop or binding_extra_kind:
    import dataclasses

    original_plans = runner.build_host_binding_plans
    tampered_count = int(binding_tamper) if binding_tamper else 0
    extra_kind = int(binding_extra_kind) if binding_extra_kind else 0

    def tampered_plans(program, region_bases):
        plans = original_plans(program, region_bases)
        if binding_drop:
            return tuple(plan for plan in plans
                         if plan.profile_id != plans[0].profile_id)
        if extra_kind:
            clone = None
            for requirement in plans[0].requirements:
                if requirement.kind == extra_kind:
                    clone = dataclasses.replace(
                        requirement, symbol_id=9001,
                        platform_address=requirement.platform_address +
                            requirement.platform_bytes + 0x100000)
            return tuple(
                dataclasses.replace(
                    plan,
                    requirements=tuple(list(plan.requirements) + [clone]))
                for plan in plans)
        return tuple(dataclasses.replace(plan, instance_count=tampered_count)
                     for plan in plans)

    runner.build_host_binding_plans = tampered_plans

if os.environ.get("GATE6_ARENA_BINDING_UNDERCOUNT") == "1":
    import mesh_ir.agent_plan_image as plan_image_module

    original_arena = plan_image_module.build_host_arena_object_plan

    def undersized_arena(workload, control, policy, regions,
                         binding_counts=None):
        plan = original_arena(workload, control, policy, regions,
                              binding_counts)
        for record in plan["records"]:
            if record.get("arena_kind") == "PARAMETER":
                record["allocation_bytes"] = 160
                record["initial_valid_bytes"] = min(
                    record["initial_valid_bytes"], 160)
        return plan

    plan_image_module.build_host_arena_object_plan = undersized_arena

policy_override = int(os.environ.get("GATE6_KV_POLICY_OVERRIDE", "-1"))
cached_override = int(os.environ.get("GATE6_CACHED_TOKENS_OVERRIDE", "-1"))
item_override = int(os.environ.get("GATE6_ITEM_ID_OVERRIDE", "-1"))
if policy_override >= 0 or cached_override >= 0 or item_override >= 0:
    import struct

    import mesh_ir.agent_plan_image as plan_image_module

    original_round = plan_image_module._round

    def overridden_round(round_):
        data = bytearray(original_round(round_))
        if policy_override >= 0:
            data[6] = policy_override
        if cached_override >= 0:
            struct.pack_into("<I", data, 28, cached_override)
        if item_override >= 0:
            struct.pack_into("<I", data, 0, item_override)
        return bytes(data)

    plan_image_module._round = overridden_round

arena_shift = int(os.environ.get("GATE6_ARENA_SHIFT", "0"))
if arena_shift:
    original_config = runner.load_agent_runtime_config

    def shifted_config(path):
        config = original_config(path)
        address_map = config.document["serving"]["address_map"]
        for arena in ("input_arena", "output_arena", "metadata_arena"):
            address_map[arena]["base"] += arena_shift
        return config

    runner.load_agent_runtime_config = shifted_config

host_wire_mode = os.environ.get("GATE6_HOST_WIRE_MODE", "")
if host_wire_mode:
    import dataclasses
    import hashlib
    import struct
    import types

    from mesh_ir.agent_plan_image import (_SECTION_ARENA,
                                           _SECTION_REQUEST_BINDINGS,
                                           _SECTION_SURROGATE,
                                           _round,
                                           decode_request_binding_section,
                                           encode_request_binding_section)
    from mesh_ir.agent_workload import load_workload_plan

    WIRE_MUTATIONS = {
        "control", "input_digest", "workload_digest", "item", "session_id",
        "program_id", "deadline", "chunk", "chunk_zero", "binding_bytes",
        "profile_key",
    }

    original_driver = runner.AgentAxiDriver

    def driver_with_wire_override(*args, **kwargs):
        canonical_path = Path(kwargs["plan_image"])
        blob = bytearray(canonical_path.read_bytes())
        workload = load_workload_plan(
            Path(os.environ["GATE6_SERVING_WORKLOAD_PLAN"]))
        task = workload.users[0].tasks[0]
        round_ = task.rounds[0]
        modes = set() if host_wire_mode == "control" else set(
            host_wire_mode.split("+"))
        assert modes <= WIRE_MUTATIONS, modes - WIRE_MUTATIONS
        canonical_round = _round(round_)
        offset = blob.find(canonical_round)
        assert offset >= 0 and blob.find(canonical_round, offset + 1) == -1

        def splice_round(**changes):
            replacement = _round(dataclasses.replace(round_, **changes))
            assert len(replacement) == len(canonical_round)
            blob[offset:offset + len(replacement)] = replacement

        if "input_digest" in modes:
            splice_round(input_content_digest="ab" * 32)
        if "workload_digest" in modes:
            blob[12:44] = bytes.fromhex("ab" * 32)
        if "item" in modes:
            splice_round(workload_plan_item_id=999)
        if "program_id" in modes:
            splice_round(program_id=2)
        if "deadline" in modes:
            splice_round(deadline_tick=12345)
        if "profile_key" in modes:
            splice_round(requested_profile_key=
                         round_.requested_profile_key + 1)
        if "session_id" in modes:
            header = struct.pack("<IQQQIHI", task.task_seq,
                                 task.think_time_ns, task.session_id,
                                 task.kv_handle,
                                 task.initial_kv_generation,
                                 task.effective_max_repair_rounds,
                                 len(task.rounds))
            task_offset = blob.find(header)
            assert task_offset >= 0 and \
                blob.find(header, task_offset + 1) == -1
            blob[task_offset + 12:task_offset + 20] = \
                struct.pack("<Q", task.session_id + 0xAB)
        section_cursor = 76
        for _ in range(struct.unpack_from("<I", blob, 8)[0]):
            section_type = struct.unpack_from(
                "<H", blob, section_cursor)[0]
            length = struct.unpack_from(
                "<Q", blob, section_cursor + 2)[0]
            section_cursor += 10
            if section_type == _SECTION_REQUEST_BINDINGS and (
                    "program_id" in modes or "binding_bytes" in modes):
                decoded = decode_request_binding_section(
                    bytes(blob[section_cursor:section_cursor + length]))
                assert len(decoded["plans"]) == 1
                if "program_id" in modes:
                    decoded["plans"][0]["program_id"] = 2
                if "binding_bytes" in modes:
                    weight = next(requirement for requirement in
                                  decoded["plans"][0]["requirements"]
                                  if requirement["kind"] == 4)
                    weight["platform_bytes"] += 1
                plans = [
                    types.SimpleNamespace(
                        program_id=plan["program_id"],
                        profile_id=plan["profile_id"],
                        primary_input_symbol_id=plan[
                            "primary_input_symbol_id"],
                        primary_output_symbol_id=plan[
                            "primary_output_symbol_id"],
                        primary_kv_symbol_id=plan["primary_kv_symbol_id"],
                        instance_count=plan["instance_count"],
                        requirements=[
                            types.SimpleNamespace(**requirement)
                            for requirement in plan["requirements"]
                        ],
                    )
                    for plan in decoded["plans"]
                ]
                encoded = encode_request_binding_section(
                    decoded["kv_session_slot_bytes"], plans)
                assert len(encoded) == length
                blob[section_cursor:section_cursor + length] = encoded
            if section_type == _SECTION_SURROGATE and (
                    "chunk" in modes or "chunk_zero" in modes):
                assert struct.unpack_from(
                    "<I", blob, section_cursor + 32)[0]
                blob[section_cursor + 80:section_cursor + 84] = \
                    struct.pack("<I", 0 if "chunk_zero" in modes else 128)
            if section_type == _SECTION_ARENA and "deadline" in modes:
                count = struct.unpack_from("<I", blob, section_cursor)[0]
                record_cursor = section_cursor + 4
                for _ in range(count):
                    if struct.unpack_from(
                            "<B", blob, record_cursor + 11)[0] == 1:
                        for field_offset in (32, 40):
                            value = struct.unpack_from(
                                "<Q", blob, record_cursor + field_offset)[0]
                            struct.pack_into("<Q", blob,
                                             record_cursor + field_offset,
                                             value + 16)
                    record_cursor += 52
            section_cursor += length
        blob[-32:] = hashlib.sha256(blob[:-32]).digest()
        host_path = canonical_path.parent / "host_wire_image.bin"
        host_path.write_bytes(blob)
        kwargs["plan_image"] = str(host_path)
        return original_driver(*args, **kwargs)

    runner.AgentAxiDriver = driver_with_wire_override

_script, child_argv = invocation(arguments.case, remainder,
                                 arguments.sim_tick_limit)
sys.argv = child_argv
runner.main()
