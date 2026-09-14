import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "util" / "mesh_ir"))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.verifier import verify_program
from mesh_ir.builder import load_arch
from mesh_ir.model import canonical_json_bytes
from mesh_ir.weight_registry import (
    TAG_TUPLE_BYTES,
    load_model_weight_image,
    load_program_weight_registry,
    materialize_content,
    tag_manifest_bytes,
    weight_tag_manifest,
)

FIXTURE_DIR = Path(__file__).resolve().parent
FIXTURES = FIXTURE_DIR.parent
GOLDEN = FIXTURES.parent / "golden"
ARCH_PATH = REPO / "configs/example/ai_mesh/arch/mesh_1x2_moe.yaml"
MOE_IMAGE = FIXTURE_DIR / "moe_min.mshb"
MOE_MULTI_IMAGE = FIXTURE_DIR / "moe_multi.mshb"
IMAGE_JSON = FIXTURES / "model_weight_image_v1.json"
REGISTRY_JSON = FIXTURES / "program_weight_bindings_v1.json"
CYCLE_GOLDEN = GOLDEN / "program_weight_registry_image_cycle_golden.json"
ARENA_BASE = 0x0000000200000000
ARENA_BYTES = 17179869184
SEED = "AI_MESH_GATE5_SYNTHETIC_WEIGHT_V1"
HOME_BYTES = 8192
HOME_ID = 1
SYMBOL_ID = 4
REGION_BYTES = 4096


