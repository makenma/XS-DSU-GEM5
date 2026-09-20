import copy
import json

import pytest

from mesh_ir.runtime_reconciliation import (
    ReconciliationError,
    verified_read_window,
)

from tests.integration.support.garnet_harness import (
    ARCH,
    REPO,
    build_program,
    build_program_with_arch,
    result_of,
    run_garnet,
)


def arch_text_with(replacements):
    text = ARCH.read_text()
    for old, new in replacements:
        assert old in text, old
        text = text.replace(old, new)
    return text


def run_dma(tmp_path, program, case, arch_text=None, extra=()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    arch = tmp_path / "arch.yaml"
    arch.write_text(arch_text if arch_text is not None else ARCH.read_text())
    program_dir = build_program_with_arch(tmp_path, program, arch)
    run = run_garnet(tmp_path, "m5out", case, program_dir, arch=arch, extra_args=extra)
    assert run.returncode == 0, run.stdout + run.stderr
    return (
        result_of(program_dir),
        json.loads((tmp_path / "m5out/config.json").read_text())["system"],
    )


def test_queue_architecture_and_each_override_are_effective(tmp_path):
    text = arch_text_with(
        (
            ("segment_queue_depth: 32", "segment_queue_depth: 1"),
            ("descriptor_queue_depth: 16", "descriptor_queue_depth: 1"),
        )
    )
    digests = set()
    for segment, descriptor in ((1, 1), (4, 1), (8, 1), (1, 2)):
        extra = () if (segment, descriptor) == (1, 1) else (
            "--segment-queue-depth", str(segment),
            "--dma-descriptor-queue-depth", str(descriptor))
        _, system = run_dma(
            tmp_path / f"q{segment}_{descriptor}", "load_saturation_contiguous",
            "load_saturation_contiguous", text, extra=extra)
        for core in (0, 1):
            assert system[f"mesh_core_{core}"]["dma"]["segment_queue_depth"] == segment
            assert system[f"mesh_core_{core}"]["dma"]["descriptor_queue_depth"] == descriptor
            assert system[f"mesh_bridge_{core}"]["ar_queue_depth"] == segment
            assert system[f"mesh_bridge_{core}"]["aw_queue_depth"] == segment
        digests.add(system["mesh_loader"]["effective_arch_digest"])
    assert len(digests) == 4


def test_window_reuses_four_ids_before_first_burst_commit(tmp_path):
    result, system = run_dma(tmp_path, "read_window", "read_outstanding_window")
    assert system["mesh_core_0"]["dma"]["axi_id_count"] == 4
    assert system["mesh_core_0"]["sram_write_bytes_per_cycle"] == 1
    assert len(result["burst_timings"]) == 24
    assert all(row["response_tick"] < row["commit_tick"]
               for row in result["burst_timings"])


def test_r_backpressure_is_reported_at_target_and_network(tmp_path):
    result, _ = run_dma(
        tmp_path, "load_saturation_contiguous", "load_saturation_contiguous",
        extra=("--read-outstanding", "4", "--garnet-buffers-per-vnet", "2,2,2,2,2",
               "--axi-message-buffer-depths", "8,32,8,16,1"))
    assert result["core_clock_period_ticks"] == 500
    targets = result["target_message_buffer_blocked_cycles"]
    assert targets["system.mesh_endpoint"]["R"] > 0
    assert result["network_stalls"]["R"]["ni_vc_busy_cycles"] > 0


WINDOW_LIMIT = 4
WINDOW_RUN = ("--read-outstanding", str(WINDOW_LIMIT))


def _read_window(result):
    return verified_read_window(
        result["burst_timings"], result["read_window"], result["bridges"],
        limit=WINDOW_LIMIT, max_beats=8, beat_bytes=32,
    )


def test_the_read_window_and_every_owner_agree(tmp_path):
    result, _ = run_dma(
        tmp_path, "read_window", "read_outstanding_window", extra=WINDOW_RUN
    )
    summary = _read_window(result)
    assert summary["limit"] == WINDOW_LIMIT, summary
    window = result["read_window"]
    reads = sorted(
        (row for row in result["burst_timings"] if row["channel"] == "AR"),
        key=lambda row: (row["ar_aw_tick"], row["ordinal"]),
    )
    # The engine's own window peak, the bridge's accounting and the admitted
    # limit are one fact.
    assert window["segment_peak"] == max(
        bridge["peak_read_outstanding"] for bridge in result["bridges"]
    ) == summary["limit"]
    # The bridge's accept ticks and the engine's burst ticks are one fact.
    assert window["ar_accept_ticks"] == sorted(
        row["ar_aw_tick"] for row in reads
    )
    # The first credit is released at the earliest RLAST, before any local
    # SRAM commit: the ID lifecycle and the retirement lifecycle differ.
    assert window["first_credit_release_tick"] == min(
        row["response_tick"] for row in reads
    )
    assert window["first_credit_release_tick"] < min(
        row["commit_tick"] for row in reads if row["commit_tick"]
    )


@pytest.mark.parametrize(
    "tamper, message",
    (
        ("engine-peak", "engine read window peaked"),
        ("bridge-peak", "adapter read window peak differs"),
        ("accepts", "AR accepts differ"),
        ("credit-tick", "earliest RLAST"),
        ("refill-before-commit", "refill must precede"),
        ("id-reuse", "reused before its RLAST"),
    ),
)
def test_the_read_window_entry_rejects_corruption(tmp_path, tamper, message):
    result, _ = run_dma(
        tmp_path, "read_window", "read_outstanding_window", extra=WINDOW_RUN
    )
    corrupted = copy.deepcopy(result)
    reads = sorted(
        (row for row in corrupted["burst_timings"] if row["channel"] == "AR"),
        key=lambda row: (row["ar_aw_tick"], row["ordinal"]),
    )
    if tamper == "engine-peak":
        corrupted["read_window"]["segment_peak"] = 3
    elif tamper == "bridge-peak":
        corrupted["bridges"][0]["peak_read_outstanding"] = 3
    elif tamper == "accepts":
        corrupted["read_window"]["ar_accept_ticks"] = (
            corrupted["read_window"]["ar_accept_ticks"][:-1]
        )
    elif tamper == "credit-tick":
        corrupted["read_window"]["first_credit_release_tick"] = 1
    elif tamper == "refill-before-commit":
        # The first burst only commits after the refill AR has already left.
        reads[0]["commit_tick"] = reads[4]["ar_aw_tick"]
    elif tamper == "id-reuse":
        # Swap two IDs: the pool stays bounded, but the second burst now holds an
        # ID that is still in flight.
        reads[1]["axi_id"], reads[2]["axi_id"] = (
            reads[2]["axi_id"], reads[1]["axi_id"],
        )
    with pytest.raises(ReconciliationError, match=message):
        _read_window(corrupted)
