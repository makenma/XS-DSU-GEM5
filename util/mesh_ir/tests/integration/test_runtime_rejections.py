import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from mesh_ir.generated.abi import ATTR_KIND

from tests.integration.support.runtime_harness import (
    ARCH,
    CONFIG,
    GEM5,
    MESH_IR_ROOT,
    REPO,
    TERMINAL_STATES as _TERMINAL_STATES,
    build_program as _build_program,
    build_program_with_arch as _build_program_with_arch,
    engine_commands as _engine_commands,
    instance_frame as _instance_frame,
    run_mock as _run_mock,
    sole_instance as _sole_instance,
    schedule as _schedule,
    terminal_partition as _assert_terminal_partition,
)


def _arch_with_queue_depth(tmp_path, depth):
    text = ARCH.read_text(encoding="utf-8")
    assert "descriptor_queue_depth: 16" in text
    path = tmp_path / f"mesh_1x2_depth{depth}.yaml"
    path.write_text(
        text.replace(
            "descriptor_queue_depth: 16", f"descriptor_queue_depth: {depth}"
        ),
        encoding="utf-8",
    )
    return path


def _arch_with_admit_window(tmp_path, window, depth=None):
    text = ARCH.read_text(encoding="utf-8")
    assert "admit_window: 8" in text
    text = text.replace("admit_window: 8", f"admit_window: {window}")
    if depth is not None:
        assert "descriptor_queue_depth: 16" in text
        text = text.replace(
            "descriptor_queue_depth: 16", f"descriptor_queue_depth: {depth}"
        )
    path = tmp_path / f"mesh_1x2_window{window}_depth{depth or 16}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _arch_with_engine_depth(tmp_path, engine, depth):
    text = ARCH.read_text(encoding="utf-8")
    anchor = f"  {engine}:\n    queue_depth: 8"
    assert anchor in text, engine
    path = tmp_path / f"mesh_1x2_{engine}{depth}.yaml"
    path.write_text(
        text.replace(anchor, f"  {engine}:\n    queue_depth: {depth}"),
        encoding="utf-8",
    )
    return path


def _build_repeat_with_count(tmp_path, count, arch=None):
    """Build the accepted repeat program with the builder's REPEAT_COUNT input
    overridden in the build subprocess.  The normal CLI builder, verifier and
    publisher still run, so the image is compiler-accepted."""
    program_dir = tmp_path / f"repeat-count{count}"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import sys; from mesh_ir import golden_programs, cli; "
            f"golden_programs.REPEAT_COUNT = {count}; "
            "raise SystemExit(cli.main(sys.argv[1:]))",
            "build",
            "--program",
            "repeat",
            "--arch",
            str(arch or ARCH),
            "--out",
            str(program_dir),
        ],
        cwd=MESH_IR_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return program_dir


def test_reference_compute_rejection_is_observed(tmp_path):
    program_dir = _build_program(tmp_path, "single")
    run = _run_mock(
        tmp_path,
        "m5out",
        "single",
        program_dir,
        ("--expect-fatal", "reference_compute"),
    )
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    fatal = [line for line in output.splitlines() if "fatal:" in line]
    assert any("reference_compute=true is forbidden" in line for line in fatal), fatal


@pytest.mark.parametrize("program", ["poison", "poison_ew", "poison_store"])
def test_filled_producer_is_resident(tmp_path, program):
    program_dir = _build_program(tmp_path, program)
    run = _run_mock(tmp_path, "m5out", program, program_dir)
    assert run.returncode == 0, run.stdout + run.stderr


def test_multi_descriptor_command_aggregates_completion(tmp_path):
    program_dir = _build_program(tmp_path, "multi_descriptor")
    run = _run_mock(tmp_path, "m5out", "multi_descriptor", program_dir)
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads((program_dir / "actual_result.json").read_text())
    rows = {row["descriptor_id"]: row for row in result["transport"]}
    assert len(rows) == 20
    assert sum(row["read_bytes"] for row in rows.values()) == 80


def test_multi_descriptor_partial_submission_backpressure(tmp_path):
    # The 20-descriptor group exceeds the default descriptor queue depth 16,
    # so the core must reserve submission and resume after each completion.
    program_dir = _build_program(tmp_path, "multi_descriptor")
    run = _run_mock(tmp_path, "m5out", "multi_descriptor", program_dir)
    assert run.returncode == 0, run.stdout + run.stderr


def test_multi_descriptor_depth_one_drains_every_descriptor(tmp_path):
    # Queue depth 1 forces the command to remain live across the whole group:
    # success must not be published while a descriptor is still unsubmitted.
    arch = _arch_with_queue_depth(tmp_path, 1)
    program_dir = _build_program_with_arch(tmp_path, "multi_descriptor", arch)
    run = _run_mock(
        tmp_path, "m5out", "multi_descriptor", program_dir, ("--arch", str(arch))
    )
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads((program_dir / "actual_result.json").read_text())
    rows = {row["descriptor_id"]: row for row in result["transport"]}
    assert len(rows) == 20
    assert sum(row["read_bytes"] for row in rows.values()) == 80
    assert result["cores"][0]["dma_idle"] == 1
    assert result["cores"][0]["live_commands"] == 0


def test_admitted_dma_continues_under_window_one_backpressure(tmp_path):
    # The admission window bounds NEW commands.  A command that is already
    # admitted and still submitting its descriptor group must keep going, or a
    # window of 1 with descriptor queue depth 1 deadlocks after descriptor 1.
    arch = _arch_with_admit_window(tmp_path, 1, depth=1)
    program_dir = _build_program_with_arch(tmp_path, "multi_descriptor", arch)
    run = _run_mock(
        tmp_path,
        "m5out",
        "multi_descriptor",
        program_dir,
        ("--arch", str(arch), "--watchdog-ticks", "10000000"),
    )
    assert "E_RUNTIME_DEADLOCK" not in (run.stdout + run.stderr), run.stdout + run.stderr
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads((program_dir / "actual_result.json").read_text())
    rows = {row["descriptor_id"]: row for row in result["transport"]}
    assert len(rows) == 20
    assert sum(row["read_bytes"] for row in rows.values()) == 80
    core, _ = _assert_terminal_partition(result)
    assert core["live_commands"] == 0
    assert core["live_dma_commands"] == 0
    assert core["allocation_pins"] == 0
    assert core["dma_idle"] == 1


