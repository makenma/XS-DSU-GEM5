"""Independent oracle for one Gate 6 R3 serving run (E2E-D).

Recomputes the executed descriptor closure, per-phase byte equations and the
agent terminal ledger from the immutable fixture program plus the runtime
artifacts.  It never trusts the artifact's own labels: any missing, extra or
mutated descriptor/byte/ledger entry is rejected.
"""

from __future__ import annotations

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from mesh_ir.serving_oracle import generate_phase_plan

_READ_KINDS = (A.DMA_KIND.LOAD, A.DMA_KIND.PREFETCH)
_WRITE_KINDS = (A.DMA_KIND.STORE, A.DMA_KIND.P2P_PUSH)


def _fail(code: str, detail: str, **context) -> None:
    raise MeshIrError(code, detail, **context)


def _owning_profile(program, command_id):
    for range_ in program.profile_stream_ranges:
        begin = range_.command_begin
        end = begin + range_.command_count
        for index in range(begin, end):
            if program.commands[index].command_id == command_id:
                return range_.profile_id
    _fail("E_BINDING_ROLE", "command has no owning profile", command=command_id)


def _bytes_of(row):
    if row["dma_kind"] in _READ_KINDS:
        return row["read_bytes"]
    if row["dma_kind"] in _WRITE_KINDS:
        return row["write_bytes"]
    return row["fill_bytes"]


