"""Independent post-drain validator for a Gate 6 R3 serving error run.

The error product is accepted only when the frozen participant identities, the
per-instance execution-view participants, the final error instance ledger, the
real drain proof and the full terminal causality chain are all present.  Every
field is checked for existence, type and range, so a missing or mutated
artifact is rejected instead of silently shrinking the expectation.  The
``ERROR_DRAINED`` enum is never accepted as a substitute for the drain facts.
"""

from __future__ import annotations

from mesh_ir.agent_planning import build_command_identity_plan
from mesh_ir.execution_view import build_execution_views
from mesh_ir.generated import abi as A
from mesh_ir.serving_oracle import generate_phase_plan

_ERROR_FIELDS = ("b_error_count", "error_read_bursts", "error_write_bursts")
_DRAIN_FIELDS = ("outstanding_reads", "outstanding_writes", "pending_aw",
                 "pending_ar")
_LEDGER_FIELDS = ("completed", "errored", "cancelled")
_IDENTITY_KINDS = ("CORE_START", "CQ_OBLIGATION_RESERVE")


def frozen_request_identity(workload, release_policy):
    document = build_command_identity_plan(workload, None, release_policy)
    generates = [record for record in document["records"]
                 if record["command_kind"] == "GENERATE"]
    if len(generates) != 1:
        return None, None
    request_id = generates[0]["request_id"]
    return request_id, request_id


