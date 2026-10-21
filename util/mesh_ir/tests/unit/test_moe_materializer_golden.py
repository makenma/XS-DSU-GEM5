import runpy
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
FIXTURES = REPO / "tests/gem5/ai_mesh/fixtures/gate6"


@pytest.mark.parametrize("mode", range(5))
def test_runtime_materializer_golden_matches_independent_oracle(mode):
    builder = runpy.run_path(str(FIXTURES / "build_moe_materializer.py"))
    assert (FIXTURES / f"moe_materialized_{mode}.bin").read_bytes() == builder["materializer_golden"](mode)