def verify_serving_run(program, transport_rows, metrics,
                      serving_phases=None) -> dict:
    if not program.profile_stream_ranges:
        _fail("E_BINDING_ROLE", "program has no profile execution ranges")
    by_id = {}
    for row in transport_rows:
        descriptor_id = row["descriptor_id"]
        if descriptor_id in by_id:
            _fail("E_TRAFFIC_MISMATCH", "duplicate descriptor row", descriptor=descriptor_id)
        by_id[descriptor_id] = row
    plan_ids = {descriptor.descriptor_id for descriptor in
                program.dma_descriptors}
    if set(by_id) != plan_ids:
        _fail("E_TRAFFIC_MISMATCH", "executed descriptor set differs from the program",
              missing=sorted(plan_ids - set(by_id)),
              extra=sorted(set(by_id) - plan_ids))
    commands = {command.command_id: command for command in program.commands}
    phase_of = {}
    for descriptor in program.dma_descriptors:
        row = by_id[descriptor.descriptor_id]
        if row["dma_kind"] != descriptor.kind:
            _fail("E_TRAFFIC_MISMATCH", "descriptor kind mismatch",
                  descriptor=descriptor.descriptor_id)
        if row["error_code"] != 0:
            _fail("E_TRAFFIC_MISMATCH", "descriptor reported an error",
                  descriptor=descriptor.descriptor_id,
                  error_code=row["error_code"])
        committed = _bytes_of(row)
        if committed != descriptor.useful_bytes:
            _fail("E_TRAFFIC_MISMATCH", "descriptor committed bytes differ from the plan",
                  descriptor=descriptor.descriptor_id,
                  committed=committed, planned=descriptor.useful_bytes)
        if commands[descriptor.command_id].command_id not in commands:
            _fail("E_TRAFFIC_MISMATCH", "descriptor command is missing",
                  descriptor=descriptor.descriptor_id)
        profile = _owning_profile(program, descriptor.command_id)
        phase_of.setdefault(profile, []).append(descriptor.descriptor_id)
    expected_profiles = [range_.profile_id
                         for range_ in program.profile_stream_ranges]
    if sorted(phase_of) != sorted(expected_profiles):
        _fail("E_TRAFFIC_MISMATCH", "not every profile executed",
              executed=sorted(phase_of), expected=sorted(expected_profiles))

    request = program.agent_request_profiles[0]
    steps = {step.instance_profile_id: step for step in
             generate_phase_plan(program, request,
                                 A.PATH_KIND.INITIAL_PREFILL)}
    for instance in program.agent_instance_profiles:
        step = steps.get(instance.instance_profile_id)
        if step is None:
            continue
        commands_in_profile = set(phase_of[instance.mesh_profile_id])
        kv_loads = [descriptor for descriptor in program.dma_descriptors
                    if descriptor.descriptor_id in commands_in_profile and
                    descriptor.kind == A.DMA_KIND.LOAD and
                    descriptor.src.tensor_id == _kv_tensor(program)]
        read_bytes = sum(descriptor.row_bytes for descriptor in kv_loads)
        if read_bytes != step.kv_read_bytes_per_member:
            _fail("E_KV_TOKEN_MISMATCH",
                  "phase KV read window differs from the plan",
                  instance=instance.instance_profile_id,
                  read=read_bytes, planned=step.kv_read_bytes_per_member)

    phases = [] if serving_phases is None else serving_phases
    plan = [
        (instance, steps[instance.instance_profile_id])
        for instance in program.agent_instance_profiles
        if instance.instance_profile_id in steps
    ]
    if len(phases) != len(plan):
        _fail("E_KV_TOKEN_MISMATCH",
              "executed phase count differs from the plan",
              executed=len(phases), planned=len(plan))
    for index, (phase, (instance, step)) in enumerate(zip(phases, plan)):
        if (phase["phase"] != step.phase or
                phase["instance_profile_id"] != step.instance_profile_id or
                phase["mesh_profile_id"] != instance.mesh_profile_id):
            _fail("E_KV_TOKEN_MISMATCH", "phase identity differs from the plan",
                  ordinal=index, phase=phase["phase"], planned=step.phase)
        appended = step.cached_tokens_after - step.kv_tokens_before
        if (phase["kv_tokens_before"] != step.kv_tokens_before or
                phase["append_tokens"] != appended):
            _fail("E_KV_TOKEN_MISMATCH",
                  "phase KV token accounting differs from the plan",
                  ordinal=index, cached=phase["kv_tokens_before"],
                  planned=step.kv_tokens_before, appended=phase["append_tokens"],
                  planned_append=appended)
        if phase["accepted_descriptors"] != phase["terminal_descriptors"]:
            _fail("E_TRAFFIC_MISMATCH",
                  "phase accepted descriptors did not all terminate",
                  ordinal=index, accepted=phase["accepted_descriptors"],
                  terminal=phase["terminal_descriptors"])
        if (phase["accepted_descriptors"] > 0) != (phase["append_tokens"] > 0):
            _fail("E_TRAFFIC_MISMATCH",
                  "phase KV append descriptor count differs from its tokens",
                  ordinal=index, accepted=phase["accepted_descriptors"],
                  appended=phase["append_tokens"])

    required = {"cq_assignments": 1, "core_starts": 1,
                "live_contexts": 0, "fatal_records": 0}
    for name, value in required.items():
        if metrics.get(name, 0) != value:
            _fail("E_KV_STATE", "agent metric is not the contract value",
                  metric=name, observed=metrics.get(name), expected=value)
    return {
        "descriptors": len(by_id),
        "phases": [
            [phase["phase"], phase["instance_profile_id"],
             phase["kv_tokens_before"], phase["append_tokens"],
             phase["core_drain_tick"], phase["append_terminal_tick"],
             phase["commit_tick"]]
            for phase in phases],
        "profiles": sorted(phase_of),
        "kv_read_bytes": {
            instance.instance_profile_id: steps[
                instance.instance_profile_id].kv_read_bytes_per_member
            for instance in program.agent_instance_profiles
            if instance.instance_profile_id in steps},
    }


def _kv_tensor(program):
    symbol = program.agent_request_profiles[0].primary_kv_symbol_id
    for relocation in program.relocations:
        if relocation.symbol_sid == symbol:
            return relocation.tensor_id
    _fail("E_ABI_BOUNDS", "KV symbol has no relocation")
