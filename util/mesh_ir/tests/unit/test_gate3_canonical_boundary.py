import json

import pytest

from mesh_ir.canonical import JSON_SAFE_INTEGER_MAX, strict_json_loads, to_canonical
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A


def test_canonical_integer_policy_is_generated_and_bounded():
    assert JSON_SAFE_INTEGER_MAX == A.JSON_SAFE_INTEGER_MAX
    assert to_canonical(A.JSON_SAFE_INTEGER_MAX) == A.JSON_SAFE_INTEGER_MAX
    assert to_canonical(A.JSON_SAFE_INTEGER_MAX + 1) == "0x20000000000000"
    for value in (-(1 << 63) - 1, 1 << 64):
        with pytest.raises(MeshIrError) as error:
            to_canonical(value)
        assert error.value.code == "E_CONFIG"


@pytest.mark.parametrize(
    "payload",
    (
        "9" * 5000,
        b'"\x80"',
        '"\\ud800"',
    ),
)
def test_strict_json_failures_are_catalog_errors(payload):
    with pytest.raises(MeshIrError) as error:
        strict_json_loads(payload)
    assert error.value.code == "E_CONFIG"


def test_invalid_unicode_diagnostic_context_remains_valid_jsonl():
    error = MeshIrError("E_CONFIG", "invalid text", value="\ud800", nested={"key": "\udfff"})
    encoded = error.to_jsonl().encode("utf-8")
    document = json.loads(encoded)
    assert document["context"] == {
        "nested": {"key": {"invalid_string": "unicode"}},
        "value": {"invalid_string": "unicode"},
    }