def digest_of(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def u64hex(value: int) -> str:
    return f"0x{value:016x}"


def projection(document: dict, field: str) -> str:
    return digest_of(canonical_json_bytes(
        {key: value for key, value in document.items() if key != field}))


def build_image() -> dict:
    document = {
        "schema": "model_weight_image_v1",
        "version": 1,
        "arena_base": u64hex(ARENA_BASE),
        "arena_bytes": u64hex(ARENA_BYTES),
        "homes": [
            {
                "weight_home_id": HOME_ID,
                "arena_offset": u64hex(0),
                "bytes": HOME_BYTES,
                "content_sha256": digest_of(
                    materialize_content(SEED, HOME_BYTES)),
                "read_only_alias_group": 1,
                "synthetic_fill": {"seed_utf8": SEED},
            },
        ],
    }
    document["model_weight_image_digest"] = projection(
        document, "model_weight_image_digest")
    return document


def symbol_regions(program) -> list:
    content = materialize_content(SEED, HOME_BYTES)
    seen = []
    for expert in program.moe_expert_specs:
        entry = (expert.weight_region_offset, expert.weight_bytes)
        if entry in seen:
            continue
        seen.append(entry)
    return [
        {
            "region_offset": u64hex(offset),
            "bytes": byte_count,
            "resolved_content_digest": digest_of(
                content[offset:offset + byte_count]),
        }
        for offset, byte_count in sorted(seen)
    ]


def build_registry(programs, image_document: dict) -> dict:
    content = materialize_content(SEED, HOME_BYTES)
    document = {
        "schema": "program_weight_bindings_v1",
        "version": 1,
        "model_weight_image_digest":
            image_document["model_weight_image_digest"],
        "programs": [
            {
                "program_id": index + 1,
                "program_semantic_digest": program.semantic_sha256(),
                "symbols": [
                    {
                        "symbol_id": SYMBOL_ID,
                        "weight_home_id": HOME_ID,
                        "home_offset": u64hex(0),
                        "bytes": HOME_BYTES,
                        "flags": 0,
                        "resolved_content_digest": digest_of(content),
                        "read_only_alias_group": 1,
                        "regions": symbol_regions(program),
                    },
                ],
            }
            for index, program in enumerate(programs)
        ],
    }
    document["program_weight_registry_digest"] = projection(
        document, "program_weight_registry_digest")
    return document


def build_cycle_golden(program, image, registry, manifest) -> dict:
    return {
        "schema": "gate5_weight_projection_cycle_golden_v1",
        "version": 1,
        "projection_order": [
            "item",
            "program_semantic_digest",
            "model_weight_image_digest",
            "program_weight_registry_digest",
            "weight_tag_manifest_digest",
        ],
        "cycle_breaks": {
            "model_weight_image_digest": [],
            "program_weight_registry_digest": [
                "program_semantic_digest",
                "model_weight_image_digest",
            ],
            "weight_tag_manifest_digest": [
                "program_semantic_digest",
                "program_weight_registry_digest",
            ],
        },
        "item": "tests/gem5/ai_mesh/fixtures/gate5/moe_min.mshb",
        "program_semantic_digest": program.semantic_sha256(),
        "model_weight_image_digest": image.digest,
        "program_weight_registry_digest": registry.digest,
        "weight_tag_manifest_digest": digest_of(tag_manifest_bytes(manifest)),
        "weight_tag_tuple_bytes": TAG_TUPLE_BYTES,
        "weight_tag_count": len(manifest),
        "weight_tags": [
            {
                "weight_tag_index": index,
                "tuple_hex": tuple_bytes.hex(),
                "weight_symbol_id": int.from_bytes(tuple_bytes[32:36],
                                                   "little"),
                "weight_region_offset": int.from_bytes(tuple_bytes[36:44],
                                                       "little"),
                "weight_bytes": int.from_bytes(tuple_bytes[44:52], "little"),
                "resolved_content_digest": tuple_bytes[52:84].hex(),
            }
            for index, tuple_bytes in manifest
        ],
    }


RNG_GOLDEN = GOLDEN / "rng_golden.json"


def key_fields(seed, plan_digest, uid, layer_id, source_rank, slot) -> dict:
    return {
        "master_seed": seed,
        "workload_plan_digest_hex": plan_digest.hex(),
        "user_id": uid.user_id,
        "task_seq": uid.task_seq,
        "repair_round": uid.repair_round,
        "phase": uid.phase,
        "sequence_ordinal": uid.sequence_ordinal,
        "token_ordinal": uid.token_ordinal,
        "layer_id": layer_id,
        "logical_source_rank": source_rank,
        "topk_slot": slot,
        "draw_id": 0,
    }


def build_rng_golden() -> dict:
    from mesh_ir.moe_rng import (
        RNG_SCHEMA_VERSION,
        RngKey,
        categorical,
        draw,
        seed64_of,
        threshold,
        without_replacement,
    )
    from mesh_ir.moe_uid import SemanticTokenUid

    plan_digest = bytes.fromhex(
        "0123456789abcdef" * 4)
    vectors = []
    key_cases = (
        ("prefill_slot0", 20260901, SemanticTokenUid(7, 3, 11, 0, 1, 0, 0),
         1, 0, 0),
        ("decode_slot1", 20260901, SemanticTokenUid(7, 3, 11, 2, 2, 5, 0),
         1, 0, 1),
        ("big_ids", 0xFFFFFFFFFFFFFFFF,
         SemanticTokenUid(0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFF, 1, 0,
                          0xFFFFFFFF), 3, 2, 0),
    )
    for name, seed, uid, layer_id, source_rank, slot in key_cases:
        key = RngKey(master_seed=seed, workload_plan_digest=plan_digest,
                     user_id=uid.user_id, task_seq=uid.task_seq,
                     repair_round=uid.repair_round, phase=uid.phase,
                     sequence_ordinal=uid.sequence_ordinal,
                     token_ordinal=uid.token_ordinal, layer_id=layer_id,
                     logical_source_rank=source_rank, topk_slot=slot,
                     draw_id=0)
        weights = [1 << 28, 1 << 29, 1 << 30, 1 << 31]
        total = sum(weights)
        r = draw(key)
        vectors.append({
            "name": name,
            "key_fields": key_fields(seed, plan_digest, uid, layer_id,
                                     source_rank, slot),
            "key_hex": key.encode().hex(),
            "sha256_hex": digest_of(key.encode()),
            "seed64": seed64_of(key),
            "splitmix64": r,
            "weights": weights,
            "total": total,
            "threshold": threshold(total, r),
            "selected_index": categorical(weights, key),
        })
    uniform = []
    for name, expert_count, top_k, seed in (
            ("e8k4", 8, 4, 20260901), ("e2k2", 2, 2, 7),
            ("e16k1", 16, 1, 0)):
        base = RngKey(master_seed=seed, workload_plan_digest=plan_digest,
                      user_id=1, task_seq=2, repair_round=0, phase=2,
                      sequence_ordinal=9, token_ordinal=0, layer_id=1,
                      logical_source_rank=0, topk_slot=0, draw_id=0)
        uniform.append({
            "name": name,
            "expert_count": expert_count,
            "top_k": top_k,
            "key_fields": {
                "master_seed": seed,
                "workload_plan_digest_hex": plan_digest.hex(),
                "user_id": 1,
                "task_seq": 2,
                "repair_round": 0,
                "phase": 2,
                "sequence_ordinal": 9,
                "token_ordinal": 0,
                "layer_id": 1,
                "logical_source_rank": 0,
                "topk_slot": 0,
                "draw_id": 0,
            },
            "key_hex": base.encode().hex(),
            "selected": without_replacement(
                expert_count, top_k,
                lambda slot, base=base: RngKey(
                    master_seed=base.master_seed,
                    workload_plan_digest=base.workload_plan_digest,
                    user_id=base.user_id, task_seq=base.task_seq,
                    repair_round=base.repair_round, phase=base.phase,
                    sequence_ordinal=base.sequence_ordinal,
                    token_ordinal=base.token_ordinal, layer_id=base.layer_id,
                    logical_source_rank=base.logical_source_rank,
                    topk_slot=slot, draw_id=0)),
        })
    return {
        "schema": "gate5_rng_golden_v1",
        "version": 1,
        "rng_schema_version": RNG_SCHEMA_VERSION,
        "key_bytes": 80,
        "vectors": vectors,
        "uniform_vectors": uniform,
    }


def main() -> int:
    arch = load_arch(ARCH_PATH)
    program = decode_program(MOE_IMAGE.read_bytes())
    verify_program(program, arch)
    multi = decode_program(MOE_MULTI_IMAGE.read_bytes())
    verify_program(multi, arch)
    image_document = build_image()
    registry_document = build_registry([program, multi], image_document)
    IMAGE_JSON.write_text(
        json.dumps(image_document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    REGISTRY_JSON.write_text(
        json.dumps(registry_document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    image = load_model_weight_image(IMAGE_JSON)
    registry = load_program_weight_registry(REGISTRY_JSON, image, program)
    manifest = weight_tag_manifest(program, registry)
    GOLDEN.mkdir(parents=True, exist_ok=True)
    CYCLE_GOLDEN.write_text(
        json.dumps(build_cycle_golden(program, image, registry, manifest),
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    RNG_GOLDEN.write_text(
        json.dumps(build_rng_golden(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"wrote {IMAGE_JSON.name}, {REGISTRY_JSON.name}, "
          f"{CYCLE_GOLDEN.name} ({len(manifest)} weight tags), "
          f"{RNG_GOLDEN.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
