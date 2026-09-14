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
from mesh_ir.moe_programs import (
    moe_dual_copy_program,
    moe_dual_drop_program,
    moe_dual_program,
    moe_min_program,
    moe_multi_program,
    moe_quad_program,
    synthetic_weight_digest,
)

FIXTURES = Path(__file__).resolve().parent
ARCH_PATH = REPO / "configs/example/ai_mesh/arch/mesh_1x2_moe.yaml"
QUAD_ARCH_PATH = REPO / "configs/example/ai_mesh/arch/mesh_4x4_moe.yaml"


def expected_of(program) -> dict:
    return {
        "abi_major": program.abi_major,
        "abi_minor": program.abi_minor,
        "required_features": program.required_features,
        "semantic_sha256": program.semantic_sha256(),
        "content_digests": [canonical(r) for r in program.content_digests],
        "moe_layer_specs": [canonical(r) for r in program.moe_layer_specs],
        "moe_expert_specs": [canonical(r) for r in program.moe_expert_specs],
        "moe_dynamic_regions": [
            canonical(r) for r in program.moe_dynamic_regions],
        "moe_kernel_specs": [canonical(r) for r in program.moe_kernel_specs],
        "weight_digest_hex": synthetic_weight_digest().hex(),
    }


def main() -> int:
    arch = load_arch(ARCH_PATH)
    program = moe_min_program(arch)
    verify_program(program, arch)
    blob = encode_program(program)
    decoded = decode_program(blob)
    assert encode_program(decoded) == blob
    assert decoded.semantic_sha256() == program.semantic_sha256()
    (FIXTURES / "moe_min.mshb").write_bytes(blob)
    dual = moe_dual_program(arch)
    verify_program(dual, arch)
    dual_blob = encode_program(dual)
    dual_decoded = decode_program(dual_blob)
    assert encode_program(dual_decoded) == dual_blob
    (FIXTURES / "moe_dual.mshb").write_bytes(dual_blob)
    (FIXTURES / "moe_dual_expected.json").write_text(
        json.dumps(expected_of(dual_decoded), indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    drop = moe_dual_drop_program(arch)
    verify_program(drop, arch)
    drop_blob = encode_program(drop)
    drop_decoded = decode_program(drop_blob)
    assert encode_program(drop_decoded) == drop_blob
    (FIXTURES / "moe_dual_drop.mshb").write_bytes(drop_blob)
    (FIXTURES / "moe_dual_drop_expected.json").write_text(
        json.dumps(expected_of(drop_decoded), indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    copy = moe_dual_copy_program(arch)
    verify_program(copy, arch)
    copy_blob = encode_program(copy)
    copy_decoded = decode_program(copy_blob)
    assert encode_program(copy_decoded) == copy_blob
    (FIXTURES / "moe_dual_copy.mshb").write_bytes(copy_blob)
    (FIXTURES / "moe_dual_copy_expected.json").write_text(
        json.dumps(expected_of(copy_decoded), indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    multi = moe_multi_program(arch)
    verify_program(multi, arch)
    multi_blob = encode_program(multi)
    multi_decoded = decode_program(multi_blob)
    assert encode_program(multi_decoded) == multi_blob
    (FIXTURES / "moe_multi.mshb").write_bytes(multi_blob)
    (FIXTURES / "moe_multi_expected.json").write_text(
        json.dumps(expected_of(multi_decoded), indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    quad_arch = load_arch(QUAD_ARCH_PATH)
    quad = moe_quad_program(quad_arch)
    verify_program(quad, quad_arch)
    quad_blob = encode_program(quad)
    quad_decoded = decode_program(quad_blob)
    assert encode_program(quad_decoded) == quad_blob
    (FIXTURES / "moe_quad.mshb").write_bytes(quad_blob)
    (FIXTURES / "moe_quad_expected.json").write_text(
        json.dumps(expected_of(quad_decoded), indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    (FIXTURES / "moe_min_expected.json").write_text(
        json.dumps(expected_of(decoded), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote moe_min.mshb ({len(blob)} B), moe_dual.mshb "
          f"({len(dual_blob)} B), moe_quad.mshb ({len(quad_blob)} B)")
    weight_builder = FIXTURES / "build_gate5_weight_artifacts.py"
    if weight_builder.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("gate5_weights",
                                                      weight_builder)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
