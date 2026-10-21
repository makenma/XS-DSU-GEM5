import json
import copy
import os
import subprocess
from pathlib import Path

import yaml

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.architecture import load_arch
from mesh_ir.serving_profiles import apply_request_profile_keys
from mesh_ir.runtime_environment import embedded_python_path

REPO = Path(__file__).resolve().parents[4]
GEM5 = REPO / "build/AXI_MESH/gem5.opt"
PROBE = REPO / "tests/gem5/ai_mesh/gate6/serving_fault_probe.py"
FIXTURES = REPO / "tests/gem5/ai_mesh/fixtures/gate6"
ARCH_YAML = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"
WEIGHT_DIGEST = "d3c1ec48f8238e96c579ad087d218b8510ca81c9c15f9b05d97b99111fd789b2"
SIM_TICK_LIMIT = "10000000000"


def write_serving_observation_probe(work, arch, ranges, *, bytes_limit=4096):
    from mesh_ir.runtime_deployment import FabricDeployment
    for node, rows in FabricDeployment.from_arch(arch).memory_map.partition(ranges).items():
        (work / f'verify-{node}.txt').write_text(''.join(f'{address:#x} {size}\n' for address, size in rows))
    probe = work / 'probe.py'
    probe.write_text('import runpy\nimport m5.objects\nfrom pathlib import Path\n'
        'endpoint = m5.objects.NpuMemoryEndpoint\n'
        'def observed_endpoint(*args, **kwargs):\n'
        '    node = int(kwargs["adapter"].dst_node)\n'
        f'    path = Path({str(work)!r}) / f"verify-{{node}}.txt"\n'
        '    if path.exists():\n'
        f'        kwargs.update(verify_json=str(path), verify_bytes_limit={bytes_limit})\n'
        '    return endpoint(*args, **kwargs)\n'
        'm5.objects.NpuMemoryEndpoint = observed_endpoint\n'
        'core = m5.objects.MeshDummyCore\n'
        'def observed_core(*args, **kwargs):\n'
        f'    kwargs.update(compute_bytes_limit={bytes_limit})\n'
        '    return core(*args, **kwargs)\n'
        'm5.objects.MeshDummyCore = observed_core\n'
        f'runpy.run_path({str(PROBE)!r}, run_name="__main__")\n')
    return probe


def assert_serving_error_quiescent(result, metrics, facts, requests):
    assert facts.fatal is None
    assert result['terminal'] == 'ERROR_DRAINED'
    assert result['error_drained'] == 1
    assert not result['watchdog_fired']
    cq = [event for event in facts.events if event['kind'] == 'CQ_CONSUME']
    assert len(cq) == len(requests) == metrics['metadata_reads_validated']
    assert {event['request_id'] for event in cq} == set(requests)
    assert all(event['status'] == 'ERROR' for event in cq)
    assert metrics['frontend_drained'] == 1
    for field in ('live_contexts', 'live_cq_obligations', 'live_sq_intakes',
                  'live_submissions', 'msi_rob_entries'):
        assert metrics[field] == 0
    assert result['bridges']
    for bridge in result['bridges']:
        assert bridge['idle'] == 1
        for field in ('outstanding_reads', 'outstanding_writes', 'pending_aw', 'pending_ar'):
            assert bridge[field] == 0
        bursts = [row for row in result['burst_timings'] if row['core_id'] == bridge['core_id']]
        reads = [row for row in bursts if row['channel'] == 'AR']
        writes = [row for row in bursts if row['channel'] == 'AW']
        assert len(reads) == bridge['ar_accepted'] == bridge['read_bursts_completed']
        assert len(writes) == bridge['aw_accepted'] == bridge['b_consumed']
        assert len(writes) == bridge['write_bursts_completed']
        assert sum(row['beats'] for row in reads) == bridge['r_beats_consumed']
        assert sum(row['beats'] for row in writes) == bridge['w_accepted']
    assert all(row['response_tick'] >= row['ar_aw_tick'] > 0
               for row in result['burst_timings'])


