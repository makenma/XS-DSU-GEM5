import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.builder import load_arch
from mesh_ir.model import MeshIrError

ARCH_PATH = Path(__file__).resolve().parents[4] / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


def _load_variant(text):
    path = Path(tempfile.mkdtemp()) / "arch.yaml"
    path.write_text(text)
    return load_arch(path)


def test_valid_arch_loads():
    load_arch(ARCH_PATH)


def test_zero_queue_rejected():
    base = ARCH_PATH.read_text()
    with pytest.raises(MeshIrError) as err:
        _load_variant(base.replace("descriptor_queue_depth: 16",
                                   "descriptor_queue_depth: 0"))
    assert err.value.code == "E_CAPABILITY_MISMATCH"


def test_axi_width_not_pow2_rejected():
    base = ARCH_PATH.read_text()
    with pytest.raises(MeshIrError) as err:
        _load_variant(base.replace("data_bytes: 32", "data_bytes: 24"))
    assert err.value.code == "E_CAPABILITY_MISMATCH"


def test_axi_id_bits_out_of_range_rejected():
    base = ARCH_PATH.read_text()
    with pytest.raises(MeshIrError) as err:
        _load_variant(base.replace("id_bits: 8", "id_bits: 17"))
    assert err.value.code == "E_CAPABILITY_MISMATCH"


def test_missing_throughput_rejected():
    base = ARCH_PATH.read_text()
    with pytest.raises(MeshIrError) as err:
        _load_variant(base.replace(
            "macs_per_cycle: {fp16: 256, bf16: 256, fp32: 64, int8: 512}",
            "macs_per_cycle: {}"))
    assert err.value.code == "E_CAPABILITY_MISMATCH"


def test_zero_clock_rejected():
    base = ARCH_PATH.read_text()
    with pytest.raises(MeshIrError) as err:
        _load_variant(base.replace("clock_hz: 2000000000", "clock_hz: 0"))
    assert err.value.code == "E_CAPABILITY_MISMATCH"
