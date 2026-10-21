import json
import math
from enum import IntEnum

import pytest

from mesh_ir.canonical import canonical_json_bytes, checked_u64, strict_json_loads
from mesh_ir.diagnostics import MeshIrError


def test_canonical_json_is_utf8_sorted_compact_and_normalizes_large_unsigned():
    encoded = canonical_json_bytes({"z": "香山", "address": 2**53, "items": [3, 2, 1]})
    assert encoded == b'{"address":"0x20000000000000","items":[3,2,1],"z":"\xe9\xa6\x99\xe5\xb1\xb1"}'


@pytest.mark.parametrize("payload", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_strict_json_rejects_duplicates_and_nonfinite(payload):
    with pytest.raises(MeshIrError) as error:
        strict_json_loads(payload)
    assert error.value.code == "E_CONFIG"


def test_strict_json_rejects_overflowed_floating_point():
    with pytest.raises(MeshIrError) as error:
        strict_json_loads('{"value":1e999}')
    assert error.value.code == "E_CONFIG"


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_json_rejects_nonfinite_scalars(value):
    with pytest.raises(MeshIrError) as error:
        canonical_json_bytes({"value": value})
    assert error.value.code == "E_CONFIG"


def test_diagnostic_is_catalog_backed_and_jsonl_stable():
    error = MeshIrError("E_SHAPE_UNBOUND", "profile omitted symbol", symbol="s1")
    record = json.loads(error.to_jsonl())
    assert record == {
        "code": "E_SHAPE_UNBOUND",
        "context": {"symbol": "s1"},
        "message": "profile omitted symbol",
        "severity": "error",
    }


def test_unknown_diagnostic_code_is_a_typed_config_failure():
    with pytest.raises(ValueError):
        MeshIrError("E_NOT_REGISTERED", "bad")


def test_large_int_enum_uses_the_shared_large_unsigned_encoding():
    class LargeEnum(IntEnum):
        VALUE = 2**53

    assert canonical_json_bytes({"value": LargeEnum.VALUE}) == b'{"value":"0x20000000000000"}'


def test_diagnostic_with_nonfinite_context_remains_valid_json():
    record = json.loads(MeshIrError("E_CONFIG", "bad input", value=math.nan).to_jsonl())
    assert record["context"]["value"] == {"invalid_scalar": "nan"}


def test_out_of_range_integer_error_remains_serializable():
    with pytest.raises(MeshIrError) as error:
        checked_u64(2**80)
    record = json.loads(error.value.to_jsonl())
    assert record["context"]["value"] == {"invalid_integer": "0x100000000000000000000"}