def write_serving_fixture(work: Path, program, arch_path=ARCH_YAML) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    program = apply_request_profile_keys(program)
    arch = load_arch(arch_path)
    (work / "arch.yaml").write_text(Path(arch_path).read_text())
    verify_program(program, arch)
    blob = encode_program(program)
    verify_program(decode_program(blob), arch)
    (work / "program.mshb").write_bytes(blob)
    (work / "schedule.mesh.json").write_text(
        json.dumps(program.canonical_dict()), encoding="utf-8")
    (work / "expected_traffic.json").write_text(
        json.dumps(program.semantics.intrinsic_traffic.canonical_dict()), encoding="utf-8")
    key = hex(program.agent_request_profiles[0].requested_profile_key)
    workload = json.loads(
        (FIXTURES / "serving_workload_plan.json").read_text("utf-8"))
    workload["users"][0]["tasks"][0]["rounds"][0][
        "requested_profile_key"] = key
    (work / "workload.json").write_text(json.dumps(workload), encoding="utf-8")
    prefill = not program.agent_request_profiles[0].flags
    if prefill:
        from tests.integration.support.workspace_program import workspace_workload_document
        workload = workspace_workload_document(program)
        (work / "workload.json").write_text(json.dumps(workload), encoding="utf-8")
    surrogate = json.loads(
        (FIXTURES / "serving_surrogate_profiles.json").read_text("utf-8"))
    surrogate["profiles"][0]["profile_key"] = key
    if not prefill:
        (work / "surrogate.json").write_text(json.dumps(surrogate), encoding="utf-8")
    config = yaml.safe_load(
        (FIXTURES / "serving_runtime_config.yaml").read_text("utf-8"))
    config["agent"]["workload_plan"] = "workload.json"
    if prefill:
        config['serving']['output_chunk_bytes'] = program.agent_request_profiles[0].publish_chunk_bytes
        config['serving']['address_map']['workspace_arena'] = {'base': 0x800200000, 'bytes': 4096}
    (work / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return work


def run_serving_fixture(work: Path, mode: str, fault_index: int = -1,
                        entry=PROBE, rejection_code=None, weight_policy="streamed",
                        delay_read_cycles=0, fault_kind="write", extra_args=(),
                        expect_execution=True, fault_target_address=None,
                        extra_latencies=(), wire_patches=(), expected_fatal=None,
                        fault_core=0):
    out = work / mode
    out.mkdir(exist_ok=True)
    arch = load_arch(work / "arch.yaml")
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(("AI_MESH_", "GATE6_"))}
    env.update(
        PYTHONPATH=embedded_python_path(),
        AI_MESH_ARTIFACT_DIR=str(out),
        GATE6_FAULT_ENABLE=str(int(fault_index >= 0)),
        GATE6_FAULT_CORE=str(fault_core),
        GATE6_FAULT_KIND=fault_kind,
        GATE6_FAULT_INDEX=str(fault_index),
        GATE6_SCHEDULE_DIR=str(work),
        GATE6_EXTRA_LATENCIES=json.dumps(extra_latencies),
        GATE6_SUBMISSION_WIRE_PATCHES=json.dumps(wire_patches),
        GATE6_CORE_IDS=json.dumps(list(arch.core_ids)),
        GATE6_ARCH_DICT=json.dumps({
            "core_ids": list(arch.core_ids),
            "axi_data_bytes": arch.axi_data_bytes,
            "axi_max_burst_beats": arch.axi_max_burst_beats,
            "region_bases": [region.base for region in arch.regions],
            "region_tile_strides": [region.tile_stride or 0
                                    for region in arch.regions],
        }),
    )
    if delay_read_cycles:
        env.update(GATE6_DELAY_ENABLE="1", GATE6_DELAY_CORE="0", GATE6_DELAY_KIND="read",
                   GATE6_DELAY_INDEX="0", GATE6_DELAY_CYCLES=str(delay_read_cycles))
    if fault_target_address is not None:
        env['GATE6_FAULT_TARGET_ADDRESS'] = str(fault_target_address)
    argv = [
        str(GEM5), "--outdir=" + str(out), str(entry),
        "--case", "gate6_serving_e2e_d", "--master-seed", "20260901",
        "--arch", str(work / "arch.yaml"),
        "--sim-tick-limit", SIM_TICK_LIMIT,
        "--serving-program", str(work / "program.mshb"),
        "--runtime-config", str(work / "config.yaml"),
        "--weight-image-digest", WEIGHT_DIGEST,
        "--weight-policy", weight_policy,
    ]
    argv.extend(("--surrogate-profiles", str(work / "surrogate.json") if (work / "surrogate.json").exists() else ""))
    argv.extend(extra_args)
    with (out / "console.log").open("w") as handle:
        process = subprocess.run(argv, cwd=REPO, env=env, stdout=handle,
                                 stderr=subprocess.STDOUT, timeout=900, check=False)
    if rejection_code is not None:
        console = (out / "console.log").read_text()
        assert process.returncode != 0, console
        assert rejection_code in console, console
        assert not (out / "gate6_facts.tsv").exists()
        assert not (out / "mesh_result.json").exists()
        return None
    if expected_fatal:
        from mesh_ir.gate3_oracle import read_facts_document
        assert process.returncode != 0, (out / 'console.log').read_text()
        assert read_facts_document(out / 'gate6_facts.tsv').fatal['symbol'] == expected_fatal
    else:
        assert process.returncode == 0, (out / "console.log").read_text()
    facts = (out / "gate6_facts.tsv").read_text("utf-8")
    if expect_execution:
        result = json.loads((out / "mesh_result.json").read_text("utf-8"))
    else:
        assert not (out / "mesh_result.json").exists()
        result = None
    metrics = {}
    for line in facts.splitlines():
        fields = line.split("|")
        if fields[0] == "METRIC":
            metrics[fields[1]] = int(fields[2])
    return facts, result, metrics


