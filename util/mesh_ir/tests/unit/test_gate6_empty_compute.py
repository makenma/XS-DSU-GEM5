from pathlib import Path

from mesh_ir.architecture import load_arch
from mesh_ir.compute_timing import compute_cycles
from mesh_ir.generated import abi as A
from mesh_ir.model import OpAttr


def test_empty_reduce_has_no_engine_work():
    root = Path(__file__).resolve().parents[4]
    arch = load_arch(root / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    names = tuple(field["name"] for field in A.REDUCE_V1_FIELDS)
    values = {"element_count": 0, "fan_in": 2, "dtype": 2}
    attr = OpAttr(A.ATTR_KIND.REDUCE_V1, payload=tuple(values.get(name, 0) for name in names), payload_fields=names)
    assert compute_cycles(arch, "LOCAL_REDUCE", attr) == 0
