"""Gate 5 determinism: two independent runs of one carrier must agree byte for
byte on every semantic artefact.

The archived result holds no path, directory or environment metadata, so the
comparison is the raw canonical JSON: ticks, event order, content, counts and
the drain window are all semantic and none of them may differ.  Only a genuine
non-semantic field would be excluded, and there is none.
"""

import json

import pytest

from tests.integration.support.garnet_harness import (
    build_program,
    cause_of,
    output_of,
    result_of,
    run_garnet,
)

CARRIERS = (
    ("e2e1_load_gemm_store", "dma_basic", "single", ()),
    ("e2e2_p2p_reduce_store", "p2p_basic", "dual", ()),
    ("drain_error", "write_error_post_commit", "single", ()),
    ("unaligned_sentinels", "dma_edge", "dma_edge", ()),
    ("shallow_queue_staged_p2p", "constrained", "dual", ()),
)


def _canonical(result):
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def _first_difference(left, right, path="result"):
    if type(left) is not type(right):
        return f"{path}: {type(left).__name__} vs {type(right).__name__}"
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return f"{path}: keys {sorted(left)} vs {sorted(right)}"
        for key in sorted(left):
            found = _first_difference(left[key], right[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return f"{path}: length {len(left)} vs {len(right)}"
        for index, (l_item, r_item) in enumerate(zip(left, right)):
            found = _first_difference(l_item, r_item, f"{path}[{index}]")
            if found:
                return found
        return None
    if left != right:
        return f"{path}: {left!r} vs {right!r}"
    return None


def _run(tmp_path, case, program, extra):
    program_dir = build_program(tmp_path, program)
    run = run_garnet(tmp_path, "m5out", case, program_dir, extra_args=tuple(extra))
    assert run.returncode == 0, output_of(run)
    return run, result_of(program_dir)


@pytest.mark.parametrize(
    "case,program,extra", [carrier[1:] for carrier in CARRIERS],
    ids=[carrier[0] for carrier in CARRIERS],
)
def test_two_independent_runs_agree_byte_for_byte(
    tmp_path_factory, case, program, extra
):
    first_run, first = _run(tmp_path_factory.mktemp("run-a"), case, program, extra)
    second_run, second = _run(
        tmp_path_factory.mktemp("run-b"), case, program, extra
    )
    assert cause_of(first_run) == cause_of(second_run), (
        cause_of(first_run),
        cause_of(second_run),
    )
    difference = _first_difference(first, second)
    assert difference is None, difference
    assert _canonical(first) == _canonical(second)
