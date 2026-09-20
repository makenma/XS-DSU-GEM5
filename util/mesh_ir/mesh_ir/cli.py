"""Command line entry for building and verifying golden .mshb programs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.model import MeshIrError
from mesh_ir.golden_programs import (
    build_dma_edge_program,
    build_dma_shapes_program,
    build_dma_error_program,
    build_dma_fence_program,
    build_dma_pin_program,
    build_dma_write_error_program,
    build_dual_core_program,
    build_fill_program,
    build_fill_offset_program,
    build_fill_view_offset_in0_out_program,
    build_fill_view_offset_in0_program,
    build_fill_view_offset_padding_pattern_program,
    build_fill_view_offset_padding_zero_program,
    build_fill_view_offset_row_gap_pattern_program,
    build_fill_view_offset_row_gap_zero_program,
    build_fill_view_offset_split_pattern_program,
    build_fill_view_offset_split_program,
    build_fill_view_offset_split_zero_program,
    build_fill_view_offset_out_pad_program,
    build_fill_view_offset_out_program,
    build_fill_view_offset_program,
    build_repeat_program,
    build_single_core_program,
    build_poison_program,
    build_sram_parallel_program,
    build_sram_conflict_program,
    build_poison_elementwise_inplace_program,
    build_poison_store_program,
    build_poison_reduce_dst_old_program,
    build_multi_descriptor_program,
    build_pre_resident_weight_program,
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
from mesh_ir.golden_runtime_programs import (
    build_barrier_asymmetric_program,
    build_p2p_cancel_program,
    build_p2p_multi_descriptor_program,
    build_p2p_prefilled_destination_program,
    build_double_buffer_overlap_program,
    build_double_buffer_serialized_program,
    build_engine_reduce_pressure_program,
    build_engine_tensor_pressure_program,
    build_engine_vector_pressure_program,
)
from mesh_ir.publication import publish_authored_program
from mesh_ir.scheduled.verify import verify_program as verify_complete_program

BUILDERS = {
    "single": build_single_core_program,
    "dual": build_dual_core_program,
    "fill": build_fill_program,
    "fill_offset": build_fill_offset_program,
    "fill_view_offset": build_fill_view_offset_program,
    "fill_view_offset_out": build_fill_view_offset_out_program,
    "fill_view_offset_out_pad": build_fill_view_offset_out_pad_program,
    "fill_view_offset_padding_zero": build_fill_view_offset_padding_zero_program,
    "fill_view_offset_padding_pattern": build_fill_view_offset_padding_pattern_program,
    "fill_view_offset_row_gap_zero": build_fill_view_offset_row_gap_zero_program,
    "fill_view_offset_row_gap_pattern": build_fill_view_offset_row_gap_pattern_program,
    "fill_view_offset_split": build_fill_view_offset_split_program,
    "fill_view_offset_split_zero": build_fill_view_offset_split_zero_program,
    "fill_view_offset_split_pattern": build_fill_view_offset_split_pattern_program,
    "fill_view_offset_in0": build_fill_view_offset_in0_program,
    "fill_view_offset_in0_out": build_fill_view_offset_in0_out_program,
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
    "multi_descriptor": build_multi_descriptor_program,
    "pre_resident_weight": build_pre_resident_weight_program,
    "engine_tensor_pressure": build_engine_tensor_pressure_program,
    "engine_vector_pressure": build_engine_vector_pressure_program,
    "engine_reduce_pressure": build_engine_reduce_pressure_program,
    "double_buffer_overlap": build_double_buffer_overlap_program,
    "double_buffer_serialized": build_double_buffer_serialized_program,
    "barrier_asymmetric": build_barrier_asymmetric_program,
    "p2p_multi_descriptor": build_p2p_multi_descriptor_program,
    "p2p_prefilled_destination": build_p2p_prefilled_destination_program,
    "p2p_cancel": build_p2p_cancel_program,
}


def cmd_build(args) -> int:
    arch = load_arch(args.arch)
    program = BUILDERS[args.program](arch)
    result = publish_authored_program(
        args.out,
        program,
        arch,
        kind="golden",
        identity=(("program", args.program), ("arch_name", arch.arch_name)),
        protected_inputs=(args.arch,),
    )
    print(json.dumps(result.canonical_dict(), sort_keys=True))
    return 0


def cmd_bindings(args) -> int:
    arch = load_arch(args.arch)
    program = BUILDERS[args.program](arch)
    selected = tuple(
        variant
        for variant in program.semantics.variants
        if (args.entrypoint_id == 0 or variant.entrypoint_id == args.entrypoint_id)
        and (args.profile_id == 0 or variant.profile_id == args.profile_id)
    )
    if len(selected) != 1:
        raise MeshIrError(
            "E_RELOCATION",
            "invocation selects no unique Program variant",
        )
    variant = selected[0]
    span = variant.membership.binding_slots
    slots = {slot.slot_id: slot for slot in program.semantics.binding_slots}
    if args.bindings_in:
        payload = json.loads(Path(args.bindings_in).read_text())
    else:
        payload = []
        for slot_id in range(span.first_id, span.first_id + span.count):
            slot = slots[slot_id]
            binding = slot.reference_binding
            payload.append(
                {
                    "slot_id": binding.slot_id,
                    "symbol": slot.symbol,
                    "region_id": binding.region_id,
                    "owner_core": binding.owner_core,
                    "allocation_offset_bytes": binding.allocation_offset_bytes,
                    "allocation_size_bytes": binding.allocation_size_bytes,
                    "allocation_alignment_bytes": binding.allocation_alignment_bytes,
                    "access": int(getattr(binding.access, "value", binding.access)),
                }
            )
    Path(args.out).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if args.traffic_out:
        from mesh_ir.ir.common import Access
        from mesh_ir.scheduled.addressing import bind_invocation
        from mesh_ir.traffic import Binding

        bindings = tuple(
            Binding(
                item["slot_id"],
                item["region_id"],
                item["owner_core"],
                item["allocation_offset_bytes"],
                item["allocation_size_bytes"],
                item["allocation_alignment_bytes"],
                Access(item["access"]),
            )
            for item in payload
        )
        invocation = bind_invocation(
            program, arch, variant.entrypoint_id, variant.profile_id, bindings
        )
        Path(args.traffic_out).write_text(
            json.dumps(
                invocation.traffic.canonical_dict(), indent=2, sort_keys=True
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


def cmd_verify(args) -> int:
    arch = load_arch(args.arch)
    data = Path(args.program).read_bytes()
    program = decode_program(data)
    verify_complete_program(program, arch)
    print(
        json.dumps(
            {
                "status": "ok",
                "semantic_sha256": program.semantic_sha256,
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
        verify_complete_program(program, arch)
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

    bindings = sub.add_parser(
        "bindings", help="emit the compiler reference dispatch bindings as JSON")
    bindings.add_argument("--program", choices=sorted(BUILDERS), required=True)
    bindings.add_argument("--arch", required=True)
    bindings.add_argument("--out", required=True)
    bindings.add_argument("--entrypoint-id", type=int, default=0)
    bindings.add_argument("--profile-id", type=int, default=0)
    bindings.add_argument(
        "--bindings-in", default="", help="Dispatch bindings to echo instead of the reference set")
    bindings.add_argument(
        "--traffic-out", default="", help="Also derive the invocation expected traffic JSON")
    bindings.set_defaults(func=cmd_bindings)

    emit_agent = sub.add_parser(
        "emit-agent-golden",
        help="embed agent protocol golden images as a C++ header")
    emit_agent.add_argument("--out", required=True)
    emit = sub.add_parser("emit-cpp-golden", help="embed golden .mshb images as a C++ header")
    emit.add_argument("--arch", required=True)
    emit.add_argument("--out", required=True)
    emit_agent.set_defaults(func=cmd_emit_agent_golden)
    emit.set_defaults(func=cmd_emit_cpp_golden)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
