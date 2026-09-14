"""Command line entry for building and verifying golden .mshb programs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.abi.verifier import verify_program
from mesh_ir.builder import load_arch
from mesh_ir.golden_programs import (
    build_dma_edge_program,
    build_dma_shapes_program,
    build_dma_error_program,
    build_dma_fence_program,
    build_dma_pin_program,
    build_dma_write_error_program,
    build_dual_core_program,
    build_fill_program,
    build_repeat_program,
    build_single_core_program,
    build_poison_program,
    build_sram_parallel_program,
    build_sram_conflict_program,
    build_poison_elementwise_inplace_program,
    build_poison_store_program,
    build_poison_reduce_dst_old_program,
    build_zero_dma_program,
    build_fence_scopes_program,
    build_cross_error_program,
    build_repeat_error_program,
    build_region_edge_program,
    build_compute_timing_program,
    build_p2p_reuse_program,
    build_barrier_e2e_program,
    build_cross_fault_program,
    build_read_window_program,
    build_load_saturation_contiguous_program,
    build_load_saturation_multi_tensor_program,
    build_load_saturation_strided_program,
)

from mesh_ir.moe_programs import (
    moe_dual_copy_program,
    moe_dual_drop_program,
    moe_dual_program,
    moe_min_program,
    moe_multi_program,
    moe_quad_program,
)

BUILDERS = {
    "single": build_single_core_program,
    "moe_min": moe_min_program,
    "moe_multi": moe_multi_program,
    "moe_dual": moe_dual_program,
    "moe_dual_drop": moe_dual_drop_program,
    "moe_dual_copy": moe_dual_copy_program,
    "moe_quad": moe_quad_program,
    "dual": build_dual_core_program,
    "fill": build_fill_program,
    "repeat": build_repeat_program,
    "poison": build_poison_program,
    "dma_edge": build_dma_edge_program,
    "dma_shapes": build_dma_shapes_program,
    "dma_error": build_dma_error_program,
    "dma_write_error": build_dma_write_error_program,
    "dma_fence": build_dma_fence_program,
    "dma_pin": build_dma_pin_program,
    "sram_parallel": build_sram_parallel_program,
    "sram_conflict": build_sram_conflict_program,
    "zero_dma": build_zero_dma_program,
    "fence_scopes": build_fence_scopes_program,
    "cross_error": build_cross_error_program,
    "repeat_error": build_repeat_error_program,
    "p2p_reuse": build_p2p_reuse_program,
    "barrier_e2e": build_barrier_e2e_program,
    "read_window": build_read_window_program,
    "load_saturation_contiguous": build_load_saturation_contiguous_program,
    "load_saturation_multi_tensor": build_load_saturation_multi_tensor_program,
    "load_saturation_strided": build_load_saturation_strided_program,
    "cross_fault": build_cross_fault_program,
    "region_edge": build_region_edge_program,
    "compute_timing": build_compute_timing_program,
    "poison_ew": build_poison_elementwise_inplace_program,
    "poison_store": build_poison_store_program,
    "poison_reduce": build_poison_reduce_dst_old_program,
}


def cmd_build(args) -> int:
    arch = load_arch(args.arch)
    program = BUILDERS[args.program](arch)
    verify_program(program, arch)
    blob = encode_program(program)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "program.mshb").write_bytes(blob)
    (out / "schedule.mesh.json").write_bytes(
        json.dumps(program.canonical_dict(), sort_keys=True, indent=2).encode("utf-8")
    )
    traffic = [
        {
            "entrypoint_id": row.entrypoint_id,
            "profile_id": row.profile_id,
            "command_id": row.command_id,
            "descriptor_id": row.descriptor_id,
            "kind": row.kind,
            "useful_bytes": row.useful_bytes,
            "physical_beat_bytes": row.physical_beat_bytes,
            "segments": row.segments,
            "bursts": row.bursts,
            "ar_count": row.ar_count,
            "r_beats": row.r_beats,
            "aw_count": row.aw_count,
            "w_beats": row.w_beats,
            "b_count": row.b_count,
        }
        for row in program.expected_traffic
    ]
    (out / "expected_traffic.json").write_bytes(
        json.dumps(traffic, sort_keys=True, indent=2).encode("utf-8")
    )
    manifest = {
        "program": args.program,
        "abi": {"major": program.abi_major, "minor": program.abi_minor},
        "arch_name": arch.arch_name,
        "arch_digest": arch.digest().hex(),
        "semantic_sha256": program.semantic_sha256(),
        "mshb_sha256": hashlib.sha256(blob).hexdigest(),
        "mshb_bytes": len(blob),
        "status": "ok",
    }
    (out / "manifest.json").write_bytes(
        json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
    )
    print(json.dumps(manifest, sort_keys=True))
    return 0


def cmd_verify(args) -> int:
    arch = load_arch(args.arch)
    data = Path(args.program).read_bytes()
    program = decode_program(data)
    verify_program(program, arch)
    print(
        json.dumps(
            {
                "status": "ok",
                "semantic_sha256": program.semantic_sha256(),
                "mshb_sha256": hashlib.sha256(data).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return 0


def cmd_emit_agent_golden(args) -> int:
    from mesh_ir import agent_protocol as P
    from mesh_ir.generated import agent_abi as A

    sq = P.encode_sq({
        "abi_major": 1, "abi_minor": 0, "opcode": A.SQ_OPCODE.GENERATE,
        "flags": 0, "sq_seq": 7, "request_id": 0x1234, "session_id": 5,
        "parameter_block_addr": 0x800000000, "parameter_block_bytes": 160,
        "program_id": 1, "profile_id": 1, "completion_cookie": 0x99,
        "qos": 4, "flags2": 0, "reserved": 0,
    })
    cq = P.encode_cq({
        "cq_seq": 3, "request_id": 0x42, "completion_cookie": 0x77,
        "status": A.CQ_STATUS.SUCCESS,
        "flags": A.CQ_FLAGS.METADATA_VALID,
        "output_bytes_or_detail_code": 8192,
    })
    timing = P.encode_tlv(A.OUTPUT_TLV_TYPE.TIMING_BREAKDOWN, bytes(64),
                          A.TLV_FLAGS.REQUIRED)
    meta = P.encode_metadata({
        "magic": 0x4f4e4741, "abi_major": 1, "abi_minor": 0,
        "header_bytes": 128, "flags": 0,
        "terminal_status": A.CQ_STATUS.SUCCESS,
        "request_id": 9, "session_id": 2, "user_id": 1, "task_seq": 1,
        "repair_round": 0, "reserved": 0, "output_tokens": 4,
        "output_bytes": 64, "semantic_content_digest": bytes(32),
        "completed_instance_count": 1, "moe_invocation_count": 0,
        "request_start_tick": 100, "terminal_ready_tick": 200,
        "reserved2": 0,
    }, tail=timing)

    def emit(name, blob):
        lines = [f"static const unsigned char {name}[] = {{"]
        for i in range(0, len(blob), 16):
            lines.append("    " + ", ".join(f"0x{b:02x}" for b in blob[i:i+16]) + ",")
        lines.append("};")
        return "\n".join(lines)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join([
        "// Generated by util/mesh_ir emit-agent-golden; do not edit.",
        "// Cross-language agent protocol pins from agent_protocol.py.",
        "#ifndef DEV_AI_MESH_GENERATED_AGENT_GOLDEN_INC",
        "#define DEV_AI_MESH_GENERATED_AGENT_GOLDEN_INC",
        "",
        emit("kAgentGoldenSq", sq),
        "",
        emit("kAgentGoldenCq", cq),
        "",
        emit("kAgentGoldenMetadata", meta),
        "",
        "#endif",
        "",
    ]) + "\n")
    print(f"wrote {out}")
    return 0


def cmd_emit_cpp_golden(args) -> int:
    arch = load_arch(args.arch)
    out = Path(args.out)
    lines = [
        "// Generated by util/mesh_ir emit-cpp-golden; do not edit.",
        "// Embeds the golden .mshb images as byte arrays (cross-language ABI pins).",
        "#ifndef DEV_AI_MESH_GENERATED_GOLDEN_MSHB_INC",
        "#define DEV_AI_MESH_GENERATED_GOLDEN_MSHB_INC",
        "",
        f"static const char kGoldenArchDigest[] = \"{arch.digest().hex()}\";",
        "",
    ]
    for key in ("single", "dual", "repeat"):
        program = BUILDERS[key](arch)
        verify_program(program, arch)
        blob = encode_program(program)
        lines.append(f"static const unsigned char kGoldenMshb{key.capitalize()}[] = {{")
        for i in range(0, len(blob), 16):
            chunk = ", ".join(f"0x{b:02x}" for b in blob[i : i + 16])
            lines.append(f"    {chunk},")
        lines.append("};")
        lines.append("")
    lines.extend(["#endif", ""])
    out.write_text("\n".join(lines), encoding="utf-8")

    splitter = out.with_name("golden_splitter.inc")
    vectors = json.loads(
        (Path(__file__).resolve().parents[1] / "tests/golden/burst_splitter_golden.json").read_text()
    )
    rows = [
        "// Generated by util/mesh_ir emit-cpp-golden; do not edit.",
        "// Cross-language burst splitter golden vectors (see",
        "// util/mesh_ir/tests/golden/burst_splitter_golden.json).",
        "#ifndef DEV_AI_MESH_GENERATED_GOLDEN_SPLITTER_INC",
        "#define DEV_AI_MESH_GENERATED_GOLDEN_SPLITTER_INC",
        "",
        "#include <cstdint>",
        "#include <tuple>",
        "#include <vector>",
        "",
        "namespace gem5 {",
        "namespace ai_mesh {",
        "namespace golden {",
        "",
        "struct SplitterCase",
        "{",
        "    const char *name;",
        "    uint64_t row_bytes;",
        "    uint32_t rows;",
        "    uint64_t base;",
        "    uint64_t stride;",
        "    uint32_t width;",
        "    uint32_t max_beats;",
        "    uint64_t beat_bytes;",
        "    uint64_t useful_bytes;",
        "    std::vector<std::tuple<uint64_t, uint64_t, uint64_t, uint32_t>> bursts;",
        "};",
        "",
        "inline std::vector<SplitterCase> splitterCases()",
        "{",
        "    return {",
    ]
    for case in vectors:
        burst_list = ", ".join(
            f"{{{b['beat_base']}, {b['logical_start']}, {b['useful_bytes']}, {b['beats']}}}"
            for b in case["bursts"]
        )
        rows.append(
            f"        {{{json.dumps(case['name'])}, {case['row_bytes']}, {case['rows']}, {case['base']}, "
            f"{case['stride']}, {case['width']}, {case['max_beats']}, {case['beat_bytes']}, "
            f"{case['useful_bytes']}, {{{burst_list}}}}},"
        )
    rows.extend(
        [
            "    };",
            "}",
            "",
            "} // namespace golden",
            "} // namespace ai_mesh",
            "} // namespace gem5",
            "",
            "#endif",
            "",
        ]
    )
    splitter.write_text("\n".join(rows), encoding="utf-8")
    print(f"wrote {out} and {splitter}")
    return 0


def cmd_emit_gate5_weight_golden(args) -> int:
    from mesh_ir.weight_registry import (
        load_model_weight_image,
        load_program_weight_registry,
        tag_manifest_bytes,
        weight_tag_manifest,
    )

    out = Path(args.out)
    arch = load_arch(args.arch)
    program = decode_program(args.program and Path(args.program).read_bytes())
    image = load_model_weight_image(args.image)
    registry = load_program_weight_registry(args.registry, image, program)
    manifest = weight_tag_manifest(program, registry)
    semantic = bytes.fromhex(program.semantic_sha256())
    resolved = []
    for expert in program.moe_expert_specs:
        binding = registry.symbol(semantic, expert.weight_symbol_id)
        for region in binding.regions:
            if region.region_offset == expert.weight_region_offset and \
                    region.bytes == expert.weight_bytes:
                resolved.append(region.resolved_content_digest)
    if len(resolved) != len(program.moe_expert_specs):
        raise MeshIrError("E_RELOCATION",
                          "every expert weight region must be bound")
    body = tag_manifest_bytes(manifest)
    lines = [
        "// Generated by util/mesh_ir emit-gate5-weight-golden; do not edit.",
        "#ifndef DEV_AI_MESH_GENERATED_GATE5_WEIGHT_GOLDEN_INC",
        "#define DEV_AI_MESH_GENERATED_GATE5_WEIGHT_GOLDEN_INC",
        "",
        "#include <cstdint>",
        "",
        "namespace gem5",
        "{",
        "namespace ai_mesh",
        "{",
        "namespace golden",
        "{",
        "",
        f'static const char kGate5ArchDigest[] = "{arch.digest().hex()}";',
        f'static const char kGate5ProgramSemanticDigest[] = '
        f'"{program.semantic_sha256()}";',
        f'static const char kGate5WeightImageDigest[] = "{image.digest}";',
        f'static const char kGate5WeightRegistryDigest[] = '
        f'"{registry.digest}";',
        f'static const char kGate5WeightTagManifestDigest[] = '
        f'"{hashlib.sha256(tag_manifest_bytes(manifest)).hexdigest()}";',
        f"static constexpr uint32_t kGate5WeightTagCount = {len(manifest)};",
        f"static constexpr uint32_t kGate5WeightTagTupleBytes = "
        f"{len(manifest[0][1]) if manifest else 0};",
        "",
        f"static const unsigned char kGate5ExpertResolvedDigests"
        f"[{len(resolved)}][32] = {{",
    ]
    for digest in resolved:
        chunk = ", ".join(f"0x{byte:02x}" for byte in digest)
        lines.append("    {" + chunk + "},")
    lines.extend([
        "};",
        "",
        "static const unsigned char kGate5WeightTagManifest[] = {",
    ])
    for offset in range(0, len(body), 12):
        chunk = ", ".join(f"0x{byte:02x}" for byte in body[offset:offset + 12])
        lines.append("    " + chunk + ",")
    lines.extend([
        "};",
        "",
        "} // namespace golden",
        "} // namespace ai_mesh",
        "} // namespace gem5",
        "",
        "#endif",
        "",
    ])
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out} ({len(manifest)} weight tags)")
    return 0


def cmd_emit_gate5_rng_golden(args) -> int:
    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    out = Path(args.out)
    lines = [
        "// Generated by util/mesh_ir emit-gate5-rng-golden; do not edit.",
        "#ifndef DEV_AI_MESH_GENERATED_GATE5_RNG_GOLDEN_INC",
        "#define DEV_AI_MESH_GENERATED_GATE5_RNG_GOLDEN_INC",
        "",
        "#include <cstdint>",
        "",
        "namespace gem5",
        "{",
        "namespace ai_mesh",
        "{",
        "namespace golden",
        "{",
        "",
        "struct RngKeyFields",
        "{",
        "    uint64_t master_seed;",
        "    const char *workload_plan_digest_hex;",
        "    uint32_t user_id;",
        "    uint32_t task_seq;",
        "    uint16_t repair_round;",
        "    uint8_t phase;",
        "    uint32_t sequence_ordinal;",
        "    uint32_t token_ordinal;",
        "    uint32_t layer_id;",
        "    uint32_t logical_source_rank;",
        "    uint16_t topk_slot;",
        "    uint16_t draw_id;",
        "};",
        "",
        "struct RngVector",
        "{",
        "    const char *name;",
        "    RngKeyFields key;",
        "    const char *key_hex;",
        "    uint64_t seed64;",
        "    uint64_t splitmix64;",
        "    uint64_t total;",
        "    uint64_t threshold;",
        "    uint64_t weights[8];",
        "    uint32_t weight_count;",
        "    uint32_t selected_index;",
        "};",
        "",
        "struct UniformVector",
        "{",
        "    const char *name;",
        "    uint32_t expert_count;",
        "    uint32_t top_k;",
        "    RngKeyFields key;",
        "    const char *key_hex;",
        "    uint32_t selected[16];",
        "};",
        "",
        f"static constexpr uint32_t kRngKeyBytes = {golden['key_bytes']};",
        "",
        "static const RngVector kRngVectors[] = {",
    ]

    def emit_fields(fields, indent):
        return [
            f"{indent}{{",
            f"{indent}    {fields['master_seed']}ull,",
            f"{indent}    {json.dumps(fields['workload_plan_digest_hex'])},",
            f"{indent}    {fields['user_id']}u, {fields['task_seq']}u,",
            f"{indent}    {fields['repair_round']}, {fields['phase']},",
            f"{indent}    {fields['sequence_ordinal']}u,",
            f"{indent}    {fields['token_ordinal']}u,",
            f"{indent}    {fields['layer_id']}u,",
            f"{indent}    {fields['logical_source_rank']}u,",
            f"{indent}    {fields['topk_slot']}, {fields['draw_id']},",
            f"{indent}}},",
        ]

    for vector in golden["vectors"]:
        weights = list(vector["weights"]) + [0] * (8 - len(vector["weights"]))
        lines.append("    {")
        lines.append(f"        {json.dumps(vector['name'])},")
        lines.extend(emit_fields(vector["key_fields"], "        "))
        lines.append(f"        {json.dumps(vector['key_hex'])},")
        lines.append(f"        {vector['seed64']}ull, "
                     f"{vector['splitmix64']}ull,")
        lines.append(f"        {vector['total']}ull, "
                     f"{vector['threshold']}ull,")
        lines.append("        {" +
                     ", ".join(f"{w}ull" for w in weights) + "},")
        lines.append(f"        {len(vector['weights'])}u,")
        lines.append(f"        {vector['selected_index']}u,")
        lines.append("    },")
    lines.extend([
        "};",
        "",
        f"static constexpr uint32_t kRngVectorCount = "
        f"{len(golden['vectors'])};",
        "",
        "static const UniformVector kUniformVectors[] = {",
    ])
    for vector in golden["uniform_vectors"]:
        selected = list(vector["selected"])
        selected += [0] * (16 - len(selected))
        lines.append("    {")
        lines.append(f"        {json.dumps(vector['name'])},")
        lines.append(f"        {vector['expert_count']}u, {vector['top_k']}u,")
        lines.extend(emit_fields(vector["key_fields"], "        "))
        lines.append(f"        {json.dumps(vector['key_hex'])},")
        lines.append("        {" + ", ".join(f"{v}u" for v in selected) + "},")
        lines.append("    },")
    lines.extend([
        "};",
        "",
        f"static constexpr uint32_t kUniformVectorCount = "
        f"{len(golden['uniform_vectors'])};",
        "",
        "} // namespace golden",
        "} // namespace ai_mesh",
        "} // namespace gem5",
        "",
        "#endif",
        "",
    ])
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def cmd_emit_gate5_overlay_golden(args) -> int:
    import sys as _sys

    from mesh_ir.moe_overlay_runtime import OVERLAY_REFS_PER_OBJECT

    refs = OVERLAY_REFS_PER_OBJECT
    fixtures = Path(args.fixtures)
    _sys.path.insert(0, str(fixtures))
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gate5_overlay_fixture", fixtures / "build_gate5_overlay.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    overlay, digest, counts, intervals = module.build_overlay()
    document = module.overlay_document(overlay, digest, counts, intervals)
    json_path = Path(args.json_out)
    json_path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    lines = [
        "// Generated by util/mesh_ir emit-gate5-overlay-golden; do not edit.",
        "#ifndef DEV_AI_MESH_GENERATED_GATE5_OVERLAY_GOLDEN_INC",
        "#define DEV_AI_MESH_GENERATED_GATE5_OVERLAY_GOLDEN_INC",
        "",
        "#include <cstdint>",
        "",
        "namespace gem5",
        "{",
        "namespace ai_mesh",
        "{",
        "namespace golden",
        "{",
        "",
        f"static constexpr uint32_t kOverlayRefsPerObject = {refs};",
        "",
        "struct OverlayObjectEntry",
        "{",
        "    uint16_t kind;",
        "    uint32_t region_id;",
        "    uint32_t ordinal;",
        "    uint32_t owner_core;",
        "    uint32_t secondary_kind;",
        "    uint32_t expert_id;",
        "    uint32_t src_core;",
        "    uint32_t dst_core;",
        "    uint32_t chunk_ordinal;",
        "    uint32_t phase;",
        "    uint32_t role;",
        "    uint32_t access;",
        "    uint32_t bytes;",
        "    uint32_t alignment;",
        "    uint32_t offset;",
        "    uint32_t ref_region;",
        "    uint32_t ref_ordinal;",
        "    uint32_t src_view_region;",
        "    uint32_t src_view_ordinal;",
        "    uint32_t dst_view_region;",
        "    uint32_t dst_view_ordinal;",
        "    uint32_t backing_kind;",
        "    uint32_t semantic_owner_kind;",
        "    uint32_t semantic_owner_ref0;",
        "    uint32_t validity_extent;",
        "    const char *token_hex;",
        "    uint32_t wait_count;",
        "    uint32_t signal_count;",
        "    uint32_t view_count;",
        f"    uint32_t wait_refs[{refs}][3];",
        f"    uint32_t signal_refs[{refs}][3];",
        f"    uint32_t view_refs[{refs}][3];",
        "};",
        "",
        f'static const char kGate5OverlayDigest[] = "{digest}";',
        f"static constexpr uint32_t kGate5OverlayLayerId = {overlay.layer_id};",
        "struct OverlayScratchEntry",
        "{",
        "    uint32_t region_id;",
        "    uint32_t ordinal;",
        "    uint64_t offset;",
        "    uint64_t bytes;",
        "};",
        "",
        f"static constexpr uint32_t kGate5OverlayFillMode = "
        f"{int(document.get('fill_mode', 0))};",
        "static const uint8_t kGate5OverlayProgramDigest[32] = {",
        "    " + ", ".join(
            f"0x{byte:02x}"
            for byte in bytes.fromhex(document["program_digest_hex"])),
        "};",
        "struct OverlayPayloadEntry",
        "{",
        "    uint32_t region_id;",
        "    uint32_t ordinal;",
        "    uint64_t offset;",
        "    uint64_t bytes;",
        "};",
        "",
        f"static constexpr uint32_t kGate5OverlayScratchCount = "
        f"{len(document['scratch'])};",
        "static const OverlayScratchEntry kGate5OverlayScratch[] = {",
        *["    {" + ", ".join([
            str(row["region_id"]), str(row["ordinal"]), str(row["offset"]),
            str(row["bytes"])]) + "}," for row in document["scratch"]],
        "};",
        "static const OverlayObjectEntry kGate5OverlayObjects[] = {",
    ]
    for entry in document["objects"]:
        lines.append("    {" + ", ".join([
            str(entry["kind"]), str(entry["region_id"]), str(entry["ordinal"]),
            str(entry["owner_core"]), str(entry["secondary_kind"]),
            str(entry["expert_id"]), str(entry["src_core"]),
            str(entry["dst_core"]), str(entry["chunk_ordinal"]),
            str(entry["phase"]), str(entry["role"]), str(entry["access"]),
            str(entry["bytes"]), str(entry["alignment"]), str(entry["offset"]),
            str(entry["ref_region"]), str(entry["ref_ordinal"]),
            str(entry.get("src_view_region", 0)),
            str(entry.get("src_view_ordinal", 0)),
            str(entry.get("dst_view_region", 0)),
            str(entry.get("dst_view_ordinal", 0)),
            str(entry["backing_kind"]), str(entry["semantic_owner_kind"]),
            str(entry["semantic_owner_ref0"]), str(entry["validity_extent"]),
            json.dumps(entry["token_hex"]), str(entry["wait_count"]),
            str(entry["signal_count"]),
            str(len(entry.get("view_refs", ()))),
            "{" + _ref_rows(entry["wait_refs"], refs) + "}",
            "{" + _ref_rows(entry["signal_refs"], refs) + "}",
            "{" + _ref_rows(entry.get("view_refs", ()), refs) + "}",
        ]) + "},")
    contents = [entry for entry in document["objects"]
                if entry.get("fill_content_hex")]
    blob = bytearray()
    payload_rows = []
    for entry in contents:
        data = bytes.fromhex(entry["fill_content_hex"])
        payload_rows.append((entry["region_id"], entry["ordinal"], len(blob),
                             len(data)))
        blob += data
    lines.extend([
        "};",
        "",
        f"static constexpr uint32_t kGate5OverlayPayloadCount = "
        f"{len(payload_rows)};",
        "static const OverlayPayloadEntry kGate5OverlayPayload[] = {",
        *["    {" + ", ".join([str(row[0]), str(row[1]), str(row[2]),
                              str(row[3])]) + "u}," for row in payload_rows],
        "};",
        "",
        f"static constexpr uint32_t kGate5OverlayPayloadBytes = "
        f"{len(blob)};",
        "static const uint8_t kGate5OverlayPayloadBlob[] = {",
        "    " + ", ".join(str(byte) for byte in blob),
        "};",
        "",
    ])
    counts = document["counts"]
    lines.extend([
        f"static constexpr uint32_t kGate5OverlayObjectCount = "
        f"{len(document['objects'])};",
        f"static constexpr uint32_t kGate5OverlayAllocations = "
        f"{counts['allocations']};",
        f"static constexpr uint32_t kGate5OverlayViews = {counts['views']};",
        f"static constexpr uint32_t kGate5OverlayCommands = "
        f"{counts['commands']};",
        f"static constexpr uint32_t kGate5OverlayEvents = {counts['events']};",
        f"static constexpr uint32_t kGate5OverlayDescriptors = "
        f"{counts['descriptors']};",
        f"static constexpr uint32_t kGate5OverlayTransfers = "
        f"{counts['transfers']};",
        "",
        "} // namespace golden",
        "} // namespace ai_mesh",
        "} // namespace gem5",
        "",
        "#endif",
        "",
    ])
    out = Path(args.out)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {json_path} and {out}")
    return 0


def _ref_rows(entries, limit) -> str:
    rows = [f"{{{region}u, {kind}u, {ordinal}u}}"
            for region, kind, ordinal in entries]
    if len(rows) > limit:
        raise ValueError("overlay object references exceed the golden bound")
    while len(rows) < limit:
        rows.append("{0xFFFFFFFFu, 0xFFFFFFFFu, 0xFFFFFFFFu}")
    return ", ".join(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="mesh_ir")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="build a golden program artifact set")
    build.add_argument("--program", choices=sorted(BUILDERS), required=True)
    build.add_argument("--arch", required=True)
    build.add_argument("--out", required=True)
    build.set_defaults(func=cmd_build)

    verify = sub.add_parser("verify", help="decode and verify a .mshb against an arch manifest")
    verify.add_argument("--program", required=True)
    verify.add_argument("--arch", required=True)
    verify.set_defaults(func=cmd_verify)

    emit_agent = sub.add_parser(
        "emit-agent-golden",
        help="embed agent protocol golden images as a C++ header")
    emit_agent.add_argument("--out", required=True)
    emit = sub.add_parser("emit-cpp-golden", help="embed golden .mshb images as a C++ header")
    emit.add_argument("--arch", required=True)
    emit.add_argument("--out", required=True)
    emit_weight = sub.add_parser(
        "emit-gate5-weight-golden",
        help="embed the Gate 5 weight tag manifest as a C++ header")
    emit_weight.add_argument("--arch", required=True)
    emit_weight.add_argument("--program", required=True)
    emit_weight.add_argument("--image", required=True)
    emit_weight.add_argument("--registry", required=True)
    emit_weight.add_argument("--out", required=True)
    emit_rng = sub.add_parser(
        "emit-gate5-rng-golden",
        help="embed the Gate 5 keyed RNG golden vectors as a C++ header")
    emit_rng.add_argument("--golden", required=True)
    emit_rng.add_argument("--out", required=True)
    emit_rng.set_defaults(func=cmd_emit_gate5_rng_golden)
    emit_overlay = sub.add_parser(
        "emit-gate5-overlay-golden",
        help="embed the Gate 5 canonical overlay objects as a C++ header")
    emit_overlay.add_argument("--fixtures", required=True)
    emit_overlay.add_argument("--json-out", required=True)
    emit_overlay.add_argument("--out", required=True)
    emit_overlay.set_defaults(func=cmd_emit_gate5_overlay_golden)
    emit_weight.set_defaults(func=cmd_emit_gate5_weight_golden)
    emit_agent.set_defaults(func=cmd_emit_agent_golden)
    emit.set_defaults(func=cmd_emit_cpp_golden)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
