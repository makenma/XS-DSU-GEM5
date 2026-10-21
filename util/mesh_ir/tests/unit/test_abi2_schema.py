from pathlib import Path

import pytest
import yaml

from mesh_ir.abi.schema_loader import load_schema


SCHEMA = Path(__file__).resolve().parents[2] / "mesh_ir/abi/mesh_ir_abi.yaml"


def test_unified_schema_has_one_identity_per_feature_and_section():
    schema = load_schema(SCHEMA)
    assert schema["abi"] == {"major": 2, "minor": 0, "min_reader_minor": 0}
    features = schema["features"]
    assert {name: row["bit"] for name, row in features.items()} == {
        "SCHEDULED_SEMANTICS": 1,
        "DYNAMIC_MOE_V1": 2,
        "AGENT_SERVING_V1": 4,
        "PROFILE_SCOPED_EXECUTION_V1": 8,
    }
    assert [name for name, row in features.items() if row["mandatory"]] == ["SCHEDULED_SEMANTICS"]
    assert "PROFILE_SCOPED_EXECUTION_V1" in features["AGENT_SERVING_V1"]["requires"]
    sections = schema["enums"]["section_type"]
    assert len(set(sections.values())) == len(sections)
    assert sections["PROFILE_STREAM_RANGES"] != sections["PROGRAM_METADATA"]
    assert schema["records"]["CONTENT_DIGESTS"]["fields"][0]["enum"] == "content_digest_object_kind"
    assert schema["blob_sections"]["STRINGS"]["header_fields"] == [{"name": "count", "type": "u32", "offset": 0}]


@pytest.mark.parametrize("damage", ("duplicate_mask", "non_mask", "unknown_dependency", "dependency_cycle", "unknown_section", "gate_unknown_section", "gate_unknown_feature", "gate_duplicate_feature", "gate_empty"))
def test_unified_schema_rejects_ambiguous_feature_contracts(tmp_path, damage):
    raw = yaml.safe_load(SCHEMA.read_text())
    features = raw["features"]
    if damage == "duplicate_mask":
        features["DYNAMIC_MOE_V1"]["bit"] = 1
    elif damage == "non_mask":
        features["DYNAMIC_MOE_V1"]["bit"] = 3
    elif damage == "unknown_dependency":
        features["DYNAMIC_MOE_V1"]["requires"] = ["ABSENT"]
    elif damage == "dependency_cycle":
        features["PROFILE_SCOPED_EXECUTION_V1"]["requires"] = ["AGENT_SERVING_V1"]
    elif damage == "unknown_section":
        raw["conditional_required_sections"]["DYNAMIC_MOE_V1"] = ["ABSENT"]
    elif damage == "gate_unknown_section":
        raw["section_required_features"]["ABSENT"] = ["DYNAMIC_MOE_V1"]
    elif damage == "gate_unknown_feature":
        raw["section_required_features"]["PROFILE_STREAM_RANGES"] = ["ABSENT"]
    elif damage == "gate_duplicate_feature":
        raw["section_required_features"]["PROFILE_STREAM_RANGES"] *= 2
    else:
        raw["section_required_features"]["PROFILE_STREAM_RANGES"] = []
    path = tmp_path / "schema.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    with pytest.raises(ValueError):
        load_schema(path)
