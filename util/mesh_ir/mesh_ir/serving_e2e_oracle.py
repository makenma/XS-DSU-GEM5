"""Independent oracle for one Gate 6 R3 serving run (E2E-D).

Recomputes the executed descriptor closure, per-phase byte equations and the
agent terminal ledger from the immutable fixture program plus the runtime
artifacts.  It never trusts the artifact's own labels: any missing, extra or
mutated descriptor/byte/ledger entry is rejected.
"""

from __future__ import annotations

import math

from mesh_ir.generated import abi as A
from mesh_ir.gate3_oracle import FactsDocument
from mesh_ir.runtime_errors import require
from mesh_ir.execution_view import build_execution_views
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.scheduled.model import EndpointSide
from mesh_ir.model import MeshIrError
from mesh_ir.serving_oracle import generate_phase_plan

_READ_KINDS = (A.DMA_KIND.LOAD, A.DMA_KIND.PREFETCH, A.DMA_KIND.GATHER_LOAD)


def _fail(code: str, detail: str, **context) -> None:
    raise MeshIrError(code, detail, **context)


def measure_request_latency(facts: FactsDocument, mesh_window: dict, *,
                            ticks_per_second: int, process_wall_seconds: float) -> dict:
    require(type(ticks_per_second) is int and ticks_per_second > 0, 'simulation tick frequency is invalid')
    require(type(process_wall_seconds) in (int, float) and math.isfinite(process_wall_seconds) and
        process_wall_seconds >= 0, 'process wall time is invalid')
    completions = [row for row in facts.events if row['kind'] == 'CQ_CONSUME' and row['status'] == 'SUCCESS']
    require(len(completions) == 1, 'request latency requires one successful completion')
    request_id = completions[0]['request_id']
    events = {}
    for name in ('PUBLICATION_COMMIT', 'CQ_CONSUME', 'CQ_OBLIGATION_RETIRE', 'TASK_THINK_READY', 'TASK_TERMINAL'):
        matches = [row for row in facts.events if row['kind'] == name and row['request_id'] == request_id]
        require(len(matches) == 1, f'request latency has no unique {name} event')
        events[name] = matches[0]['tick']
    windows = dict(host_request=('PUBLICATION_COMMIT', 'CQ_CONSUME'),
        request_retirement=('PUBLICATION_COMMIT', 'CQ_OBLIGATION_RETIRE'),
        business=('TASK_THINK_READY', 'TASK_TERMINAL'), mesh=('FIRST_COMMAND_ISSUE', 'LAST_COMMAND_TERMINAL'))
    events.update(FIRST_COMMAND_ISSUE=mesh_window['begin_tick'], LAST_COMMAND_TERMINAL=mesh_window['end_tick'])
    require(events['PUBLICATION_COMMIT'] <= events['FIRST_COMMAND_ISSUE'] <= events['LAST_COMMAND_TERMINAL'] <=
        events['CQ_CONSUME'] <= events['CQ_OBLIGATION_RETIRE'], 'request and mesh latency windows are inconsistent')
    report = dict(request_id=request_id, ticks_per_second=ticks_per_second, process_wall_seconds=process_wall_seconds)
    for name, (begin, end) in windows.items():
        require(events[begin] <= events[end], f'{name} latency is negative')
        report[name] = dict(begin_event=begin, end_event=end, begin_tick=events[begin], end_tick=events[end],
            elapsed_ticks=events[end] - events[begin], seconds=(events[end] - events[begin]) / ticks_per_second)
    return report


def verify_serving_run(program, transport_rows, metrics,
                      serving_phases=None) -> dict:
    if not program.profile_stream_ranges:
        _fail("E_BINDING_ROLE", "program has no profile execution ranges")
    views = build_execution_views(program)
    catalog = ProgramBindingCatalog(program)
    owners = {command: view.profile_id for view in views.views for command in view.command_ids}
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
        expected = {
            "read_bytes": descriptor.useful_bytes if descriptor.kind in _READ_KINDS else 0,
            "write_bytes": descriptor.useful_bytes if descriptor.kind == A.DMA_KIND.STORE else 0,
            "p2p_bytes": descriptor.useful_bytes if descriptor.kind == A.DMA_KIND.P2P_PUSH else 0,
            "fill_bytes": descriptor.useful_bytes if descriptor.kind == A.DMA_KIND.LOCAL_FILL else 0,
        }
        if any(type(row.get(field)) is not int or row[field] != value
               for field, value in expected.items()):
            _fail("E_TRAFFIC_MISMATCH", "descriptor committed bytes differ from the plan",
                  descriptor=descriptor.descriptor_id,
                  committed={field: row.get(field) for field in expected}, planned=expected)
        if descriptor.command_id not in commands:
            _fail("E_TRAFFIC_MISMATCH", "descriptor command is missing",
                  descriptor=descriptor.descriptor_id)
        profile = owners[descriptor.command_id]
        phase_of.setdefault(profile, []).append(descriptor.descriptor_id)
    expected_profiles = [view.profile_id for view in views.views]
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
        descriptors = views.for_instance(instance.mesh_entrypoint_id, instance.mesh_profile_id).descriptor_ids
        slots = set()
        for member in program.agent_instance_member_bindings[instance.member_binding_first:instance.member_binding_first + instance.member_binding_count]:
            if member.static_kv_symbol_id:
                symbol = program.strings[member.static_kv_symbol_id - 1].value
                slots.add(catalog.binding(instance.mesh_entrypoint_id, instance.mesh_profile_id, symbol).slot.slot_id)
        kv_loads = []
        for descriptor in program.dma_descriptors:
            if descriptor.descriptor_id not in descriptors or descriptor.kind not in _READ_KINDS:
                continue
            source = catalog.descriptor_binding(descriptor.descriptor_id, EndpointSide.SRC)
            if source is not None and source.slot.slot_id in slots:
                kv_loads.append(descriptor)
        read_bytes = sum(descriptor.useful_bytes for descriptor in kv_loads)
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
                "live_contexts": 0}
    for name, value in required.items():
        if type(metrics.get(name)) is not int or metrics[name] != value:
            _fail("E_KV_STATE", "agent metric is not the contract value",
                  metric=name, observed=metrics.get(name), expected=value)
    if metrics.get("fatal_records", 0) != 0:
        _fail("E_KV_STATE", "serving run recorded a fatal event",
              observed=metrics["fatal_records"])
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
