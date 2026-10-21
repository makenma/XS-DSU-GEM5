from pathlib import Path

from mesh_ir.architecture import load_arch
from mesh_ir.scheduled.verify import verify_program
from tests.golden.support.stage4_backing_cases import (
    build_persistent_authored_program,
)


ROOT = Path(__file__).resolve().parents[4]
ARCH = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")


def test_authored_persistent_object_projects_its_allocation_flags():
    program = build_persistent_authored_program(ARCH)
    persistent_object = next(item for item in program.semantics.objects if item.persistent)
    backing = next(
        item.backing
        for item in program.semantics.object_backings
        if item.object_id == persistent_object.object_id
    )
    allocation = program.allocations[backing.allocation_id - 1]

    assert allocation.flags == int(persistent_object.persistent)
    assert verify_program(program, ARCH).program is program
