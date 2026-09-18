import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "util" / "mesh_ir"))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.abi.verifier import verify_program
from mesh_ir.builder import load_arch
from mesh_ir.model import canonical
from mesh_ir.serving_profiles import (
    apply_request_profile_keys,
    program_profile_key_base_digest,
    serving_capacity_required,
)
from mesh_ir.serving_programs import (
    full_view_serving_program,
    keyed_serving_program,
    serving_program,
    split_kv_serving_program,
)

FIXTURES = Path(__file__).resolve().parent
ARCH_PATH = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"

SECTIONS = (
    "agent_request_profiles",
    "agent_instance_profiles",
    "agent_source_core_map",
    "agent_instance_member_bindings",
    "agent_request_binding_requirements",
    "agent_publish_surrogate_bindings",
)


def write_schedule(name, program) -> None:
    (FIXTURES / (name + "_schedule.mesh.json")).write_text(
        json.dumps(program.canonical_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def expected_of(program) -> dict:
    document = {
        "abi_major": program.abi_major,
        "abi_minor": program.abi_minor,
        "required_features": program.required_features,
        "semantic_sha256": program.semantic_sha256(),
        "profile_key_base_digest": program_profile_key_base_digest(
            program).hex(),
        "capacity_required": serving_capacity_required(program).fields(),
    }
    for name in SECTIONS:
        document[name] = [canonical(record)
                          for record in getattr(program, name)]
    return document


def main() -> int:
    arch = load_arch(ARCH_PATH)
    program = keyed_serving_program(arch)
    verify_program(program, arch)
    blob = encode_program(program)
    decoded = decode_program(blob)
    assert encode_program(decoded) == blob
    assert decoded.semantic_sha256() == program.semantic_sha256()
    two_tokens = apply_request_profile_keys(
        serving_program(arch, output_tokens=2))
    verify_program(two_tokens, arch)
    two_blob = encode_program(two_tokens)
    two_decoded = decode_program(two_blob)
    assert encode_program(two_decoded) == two_blob
    assert two_decoded.semantic_sha256() == two_tokens.semantic_sha256()
    (FIXTURES / "serving_two_tokens.mshb").write_bytes(two_blob)
    write_schedule("serving_two_tokens", two_decoded)
    (FIXTURES / "serving_two_tokens_expected.json").write_text(
        json.dumps(expected_of(two_decoded), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (FIXTURES / "serving_min.mshb").write_bytes(blob)
    (FIXTURES / "serving_min_expected.json").write_text(
        json.dumps(expected_of(decoded), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_schedule("serving_min", decoded)
    split_kv = apply_request_profile_keys(split_kv_serving_program(arch))
    verify_program(split_kv, arch)
    split_blob = encode_program(split_kv)
    split_decoded = decode_program(split_blob)
    assert encode_program(split_decoded) == split_blob
    assert split_decoded.semantic_sha256() == split_kv.semantic_sha256()
    (FIXTURES / "serving_split_kv.mshb").write_bytes(split_blob)
    write_schedule("serving_split_kv", split_decoded)
    (FIXTURES / "serving_split_kv_expected.json").write_text(
        json.dumps(expected_of(split_decoded), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    full_view = apply_request_profile_keys(full_view_serving_program(arch))
    verify_program(full_view, arch)
    view_blob = encode_program(full_view)
    view_decoded = decode_program(view_blob)
    assert encode_program(view_decoded) == view_blob
    (FIXTURES / "serving_fullview.mshb").write_bytes(view_blob)
    (FIXTURES / "serving_fullview_expected.json").write_text(
        json.dumps(expected_of(view_decoded), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_schedule("serving_fullview", view_decoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