def test_repeat_replay_issues_under_window_one(tmp_path):
    # The REPEAT gate is retained control/drain state, not executable work: it
    # must not consume the window it needs for its own replay, or a window of 1
    # starves every replay generation.
    arch = _arch_with_admit_window(tmp_path, 1)
    program_dir = _build_program_with_arch(tmp_path, "repeat", arch)
    run = _run_mock(
        tmp_path,
        "m5out",
        "repeat",
        program_dir,
        ("--arch", str(arch), "--watchdog-ticks", "10000000"),
    )
    assert "E_RUNTIME_DEADLOCK" not in (run.stdout + run.stderr), run.stdout + run.stderr
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads((program_dir / "actual_result.json").read_text())
    core, plan = _assert_terminal_partition(result)
    assert len({generation for _, generation in plan}) == 3
    assert core["live_commands"] == 0
    assert core["allocation_pins"] == 0
    assert core["dma_idle"] == 1


def test_repeat_generations_complete(tmp_path):
    program_dir = _build_program(tmp_path, "repeat")
    run = _run_mock(tmp_path, "m5out", "repeat", program_dir)
    assert run.returncode == 0, run.stdout + run.stderr


def test_multi_descriptor_error_drain_stops_submission(tmp_path):
    # Inject an AXI error on a middle descriptor: the runtime must stop
    # submitting the remaining descriptors, retire the in-flight ones safely
    # and drain to MESH_PROGRAM_ERROR_DRAINED instead of hanging.
    program_dir = _build_program(tmp_path, "multi_descriptor")
    run = _run_mock(
        tmp_path,
        "m5out",
        "multi_descriptor",
        program_dir,
        ("--error-descriptors", "5"),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    result = json.loads((program_dir / "actual_result.json").read_text())
    rows = {row["descriptor_id"]: row for row in result["transport"]}
    assert 20 not in rows
    assert result["cores"][0]["dma_idle"] == 1
    assert result["cores"][0]["live_commands"] == 0


def test_cross_command_error_drains_at_depth_one(tmp_path):
    # Descriptor 1 errors while a later command is already admitted with zero
    # submitted descriptors: the admitted-but-idle command must have an
    # explicit cancellation path instead of hanging the watchdog.
    arch = _arch_with_queue_depth(tmp_path, 1)
    program_dir = _build_program_with_arch(tmp_path, "single", arch)
    run = _run_mock(
        tmp_path,
        "m5out",
        "single",
        program_dir,
        (
            "--arch",
            str(arch),
            "--error-descriptors",
            "1",
            "--watchdog-ticks",
            "10000000",
        ),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    assert "E_RUNTIME_DEADLOCK" not in output, output
    result = json.loads((program_dir / "actual_result.json").read_text())
    core = result["cores"][0]
    assert core["dma_idle"] == 1
    assert core["live_commands"] == 0
    assert core["live_dma_commands"] == 0
    assert core["live_dma_tags"] == 0
    assert core["allocation_pins"] == 0
    _assert_terminal_partition(result)


def test_command_has_exactly_one_terminal_state(tmp_path):
    program_dir = _build_program(tmp_path, "multi_descriptor")
    run = _run_mock(
        tmp_path,
        "m5out",
        "multi_descriptor",
        program_dir,
        ("--error-descriptors", "5"),
    )
    assert "MESH_PROGRAM_ERROR_DRAINED" in (run.stdout + run.stderr)
    result = json.loads((program_dir / "actual_result.json").read_text())
    core, _ = _assert_terminal_partition(result)
    errored = set(core["errored_command_ids"])
    cancelled = set(core["cancelled_command_ids"])
    completed = set(core["completed_command_ids"])
    assert errored == {2}
    assert errored.isdisjoint(cancelled)
    assert errored.isdisjoint(completed)
    assert cancelled.isdisjoint(completed)
    assert core["live_commands"] == 0
    assert core["live_dma_commands"] == 0
    assert core["live_dma_tags"] == 0
    assert core["allocation_pins"] == 0
    assert core["dma_idle"] == 1


def test_unadmittable_command_still_takes_a_terminal(tmp_path):
    # dma_pin puts commands from two streams on one SRAM allocation, so a
    # command that fails while holding that allocation's pin leaves a later
    # command that can never be admitted.  It must still retire with exactly
    # one cancelled terminal instead of vanishing from the ledger.
    program_dir = _build_program(tmp_path, "dma_pin")
    run = _run_mock(
        tmp_path,
        "m5out",
        "dma_pin",
        program_dir,
        ("--error-descriptors", "2"),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    result = json.loads((program_dir / "actual_result.json").read_text())
    core, plan = _assert_terminal_partition(result)
    assert set(core["errored_command_ids"]) == {3}
    assert len(core["cancelled_command_ids"]) == len(plan) - 3
    assert core["live_commands"] == 0
    assert core["live_dma_commands"] == 0
    assert core["live_dma_tags"] == 0
    assert core["allocation_pins"] == 0
    assert core["dma_idle"] == 1


@pytest.mark.parametrize("count", (1, 2, 3))
@pytest.mark.parametrize("window", (None, 1))
def test_repeat_generation_counts_partition_and_complete(tmp_path, count, window):
    # A compiler-accepted REPEAT with count 1 performs one original execution
    # and no replay: the retained control state must retire even though there is
    # no drained generation to finalize it.  Counts 1/2/3 are covered at the
    # default and the smallest window.
    case = tmp_path / f"count{count}-window{window}"
    case.mkdir()
    arch = ARCH if window is None else _arch_with_admit_window(case, window)
    program_dir = _build_repeat_with_count(case, count, arch)
    args = ["--watchdog-ticks", "10000000"]
    if window is not None:
        args = ["--arch", str(arch), *args]
    run = _run_mock(case, "m5out", "repeat", program_dir, tuple(args))
    output = run.stdout + run.stderr
    assert "E_RUNTIME_DEADLOCK" not in output, output
    assert run.returncode == 0, output
    result = json.loads((program_dir / "actual_result.json").read_text())
    core, plan = _assert_terminal_partition(result)
    # Generation 0 is the original pass; replay passes are labelled 2..count.
    assert {generation for _, generation in plan} == {0} | set(
        range(2, count + 1)
    ), plan
    states = {
        record["state"]
        for instance in result["instances"]
        for record in instance["cores"][str(core["core_id"])]["terminals"]
    }
    assert states == {"completed"}, states
    assert core["live_commands"] == 0
    assert core["live_dma_commands"] == 0
    assert core["live_dma_tags"] == 0
    assert core["allocation_pins"] == 0
    assert core["dma_idle"] == 1


def test_repeat_single_generation_error_drains(tmp_path):
    # The count-1 control state must also survive the error path with an exact
    # terminal partition and a physical drain.
    program_dir = _build_repeat_with_count(tmp_path, 1)
    run = _run_mock(
        tmp_path,
        "m5out",
        "repeat",
        program_dir,
        ("--error-descriptors", "1", "--watchdog-ticks", "10000000"),
    )
    output = run.stdout + run.stderr
    assert "E_RUNTIME_DEADLOCK" not in output, output
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    assert "does not partition" not in output, output
    result = json.loads((program_dir / "actual_result.json").read_text())
    core, plan = _assert_terminal_partition(result)
    assert {generation for _, generation in plan} == {0}, plan
    assert core["live_commands"] == 0
    assert core["allocation_pins"] == 0
    assert core["dma_idle"] == 1


def test_repeat_generations_have_distinct_terminal_records(tmp_path):
    # A REPEAT subrange member is dispatched once per generation, so the
    # ledger must carry one terminal per (command_id, generation) rather than
    # collapsing the replays onto one record.
    program_dir = _build_program(tmp_path, "repeat")
    run = _run_mock(tmp_path, "m5out", "repeat", program_dir)
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads((program_dir / "actual_result.json").read_text())
    core, plan = _assert_terminal_partition(result)
    generations = {entry[1] for entry in core["dispatch_plan"]}
    assert len(generations) > 1
    assert len(plan) > len({entry[0] for entry in core["dispatch_plan"]})


def _repeat_generation_states(result):
    instance = result["instances"][0]
    core_id = result["cores"][0]["core_id"]
    return {
        (record["command_id"], record["generation"]): record["state"]
        for record in instance["cores"][str(core_id)]["terminals"]
    }


def test_repeat_first_pass_error_cancels_every_later_generation(tmp_path):
    # Descriptor 1 is the REPEAT window's load, so an error on its first
    # occurrence kills generation 0 before any replay runs.  The replay
    # generations are still part of the dispatch plan and must take their
    # cancelled terminal without shrinking the plan or skipping the check.
    program_dir = _build_program(tmp_path, "repeat")
    run = _run_mock(
        tmp_path,
        "m5out",
        "repeat",
        program_dir,
        ("--error-descriptors", "1", "--watchdog-ticks", "10000000"),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    assert "does not partition" not in output, output
    assert "E_RUNTIME_DEADLOCK" not in output, output
    result = json.loads((program_dir / "actual_result.json").read_text())
    core, plan = _assert_terminal_partition(result)
    states = _repeat_generation_states(result)
    # Generation 0 of the window is the faulted pass; every later generation
    # is a logical cancellation, not a silent omission.
    assert states[(2, 0)] == "errored"
    replay = {key: state for key, state in states.items() if key[1] != 0}
    assert replay, states
    assert set(replay.values()) == {"cancelled"}, states
    assert core["live_commands"] == 0
    assert core["allocation_pins"] == 0
    assert core["dma_idle"] == 1


def test_repeat_replay_error_cancels_remaining_generations(tmp_path):
    # The same fault applied to the second occurrence of descriptor 1 lets
    # generation 0 complete and fails inside the first replay generation, so
    # the remaining replay generations must still be cancelled.
    program_dir = _build_program(tmp_path, "repeat")
    run = _run_mock(
        tmp_path,
        "m5out",
        "repeat",
        program_dir,
        (
            "--error-descriptors",
            "1",
            "--fault-occurrence",
            "2",
            "--watchdog-ticks",
            "10000000",
        ),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    assert "does not partition" not in output, output
    assert "E_RUNTIME_DEADLOCK" not in output, output
    result = json.loads((program_dir / "actual_result.json").read_text())
    core, plan = _assert_terminal_partition(result)
    states = _repeat_generation_states(result)
    generations = sorted({entry[1] for entry in core["dispatch_plan"]})
    assert len(generations) > 2, generations
    first_failed = generations[1]
    assert any(
        state == "errored" and key[1] == first_failed
        for key, state in states.items()
    ), states
    for generation in generations[2:]:
        replay = {key: s for key, s in states.items() if key[1] == generation}
        assert replay, (generation, states)
        assert set(replay.values()) == {"cancelled"}, (generation, replay)
    assert core["live_commands"] == 0
    assert core["allocation_pins"] == 0
    assert core["dma_idle"] == 1



def test_watchdog_reports_the_receive_transfer(tmp_path):
    # The accepted dual fixture's RECV_WAIT becomes the blocked decode head once
    # the peer descriptor is dropped, so the derived receive relation is
    # reachable without changing admission.  The watchdog bound must be large
    # enough to reach that state (5000 is not).
    program_dir = _build_program(tmp_path, "dual")
    descriptors = _schedule(program_dir)["DMA_DESCRIPTORS"]
    p2p = next(row for row in descriptors if row["transfer_id"] != 0)
    recv_command = _recv_wait_command(program_dir, p2p["transfer_id"])
    run = _run_mock(
        tmp_path,
        "m5out",
        "dual",
        program_dir,
        (
            "--drop-descriptors",
            str(p2p["descriptor_id"]),
            "--watchdog-ticks",
            "10000000",
        ),
    )
    output = run.stdout + run.stderr
    assert "E_RUNTIME_DEADLOCK" in output, output
    graph = output[output.find("E_RUNTIME_DEADLOCK"):]
    assert (
        f"recv cmd={recv_command} transfer={p2p['transfer_id']} committed=0 "
        f"producers=cmd{p2p['command_id']}.descriptor{p2p['descriptor_id']}"
        in graph
    ), graph


def test_admission_window_bounds_independent_issue(tmp_path):
    # single's two LOAD commands both wait only on event 1, so neither depends
    # on the other: the admission window alone decides whether the second can
    # issue before the first retires.
    observed = {}
    for window in (8, 1):
        case = tmp_path / f"window{window}"
        case.mkdir()
        arch = _arch_with_admit_window(case, window)
        program_dir = _build_program_with_arch(case, "single", arch)
        assert _command_wait_events(program_dir, 2) == [1]
        assert _command_wait_events(program_dir, 3) == [1]
        run = _run_mock(
            case, "m5out", "single", program_dir, ("--arch", str(arch))
        )
        assert run.returncode == 0, run.stdout + run.stderr
        core = json.loads((program_dir / "actual_result.json").read_text())[
            "cores"
        ][0]
        observed[window] = (
            int(core["command_issue_ticks"]["3"]),
            int(core["command_done_ticks"]["2"]),
        )
    assert observed[8][0] < observed[8][1], observed
    assert observed[1][0] >= observed[1][1], observed


_ENGINE_FIXTURES = (
    ("engine_tensor_pressure", 4),
    ("engine_vector_pressure", 5),
    ("engine_reduce_pressure", 6),
)


def _engine_depth_run(tmp_path, program, engine, depth):
    case = tmp_path / f"{program}-depth{depth}"
    case.mkdir(parents=True, exist_ok=True)
    arch = _arch_with_engine_depth(case, engine, depth)
    program_dir = _build_program_with_arch(case, program, arch)
    run = _run_mock(
        case, "m5out", program, program_dir, ("--arch", str(arch))
    )
    assert run.returncode == 0, run.stdout + run.stderr
    return program_dir, json.loads((program_dir / "actual_result.json").read_text())


def _engine_content(program_dir, result):
    """Content and traffic keyed by the published stable operation identity, so
    two runs of the same program can be compared without a second fixture
    definition."""
    schedule = _schedule(program_dir)
    stable = {
        row["command_id"]: row["source_op_id"] for row in schedule["COMMANDS"]
    }
    descriptor_op = {
        row["descriptor_id"]: stable[row["command_id"]]
        for row in schedule["DMA_DESCRIPTORS"]
    }
    content = {
        "digests": {
            f"{row['core_id']}:{stable[row['command_id']]}": row["digest"]
            for row in result["digests"]
        },
        "traffic": {
            f"{descriptor_op[row['descriptor_id']]}:{row['descriptor_id']}": (
                row["read_bytes"],
                row["write_bytes"],
                row["p2p_bytes"],
                row["fill_bytes"],
                row["read_bursts"],
                row["write_bursts"],
                row["payload_digest"],
            )
            for row in result["transport"]
        },
        "useful_bytes": sum(
            row["read_bytes"]
            + row["write_bytes"]
            + row["p2p_bytes"]
            + row["fill_bytes"]
            for row in result["transport"]
        ),
    }
    assert content["digests"], content
    assert content["traffic"], content
    return content


@pytest.mark.parametrize(("program", "engine_id"), _ENGINE_FIXTURES)
def test_engine_slot_bounds_dependency_ready_work(tmp_path, program, engine_id):
    engine = program.split("_", 2)[1] + "_engine"
    runs = {
        depth: _engine_depth_run(tmp_path, program, engine, depth)
        for depth in (1, 2)
    }
    program_dir = runs[1][0]
    engine_commands = _engine_commands(program_dir, engine_id)
    assert len(engine_commands) >= 2, engine_commands
    waits = _schedule(program_dir)["COMMAND_WAITS"]
    commands = _schedule(program_dir)["COMMANDS"]
    wait_set = {
        row["command_id"]: [
            waits[index]["event_id"]
            for index in range(row["wait_begin"], row["wait_begin"] + row["wait_count"])
        ]
        for row in commands
    }
    ready_groups = {
        tuple(wait_set[command_id]) for command_id in engine_commands
    }
    assert len(ready_groups) == 1, (engine_commands, ready_groups)
    producers = [
        row["command_id"]
        for row in commands
        if row["signal_event"] in wait_set[engine_commands[0]]
    ]
    assert len(producers) == len(wait_set[engine_commands[0]]), producers

    for depth in (1, 2):
        core, _ = _assert_terminal_partition(runs[depth][1])
        result = runs[depth][1]
        _, ledger = _instance_frame(result, _sole_instance(result), core["core_id"])
        issue = {
            row["command_id"]: row["issue_tick"]
            for row in ledger["observations"]["commands"]
            if row["issued"]
        }
        done = {
            row["command_id"]: row["terminal_tick"]
            for row in ledger["observations"]["commands"]
            if row["terminal"]
        }
        blocks = ledger["observations"]["engine_blocks"]
        for producer in producers:
            assert done[producer] < min(done[c] for c in engine_commands), done
        ordered = sorted(engine_commands, key=lambda c: (issue[c], c))
        for block in blocks:
            assert block["generation"] == 0, block
            assert block["episode_index"] >= 0, block
            assert block["depth"] == depth, (block, depth)
            assert block["occupied"] <= block["depth"], block
            assert block["first_reject_tick"] <= block["last_reject_tick"], block
            assert block["holders"], block
            owners = [holder["command_id"] for holder in block["holders"]]
            assert len(set(owners)) == len(owners), block
            for owner in owners:
                assert owner in engine_commands, (owner, engine_commands)
                assert issue[owner] <= block["first_reject_tick"] <= done[owner], (
                    block,
                    issue[owner],
                    done[owner],
                )
        if depth == 1:
            for holder, blocked in zip(ordered, ordered[1:]):
                assert issue[blocked] >= done[holder], (issue, done)
            blocked_commands = {block["command_id"] for block in blocks}
            assert set(ordered[1:]) <= blocked_commands, (ordered, blocks)
        else:
            assert any(
                issue[later] < done[earlier]
                for earlier, later in zip(ordered, ordered[1:])
            ), (issue, done)
        assert core["engine_occupancy"] == {"tensor": 0, "vector": 0, "reduce": 0}
        assert core["live_commands"] == 0
        assert core["dma_idle"] == 1

    assert _engine_content(runs[1][0], runs[1][1]) == _engine_content(
        runs[2][0], runs[2][1]
    )


def test_engine_block_episodes_track_owner_changes(tmp_path):
    # The worker-stream RELU stays ready behind whichever lifecycle-stream RELU
    # holds the single vector slot, so the same command/generation must produce
    # one episode per distinct holder rather than a single swallowed record.
    program_dir, result = _engine_depth_run(
        tmp_path, "engine_vector_pressure", "vector_engine", 1
    )
    core, _ = _assert_terminal_partition(result)
    _, ledger = _instance_frame(result, _sole_instance(result), core["core_id"])
    engine_commands = _engine_commands(program_dir, 5)
    assert len(engine_commands) >= 3, engine_commands
    first, second, third = engine_commands[:3]
    episodes = [
        block
        for block in ledger["observations"]["engine_blocks"]
        if block["command_id"] == third and block["generation"] == 0
    ]
    assert len(episodes) >= 2, ledger["observations"]["engine_blocks"]
    assert [
        [holder["command_id"] for holder in block["holders"]] for block in episodes[:2]
    ] == [[first], [second]], episodes
    assert [block["episode_index"] for block in episodes] == list(range(len(episodes))), episodes
    for block in episodes:
        assert block["depth"] == 1 and block["occupied"] == 1, block
        assert block["first_reject_tick"] <= block["last_reject_tick"], block
    assert [block["begin_tick"] for block in episodes] == sorted(
        block["begin_tick"] for block in episodes
    ), episodes


@pytest.mark.parametrize(("program", "engine_id"), _ENGINE_FIXTURES)
def test_engine_slot_state_resets_between_instances(tmp_path, program, engine_id):
    case = tmp_path / f"{program}-instances"
    case.mkdir(parents=True, exist_ok=True)
    engine = program.split("_", 2)[1] + "_engine"
    arch = _arch_with_engine_depth(case, engine, 1)
    program_dir = _build_program_with_arch(case, program, arch)
    run = _run_mock(
        case,
        "m5out",
        program,
        program_dir,
        ("--arch", str(arch), "--instances", "2"),
    )
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads((program_dir / "actual_result.json").read_text())
    assert len(result["instances"]) == 2
    core, plan = _assert_terminal_partition(result)
    assert len(_engine_commands(program_dir, engine_id)) >= 2
    for index, instance in enumerate(result["instances"]):
        ledger = instance["cores"][str(core["core_id"])]
        assert ledger["completed"] == len(plan), (index, ledger["completed"])
        assert ledger["errored"] == 0 and ledger["cancelled"] == 0, ledger
        assert ledger["resources"] == {
            "live_commands": 0,
            "live_dma_commands": 0,
            "live_dma_tags": 0,
            "allocation_pins": 0,
            "engine_occupancy": {"tensor": 0, "vector": 0, "reduce": 0},
        }, ledger


def _deadlock_graph(tmp_path, program, extra_args):
    program_dir = _build_program(tmp_path, program)
    run = _run_mock(
        tmp_path, "m5out", program, program_dir,
        (*extra_args, "--watchdog-ticks", "5000"),
    )
    output = run.stdout + run.stderr
    assert "E_RUNTIME_DEADLOCK" in output, output
    return output[output.find("E_RUNTIME_DEADLOCK"):]


def _command_allocation_ids(program_dir, command_id):
    sections = _schedule(program_dir)
    row = next(item for item in sections["COMMANDS"] if item["command_id"] == command_id)
    operands = sections["COMMAND_OPERANDS"][
        row["operand_begin"] : row["operand_begin"] + row["operand_count"]
    ]
    return {operand["allocation_id"] for operand in operands}


def _descriptor_command(program_dir, descriptor_id):
    for row in _schedule(program_dir)["DMA_DESCRIPTORS"]:
        if row["descriptor_id"] == descriptor_id:
            return row["command_id"]
    raise AssertionError(f"descriptor {descriptor_id} is not in the program")


def _command_wait_events(program_dir, command_id):
    sections = _schedule(program_dir)
    waits = sections["COMMAND_WAITS"]
    for row in sections["COMMANDS"]:
        if row["command_id"] == command_id:
            return [
                waits[index]["event_id"]
                for index in range(
                    row["wait_begin"], row["wait_begin"] + row["wait_count"]
                )
            ]
    raise AssertionError(f"command {command_id} is not in the program")


def _recv_wait_command(program_dir, transfer_id):
    """The command whose RECV_WAIT attr names this transfer, from the shared
    ABI attr-kind authority."""
    sections = _schedule(program_dir)
    attr_kinds = [
        index + 1
        for index, attr in enumerate(sections["OP_ATTRS"])
        if attr.get("kind") == ATTR_KIND.RECV_WAIT_V1
        and attr.get("transfer_id") == transfer_id
    ]
    assert len(attr_kinds) == 1, attr_kinds
    matches = [
        row["command_id"]
        for row in sections["COMMANDS"]
        if row["attr_index"] == attr_kinds[0]
    ]
    assert len(matches) == 1, matches
    return matches[0]


def _command_signal_event(program_dir, command_id):
    for row in _schedule(program_dir)["COMMANDS"]:
        if row["command_id"] == command_id:
            return row["signal_event"]
    raise AssertionError(f"command {command_id} is not in the program")


def _next_command_after(program_dir, command_id):
    commands = _schedule(program_dir)["COMMANDS"]
    assert len({row["stream_id"] for row in commands}) == 1
    later = [
        row["command_id"] for row in commands if row["command_id"] > command_id
    ]
    assert later, "no command follows the dropped descriptor"
    return min(later)


def _arch_with_three_cores(tmp_path):
    """A schema-valid three-core architecture: mesh dimensions, one ordered
    fabric initiator per core, one peer target per core and the complete
    source-target quota set."""
    text = ARCH.read_text(encoding="utf-8")
    assert "cols: 2" in text and "core_ids: [0, 1]" in text
    text = text.replace("cols: 2", "cols: 3")
    text = text.replace("core_ids: [0, 1]", "core_ids: [0, 1, 2]")
    core_one = (
        "  - {name: core_1, core_id: 1, src_node: 1, src_port: 0, "
        "router_id: 1, default_target_node: 100}\n"
    )
    assert core_one in text
    text = text.replace(
        core_one,
        core_one
        + "  - {name: core_2, core_id: 2, src_node: 2, src_port: 0, "
        "router_id: 2, default_target_node: 100}\n",
        1,
    )
    peer_one = (
        "  - name: peer_1\n    dst_node: 1001\n    router_id: 1\n"
        "    ranges:\n"
        "      - {region_id: 2, owner_core: 1, offset_bytes: 0, "
        "size_bytes: 0x200000, access: READ_WRITE}\n"
    )
    assert peer_one in text
    text = text.replace(
        peer_one,
        peer_one
        + "  - name: peer_2\n    dst_node: 1002\n    router_id: 2\n"
        "    ranges:\n"
        "      - {region_id: 2, owner_core: 2, offset_bytes: 0, "
        "size_bytes: 0x200000, access: READ_WRITE}\n",
        1,
    )
    present = {(0, 100), (0, 1000), (0, 1001), (1, 100), (1, 1000), (1, 1001)}
    missing = "".join(
        f"  - {{src_node: {source}, src_port: 0, dst_node: {target}, "
        f"write_contexts: 16, write_beats: 512, read_contexts: 16, "
        f"read_beats: 512}}\n"
        for source in (0, 1, 2)
        for target in (100, 1000, 1001, 1002)
        if (source, target) not in present
    )
    path = tmp_path / "mesh_1x3.yaml"
    path.write_text(text.rstrip("\n") + "\n" + missing, encoding="utf-8")
    return path


def test_architecture_count_change_is_rejected_before_installation(tmp_path):
    # A runtime architecture with one more core than the program image must be
    # rejected by the C++ admission path before any core is installed.  The
    # fixture is schema-valid (dimensions, initiators, peer targets and quotas
    # all extended), so only the digest comparison can reject it: the scheduled
    # core list is a covered fact of the effective architecture digest.
    program_dir = _build_program(tmp_path, "single")
    arch = _arch_with_three_cores(tmp_path)
    run = _run_mock(
        tmp_path, "m5out", "single", program_dir, ("--arch", str(arch))
    )
    output = run.stdout + run.stderr
    assert "E_ARCH_DIGEST" in output, output
    assert "architecture digest mismatch" in output, output
    assert "MESH_PROGRAM_DONE" not in output, output


def test_watchdog_reports_lost_completion(tmp_path):
    program_dir = _build_program(tmp_path, "single")
    run = _run_mock(
        tmp_path,
        "m5out",
        "single",
        program_dir,
        ("--drop-descriptors", "1", "--watchdog-ticks", "5000"),
    )
    output = run.stdout + run.stderr
    assert "E_RUNTIME_DEADLOCK" in output, output
    assert "core 0 live=" in output, output
    # The lost descriptor's owning command must appear with its exact pending
    # descriptor, and the consumer with the exact event and producer.
    owner = _descriptor_command(program_dir, 1)
    event = _command_signal_event(program_dir, owner)
    assert f"dma cmd={owner} generation=0 submitted=1/1 pending=1" in output, output
    assert re.search(
        rf"wait-event cmd=\d+ generation=0 event={event} producer=cmd{owner}\b",
        output,
    ), output


def test_watchdog_names_the_pin_holder(tmp_path):
    # dma_pin serializes two streams on one SRAM allocation; dropping the
    # descriptor that keeps the holder admitted must name that exact holder.
    program_dir = _build_program(tmp_path, "dma_pin")
    holder = _descriptor_command(program_dir, 2)
    run = _run_mock(
        tmp_path, "m5out", "dma_pin", program_dir,
        ("--drop-descriptors", "2", "--watchdog-ticks", "5000"),
    )
    output = run.stdout + run.stderr
    assert "E_RUNTIME_DEADLOCK" in output, output
    graph = output[output.find("E_RUNTIME_DEADLOCK"):]
    assert re.search(
        rf"pin-blocked cmd=\d+ generation=0 allocation=\d+ pins=1 "
        rf"held_by=cmd{holder}\b",
        graph,
    ), graph
    blocked = re.search(r"pin-blocked cmd=(\d+) ", graph)
    assert blocked and int(blocked.group(1)) != holder, graph

    rows = [row for row in graph.splitlines() if "pin-blocked" in row]
    parsed = {}
    for row in rows:
        match = re.fullmatch(
            r"\s*pin-blocked cmd=(\d+) generation=(\d+) allocation=(\d+) "
            r"pins=(\d+) held_by=(.*)",
            row,
        )
        assert match, row
        parsed.setdefault(int(match.group(1)), []).append(match)
    assert set(parsed) == {int(blocked.group(1))}, parsed
    blocked_command = int(blocked.group(1))
    blocked_allocations = _command_allocation_ids(program_dir, blocked_command)
    holder_allocations = _command_allocation_ids(program_dir, holder)
    assert blocked_allocations & holder_allocations, (blocked_allocations, holder_allocations)
    seen = {int(match.group(3)) for match in parsed[blocked_command]}
    local = {row["allocation_id"] for row in _schedule(program_dir)["ALLOCATIONS"]}
    shared = (blocked_allocations & holder_allocations) & local
    assert seen and seen <= blocked_allocations & local, (seen, blocked_allocations, local)
    assert shared and shared <= seen, (shared, seen)
    for match in parsed[blocked_command]:
        assert match.group(2) == "0", match.group(0)
        assert match.group(4) == "1", match.group(0)
        holders = match.group(5).split(",")
        assert len(set(holders)) == len(holders), match.group(0)
        assert f"cmd{holder}" in holders, match.group(0)
        assert int(match.group(3)) in blocked_allocations, match.group(0)


def test_watchdog_names_fence_tag_owners(tmp_path):
    # dma_fence registers an instance-scoped fence behind the load; dropping
    # that descriptor leaves a captured tag unretired, and the graph must name
    # the tag and its exact owning command.
    program_dir = _build_program(tmp_path, "dma_fence")
    owner = _descriptor_command(program_dir, 1)
    run = _run_mock(
        tmp_path, "m5out", "dma_fence", program_dir,
        ("--drop-descriptors", "1", "--watchdog-ticks", "5000"),
    )
    output = run.stdout + run.stderr
    assert "E_RUNTIME_DEADLOCK" in output, output
    graph = output[output.find("E_RUNTIME_DEADLOCK"):]
    assert re.search(
        rf"fence cmd=\d+ core=\d+ scope=instance tags=[^\n]*\(cmd{owner}\)",
        graph,
    ), graph

    mapping = {}
    for row in graph.splitlines():
        match = re.search(r"fence cmd=(\d+) core=(\d+) scope=instance tags=(.*)", row)
        if match is None:
            continue
        for entry in match.group(3).split(","):
            tag = re.fullmatch(r"(\d+):(\d+)\(cmd(\d+)\)", entry)
            assert tag, entry
            key = (int(tag.group(1)), int(tag.group(2)))
            assert key not in mapping, key
            mapping[key] = int(tag.group(3))
    dma_commands = {
        row["command_id"] for row in _schedule(program_dir)["DMA_DESCRIPTORS"]
    }
    assert mapping, graph
    assert owner in set(mapping.values()), mapping
    assert set(mapping.values()) <= dma_commands, (mapping, dma_commands)
    assert len(mapping) == len(set(mapping)), mapping


def test_watchdog_reports_admission_window_occupancy(tmp_path):
    # CPP-05 command-FIFO observation: with the window saturated by an admitted
    # command that cannot retire, the decode head is blocked by capacity and
    # the graph reports the occupancy.  The same fixture at the default window
    # shows only the dependency wait, which is what makes the occupancy edge a
    # capacity fact instead of a restatement of the dependency.
    for window, expect_occupancy in ((1, True), (8, False)):
        case_dir = tmp_path / f"window{window}"
        case_dir.mkdir(parents=True, exist_ok=True)
        arch = _arch_with_admit_window(case_dir, window, depth=16)
        program_dir = _build_program_with_arch(case_dir, "multi_descriptor", arch)
        owner = _descriptor_command(program_dir, 1)
        head = _next_command_after(program_dir, owner)
        run = _run_mock(
            case_dir,
            "m5out",
            "multi_descriptor",
            program_dir,
            (
                "--arch",
                str(arch),
                "--drop-descriptors",
                "1",
                "--watchdog-ticks",
                "5000",
            ),
        )
        output = run.stdout + run.stderr
        assert "E_RUNTIME_DEADLOCK" in output, output
        graph = output[output.find("E_RUNTIME_DEADLOCK"):]
        assert "wait-event" in graph, graph
        occupancy = (
            f"window-blocked cmd={head} generation=0 stream=0 occupied=1/1"
        )
        if expect_occupancy:
            assert occupancy in graph, graph
        else:
            assert "window-blocked" not in graph, graph


def test_watchdog_ignores_continuous_progress(tmp_path):
    program_dir = _build_program(tmp_path, "multi_descriptor")
    run = _run_mock(
        tmp_path,
        "m5out",
        "multi_descriptor",
        program_dir,
        ("--watchdog-ticks", "20000"),
    )
    assert run.returncode == 0, run.stdout + run.stderr


_SINGLE_DEPENDENCIES = ((1, 2), (1, 3), (2, 4), (3, 4), (4, 5), (5, 6), (6, 7))


def test_start_and_event_visibility_use_core_edges(tmp_path):
    program_dir = _build_program(tmp_path, "single")
    run = _run_mock(tmp_path, "m5out", "single", program_dir)
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads((program_dir / "actual_result.json").read_text())
    period = result["core_clock_period_ticks"]
    core = result["cores"][0]
    issue = {int(key): value for key, value in core["command_issue_ticks"].items()}
    done = {int(key): value for key, value in core["command_done_ticks"].items()}
    assert issue[1] % period == 0
    for producer, consumer in _SINGLE_DEPENDENCIES:
        if producer in done and consumer in issue:
            assert issue[consumer] >= done[producer] + period, (
                producer,
                consumer,
            )


def test_mock_arbitration_is_reproducible(tmp_path):
    program_dir = _build_program(tmp_path, "dual")
    first = _run_mock(tmp_path, "m5out-first", "dual", program_dir)
    assert first.returncode == 0, first.stderr
    first_result = (program_dir / "actual_result.json").read_bytes()
    second = _run_mock(tmp_path, "m5out-second", "dual", program_dir)
    assert second.returncode == 0, second.stderr
    assert (program_dir / "actual_result.json").read_bytes() == first_result


def _emit_bindings(tmp_path, program):
    path = tmp_path / f"{program}.bindings.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mesh_ir.cli",
            "bindings",
            "--program",
            program,
            "--arch",
            str(ARCH),
            "--out",
            str(path),
        ],
        cwd=MESH_IR_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(path.read_text())


def _run_mock_with_bindings(tmp_path, program, program_dir, bindings):
    path = tmp_path / f"{program}.dispatch.json"
    path.write_text(json.dumps(bindings), encoding="utf-8")
    return _run_mock(
        tmp_path, "m5out", program, program_dir, ("--bindings-file", str(path))
    )


def test_reference_and_shifted_bindings_both_run(tmp_path):
    program_dir = _build_program(tmp_path, "single")
    reference = _emit_bindings(tmp_path, "single")
    baseline = _run_mock_with_bindings(tmp_path, "single", program_dir, reference)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    shifted = [dict(item) for item in reference]
    shifted[-1]["allocation_offset_bytes"] += 0x1000
    relocated = _run_mock_with_bindings(tmp_path, "single", program_dir, shifted)
    assert relocated.returncode == 0, relocated.stdout + relocated.stderr


def _shifted_output_bindings(tmp_path, program, delta):
    bindings = _emit_bindings(tmp_path, program)
    for item in bindings:
        if item["access"] == 2:
            item["allocation_offset_bytes"] += delta
    return bindings


def _derive_invocation_traffic(tmp_path, program, bindings):
    bindings_path = tmp_path / f"{program}.shifted.bindings.json"
    bindings_path.write_text(json.dumps(bindings), encoding="utf-8")
    traffic_path = tmp_path / f"{program}.shifted.traffic.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mesh_ir.cli",
            "bindings",
            "--program",
            program,
            "--arch",
            str(ARCH),
            "--out",
            str(tmp_path / f"{program}.echo.json"),
            "--bindings-in",
            str(bindings_path),
            "--traffic-out",
            str(traffic_path),
        ],
        cwd=MESH_IR_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return bindings_path, traffic_path


def test_alternate_binding_derives_its_own_traffic_oracle(tmp_path):
    # +0x100 moves the STORE across the 4 KiB burst split, so only the
    # invocation-derived oracle can accept it; the published reference oracle
    # must reject the same run.
    program_dir = _build_program(tmp_path, "single")
    bindings = _shifted_output_bindings(tmp_path, "single", 0x100)
    bindings_path, traffic_path = _derive_invocation_traffic(
        tmp_path, "single", bindings
    )
    run = _run_mock(
        tmp_path,
        "m5out",
        "single",
        program_dir,
        (
            "--bindings-file",
            str(bindings_path),
            "--expected-traffic",
            str(traffic_path),
        ),
    )
    assert run.returncode == 0, run.stdout + run.stderr
    reference_run = _run_mock(
        tmp_path,
        "m5out-reference-oracle",
        "single",
        program_dir,
        ("--bindings-file", str(bindings_path)),
    )
    assert reference_run.returncode != 0
    assert "mismatch" in (reference_run.stdout + reference_run.stderr)


def _drop_slot(bindings):
    bindings.pop()


def _add_extra_slot(bindings):
    extra = dict(bindings[0])
    extra["slot_id"] = 4096
    bindings.append(extra)


def _repeat_slot(bindings):
    bindings.append(dict(bindings[0]))


def _misalign(bindings):
    bindings[0]["allocation_alignment_bytes"] = 48


def _overflow_region(bindings):
    bindings[0]["allocation_offset_bytes"] = 0x400000000


@pytest.mark.parametrize(
    "mutate",
    [_drop_slot, _add_extra_slot, _repeat_slot, _misalign, _overflow_region],
    ids=["missing", "extra", "repeated", "misaligned", "beyond-region"],
)
def test_binding_rejection_precedes_any_core_execution(tmp_path, mutate):
    program_dir = _build_program(tmp_path, "single")
    bindings = _emit_bindings(tmp_path, "single")
    mutate(bindings)
    run = _run_mock_with_bindings(tmp_path, "single", program_dir, bindings)
    assert run.returncode != 0
    output = run.stdout + run.stderr
    assert "E_RELOCATION" in output
    assert "MESH_PROGRAM_DONE" not in output
    assert "MESH_E2E_PASS" not in output


def test_overlapping_writable_bindings_are_rejected(tmp_path):
    program_dir = _build_program(tmp_path, "single")
    bindings = _emit_bindings(tmp_path, "single")
    writable = next(item for item in bindings if item["access"] == 2)
    readable = next(
        item
        for item in bindings
        if item["access"] == 1
        and item["region_id"] == writable["region_id"]
        and item["owner_core"] == writable["owner_core"]
    )
    writable["allocation_offset_bytes"] = readable["allocation_offset_bytes"]
    run = _run_mock_with_bindings(tmp_path, "single", program_dir, bindings)
    assert run.returncode != 0
    output = run.stdout + run.stderr
    assert "E_RELOCATION" in output
    assert "MESH_PROGRAM_DONE" not in output
