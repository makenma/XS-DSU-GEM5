import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[5]
DRIVER_SRC = Path(__file__).with_name("mutation_driver.cc")
INCLUDES = (REPO / "src", REPO / "src/dev/ai_mesh")
DRIVER_SOURCES = (
    "mesh_binary.cc",
    "mesh_binary_envelope.cc",
    "mesh_binary_storage.cc",
    "mesh_binary_validation.cc",
    "mesh_binary_canonical.cc",
    "mesh_canonical.cc",
    "mesh_hash.cc",
    "mesh_ir_computation_verifier.cc",
    "mesh_ir_logical_computation_verifier.cc",
    "mesh_ir_physical_operation_verifier.cc",
    "mesh_ir_compute_verifier.cc",
    "mesh_ir_control_dependency_verifier.cc",
    "mesh_ir_control_projection.cc",
    "mesh_ir_dependency_graph.cc",
    "mesh_ir_backing.cc",
    "mesh_ir_lifetime_verifier.cc",
    "mesh_ir_dma_verifier.cc",
    "mesh_ir_intrinsic_dependency_facts.cc",
    "mesh_ir_intrinsic_memory_verifier.cc",
    "mesh_ir_region.cc",
    "mesh_ir_semantic_context.cc",
    "mesh_ir_verifier.cc",
    "mesh_splitter.cc",
)


@pytest.fixture(scope="session")
def driver(tmp_path_factory):
    executable = tmp_path_factory.mktemp("mutation-driver") / "mutation_driver"
    compiled = subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-g",
            "-O1",
            "-fsanitize=address,undefined",
            "-fno-sanitize-recover=all",
            *(f"-I{include}" for include in INCLUDES),
            str(DRIVER_SRC),
            *(str(REPO / "src/dev/ai_mesh" / source) for source in DRIVER_SOURCES),
            "-lisl",
            "-o",
            str(executable),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    return str(executable)