def write_shared_fixture(work: Path, ranks=(0, 0), reverse=False, moe=False, output_tokens=1, program=None):
    from mesh_ir.architecture import load_arch
    from mesh_ir.serving_programs import ranked_full_view_serving_program, moe_full_view_serving_program
    arch = ARCH_YAML.with_name('mesh_1x2_moe.yaml') if moe else ARCH_YAML
    if program is None:
        program = (moe_full_view_serving_program if moe else ranked_full_view_serving_program)(load_arch(arch), output_tokens)
    work = write_serving_fixture(work, program, arch)
    workload_path = work / 'workload.json'
    workload = json.loads(workload_path.read_text())
    first = workload['users'][0]['tasks'][0]['rounds'][0]
    first.update(logical_source_rank=ranks[0], output_tokens=output_tokens,
                 generated_code_bytes=128 * output_tokens,
                 output_capacity_bytes=128 * output_tokens,
                 kv_required_tokens_after_round=8 + output_tokens)
    second = copy.deepcopy(workload['users'][0])
    second['user_id'] = 1
    task = second['tasks'][0]
    task['session_id'] = task['kv_handle'] = 2
    task['rounds'][0]['workload_plan_item_id'] = 2
    task['rounds'][0]['input_content_digest'] = '6d' * 32
    task['rounds'][0]['logical_source_rank'] = ranks[1]
    workload['users'].append(second)
    if reverse:
        workload['users'][0]['tasks'][0]['think_time_ns'] += 100
    workload_path.write_text(json.dumps(workload))
    surrogate_path = work / 'surrogate.json'
    surrogate = json.loads(surrogate_path.read_text())
    surrogate['profiles'][0].update(output_tokens=output_tokens, output_bytes=128 * output_tokens)
    surrogate_path.write_text(json.dumps(surrogate))
    config_path = work / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['agent']['users'] = 2
    config['serving'].update(max_active_sequences=2, batch_wait_cycles=100000)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    return program, work
