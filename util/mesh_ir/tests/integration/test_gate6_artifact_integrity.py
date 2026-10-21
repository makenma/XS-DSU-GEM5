import copy

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.gate3_oracle import read_facts_document
from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from mesh_ir.serving_e2e_oracle import verify_serving_run

from .serving_runtime import FIXTURES, run_serving_fixture, write_serving_fixture


@pytest.fixture(scope="module")
def real_serving_runs(tmp_path_factory):
    program = decode_program((FIXTURES / "serving_two_tokens.mshb").read_bytes())
    work = write_serving_fixture(tmp_path_factory.mktemp("artifact_integrity"), program)
    runs = []
    for index in range(3):
        mode = f"repeat_{index}"
        facts, result, metrics = run_serving_fixture(work, mode)
        document = read_facts_document(work / mode / "gate6_facts.tsv")
        assert document.fatal is None
        verify_serving_run(program, result["transport"], metrics, document.serving_phases)
        runs.append((facts, result, metrics, document))
    return program, runs


def test_three_real_runs_have_identical_execution_and_facts(real_serving_runs):
    _, runs = real_serving_runs
    for facts, result, metrics, _ in runs[1:]:
        assert facts == runs[0][0]
        assert result == runs[0][1]
        assert metrics == runs[0][2]


@pytest.mark.parametrize("field", ["write_bytes", "p2p_bytes", "fill_bytes"])
def test_real_transport_rejects_foreign_traffic_channel(real_serving_runs, field):
    program, runs = real_serving_runs
    _, result, metrics, document = runs[0]
    rows = copy.deepcopy(result["transport"])
    row = next(row for row in rows if row["dma_kind"] == A.DMA_KIND.LOAD)
    row[field] += 1
    with pytest.raises(MeshIrError, match="E_TRAFFIC_MISMATCH"):
        verify_serving_run(program, rows, metrics, document.serving_phases)


@pytest.mark.parametrize("field", ["live_contexts"])
def test_real_facts_require_explicit_zero_metrics(real_serving_runs, field):
    program, runs = real_serving_runs
    _, result, metrics, document = runs[0]
    altered = dict(metrics)
    del altered[field]
    with pytest.raises(MeshIrError, match="E_KV_STATE"):
        verify_serving_run(program, result["transport"], altered, document.serving_phases)
