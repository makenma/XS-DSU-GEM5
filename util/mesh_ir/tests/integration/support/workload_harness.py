from functools import lru_cache
from pathlib import Path
import json

import pytest
import yaml

from mesh_ir.compile_service import export_and_compile_factory
from tests.integration.support.runtime_harness import REPO, expected_traffic, schedule


ARCH = REPO / "configs/example/ai_mesh/arch/mesh_2x2.yaml"
CONSTRAINED = ("--garnet-buffers-per-vnet", "1,2,1,1,2")


def compile_workload(directory, model, *, algorithm="ring", chunk_bytes=28):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    examples = REPO / "util/mesh_ir/examples"
    config = yaml.safe_load((examples / f"tiny_{model}_compile.yaml").read_text())
    config["parallelism"]["tensor_parallel"] = 4
    config["collectives"] = {"all_reduce_algorithm": algorithm, "chunk_bytes": chunk_bytes}
    config["shape_profiles"]["forward"] = config["shape_profiles"]["forward"][:1]
    config_path = directory / "compile.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    program = directory / "program"
    export_and_compile_factory(
        f"examples.tiny_{model}:create_model", examples / f"tiny_{model}_inputs.json",
        examples / "tiny_transformer_shapes.json" if model == "transformer" else None,
        ARCH, config_path, program,
    )
    effective = json.loads((program / "effective_config.json").read_text())
    assert algorithm in json.dumps(effective)
    return program


@pytest.fixture(scope="session")
def compiled_workloads(tmp_path_factory):
    root = tmp_path_factory.mktemp("gate6-workloads")

    @lru_cache
    def compile_once(model, algorithm="ring", chunk_bytes=28):
        return compile_workload(root / f"{model}-{algorithm}-{chunk_bytes}", model,
                                algorithm=algorithm, chunk_bytes=chunk_bytes)

    return compile_once


def descriptors(program):
    kinds = {row["descriptor_id"]: row["kind"] for row in schedule(program)["DMA_DESCRIPTORS"]}
    return {row["identity"]["descriptor_id"]: {
        **row, **row["identity"], "kind": kinds[row["identity"]["descriptor_id"]]}
        for row in expected_traffic(program)}