def typed_tick(value, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if minimum is not None and value < minimum:
        return None
    return value


def typed_flag(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value in (0, 1) else None


def planned_instance_cores(program, path_kind=None):
    if path_kind is None:
        path_kind = A.PATH_KIND.INITIAL_PREFILL
    if not program.agent_request_profiles:
        return []
    steps = generate_phase_plan(program, program.agent_request_profiles[0],
                                path_kind)
    views = build_execution_views(program)
    profiles = {profile.instance_profile_id: profile
                for profile in program.agent_instance_profiles}
    cores = []
    for step in steps:
        profile = profiles.get(step.instance_profile_id)
        if profile is None:
            return []
        if views.whole_program:
            view = views.wholeProgramView()
        else:
            view = views.for_instance(profile.mesh_entrypoint_id,
                                      profile.mesh_profile_id)
        cores.append(sorted(view.core_ids))
    return cores


def _identities(rows, key):
    if not isinstance(rows, list):
        return None
    found = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        value = typed_tick(row.get(key), minimum=0)
        if value is None:
            return None
        found.append(value)
    return found


def _same_identity_set(found, expected):
    return found is not None and sorted(found) == sorted(expected)


def _instance_ledgers(instances, instance_cores, fault_core, failures):
    expected_ordinals = list(range(1, len(instance_cores) + 1))
    for index, entry in enumerate(instances):
        if not isinstance(entry, dict):
            failures.append("an instance ledger entry is not an object")
            continue
        ordinal = typed_tick(entry.get("instance"), minimum=1)
        if index >= len(instance_cores):
            failures.append("the instance ledger exceeds the frozen execution "
                            "plan")
            continue
        if ordinal != expected_ordinals[index]:
            failures.append("the instance ledger ordinal does not match the "
                            "frozen execution plan")
        ledgers = entry.get("cores")
        if not isinstance(ledgers, dict):
            failures.append("instance %s carries no core ledger" % (index + 1))
            continue
        expected = [str(core) for core in instance_cores[index]]
        if sorted(ledgers) != sorted(expected):
            failures.append("instance %s does not carry exactly its frozen "
                            "participants" % (index + 1))
            continue
        for ident in expected:
            ledger = ledgers[ident]
            if not isinstance(ledger, dict):
                failures.append("instance %s has no ledger for core %s"
                                % (index + 1, ident))
                continue
            values = [typed_tick(ledger.get(name), minimum=0)
                      for name in _LEDGER_FIELDS]
            if any(value is None for value in values):
                failures.append("instance %s core %s ledger is not a "
                                "non-negative integer map"
                                % (index + 1, ident))
                continue
            if index + 1 < len(instances) and values[1] != 0:
                failures.append("committed instance %s recorded an error"
                                % (index + 1))
            if index + 1 == len(instances) and ident == str(fault_core) and \
                    values[1] < 1:
                failures.append("the final error instance did not record the "
                                "fault core error")


def _event_tick(events, kind, request_id, cookie, cookie_rule, failures):
    matches = [event for event in events
               if isinstance(event, dict) and event.get("kind") == kind]
    if not matches:
        failures.append("the %s event is missing" % kind)
        return None
    if len(matches) > 1:
        failures.append("the %s event is duplicated" % kind)
    event = matches[0]
    tick = typed_tick(event.get("tick"), minimum=1)
    if tick is None:
        failures.append("the %s event has no tick" % kind)
    if request_id is None or event.get("request_id") != request_id:
        failures.append("the %s event does not belong to the admitted request"
                        % kind)
    if cookie_rule is True and event.get("cookie") != cookie:
        failures.append("the %s event does not carry the bound CQ identity"
                        % kind)
    if cookie_rule is False and event.get("cookie") is not None:
        failures.append("the %s event carries an unexpected CQ identity"
                        % kind)
    return tick


def _admitted_identity(events, request_id, cookie, failures):
    if request_id is None or cookie is None:
        failures.append("the frozen command plan does not select a single "
                        "request identity")
        return
    seen = []
    for kind in _IDENTITY_KINDS:
        matches = [event for event in events
                   if isinstance(event, dict) and event.get("kind") == kind]
        if len(matches) != 1:
            failures.append("the run does not carry exactly one %s event"
                            % kind)
            continue
        event_request = typed_tick(matches[0].get("request_id"), minimum=0)
        event_cookie = typed_tick(matches[0].get("cookie"), minimum=0)
        if event_request != request_id or event_cookie != cookie:
            failures.append("the %s event does not match the frozen request "
                            "identity" % kind)
        seen.append((event_request, event_cookie))
    if len(seen) == 2 and seen[0] != seen[1]:
        failures.append("the admitted request and its bound CQ identity "
                        "disagree")


def verify_error_drain(result, program, core_ids, fault_core, committed_phases,
                       events, request_id, cookie, path_kind=None):
    failures = []
    if not isinstance(result, dict):
        return ["the error artifact is not an object"]
    expected_cores = sorted(core_ids)
    instance_cores = planned_instance_cores(program, path_kind)

    if result.get("terminal") != "ERROR_DRAINED":
        failures.append("the artifact terminal is not ERROR_DRAINED")
    if typed_flag(result.get("error_drained")) != 1:
        failures.append("error_drained is not the integer 1")

    terminal_tick = typed_tick(result.get("terminal_tick"), minimum=1)
    if terminal_tick is None:
        failures.append("terminal_tick is missing or not a positive integer")
    terminal_instance = typed_tick(result.get("terminal_instance"), minimum=1)
    if terminal_instance is None:
        failures.append("terminal_instance is missing or not a positive integer")
    terminal_core = typed_tick(result.get("terminal_error_core"), minimum=0)
    if terminal_core is None or terminal_core != fault_core:
        failures.append("terminal_error_core does not name the injected fault "
                        "core")

    instances = result.get("instances")
    if not isinstance(instances, list) or not instances:
        failures.append("the error artifact carries no instance ledger")
        instances = []
    elif len(instances) != committed_phases + 1 or \
            len(instance_cores) < len(instances):
        failures.append("the instance ledger does not cover the committed "
                        "phases plus the drained error instance")
    _instance_ledgers(instances, instance_cores, fault_core, failures)
    if instances:
        final = instances[-1]
        final_ordinal = (typed_tick(final.get("instance"), minimum=1)
                         if isinstance(final, dict) else None)
        if terminal_instance is not None and final_ordinal != terminal_instance:
            failures.append("the final instance ledger is not the instance that "
                            "committed the terminal")

    cores = result.get("cores")
    if not isinstance(cores, list) or not cores:
        failures.append("the error artifact carries no core evidence")
        cores = []
    core_ids_seen = _identities(cores, "core_id")
    if core_ids_seen is None:
        failures.append("the core evidence identity is malformed")
    elif not _same_identity_set(core_ids_seen, expected_cores):
        failures.append("the core evidence identity set differs from the frozen "
                        "participants")
    for core in cores:
        if not isinstance(core, dict):
            failures.append("a core evidence entry is not an object")
            continue
        ident = core.get("core_id")
        latch = typed_tick(core.get("error_latch_tick"), minimum=0)
        drained = typed_tick(core.get("work_drained_tick"), minimum=0)
        dma_idle = typed_flag(core.get("dma_idle"))
        instance_error = typed_flag(core.get("instance_error"))
        if None in (latch, drained, dma_idle, instance_error):
            failures.append("core %s drain evidence is malformed" % ident)
            continue
        if ident == fault_core:
            if latch <= 0 or instance_error != 1:
                failures.append("the fault core did not latch the injected "
                                "error")
            if drained <= 0:
                failures.append("the fault core committed the error product "
                                "with no work drain proof")
            elif latch > drained:
                failures.append("the fault core work drained before its error "
                                "latched")
            elif terminal_tick is not None and drained > terminal_tick:
                failures.append("the error terminal precedes the fault core "
                                "drain")
            if dma_idle != 1:
                failures.append("the fault core committed the error product "
                                "with a live DMA engine")
        elif latch != 0 or instance_error != 0:
            failures.append("a non-fault core claims the injected error")

    bridges = result.get("bridges")
    if not isinstance(bridges, list) or not bridges:
        failures.append("the error artifact carries no bridge evidence")
        bridges = []
    bridge_ids = _identities(bridges, "core_id")
    if bridge_ids is None:
        failures.append("the bridge evidence identity is malformed")
    elif not _same_identity_set(bridge_ids, expected_cores):
        failures.append("the bridge evidence identity set differs from the "
                        "frozen participants")
    for bridge in bridges:
        if not isinstance(bridge, dict):
            failures.append("a bridge evidence entry is not an object")
            continue
        ident = bridge.get("core_id")
        for field in _DRAIN_FIELDS:
            value = typed_tick(bridge.get(field), minimum=0)
            if value is None or value != 0:
                failures.append("the bridge on core %s still owns %s traffic at "
                                "the error terminal" % (ident, field))
        if typed_flag(bridge.get("idle")) != 1:
            failures.append("the bridge on core %s is not idle at the error "
                            "terminal" % ident)
        marks = []
        for field in _ERROR_FIELDS:
            value = typed_tick(bridge.get(field), minimum=0)
            if value is None:
                failures.append("the bridge on core %s has no %s count"
                                % (ident, field))
                continue
            marks.append(value)
        if ident == fault_core and marks and not any(marks):
            failures.append("the fault core bridge recorded no AXI error")
        if ident != fault_core and any(marks):
            failures.append("a non-fault bridge recorded an AXI error")

    _admitted_identity(events, request_id, cookie, failures)
    host = _event_tick(events, "GENERATE_TERMINAL_LATCHED", request_id, cookie,
                       False, failures)
    ready = _event_tick(events, "TERMINAL_READY", request_id, cookie, True,
                        failures)
    cq = _event_tick(events, "CQ_ASSIGN", request_id, cookie, True, failures)
    task = _event_tick(events, "TASK_TERMINAL", request_id, cookie, False,
                       failures)
    if terminal_tick is not None:
        if host is not None and host < terminal_tick:
            failures.append("the Frontend terminal latch precedes the mesh "
                            "error terminal")
        if host is not None and ready is not None and host > ready:
            failures.append("the Frontend terminal latch does not precede the "
                            "CQ terminal ready")
        if ready is not None and cq is not None and ready > cq:
            failures.append("the CQ terminal ready does not precede the CQ "
                            "assignment")
        if cq is not None and cq < terminal_tick:
            failures.append("the CQ assignment precedes the mesh error "
                            "terminal")
        if cq is not None and task is not None and task < cq:
            failures.append("the Host task terminal precedes its CQ "
                            "assignment")
    return failures
