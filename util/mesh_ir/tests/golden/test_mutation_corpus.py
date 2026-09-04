"""Cross-language mutation corpus: every mutation must produce IDENTICAL
{accepted, error_code} on the Python and the C++ readers.

The C++ side runs through a standalone ASan/UBSan build of the real
decoder + verifier (tests/golden/support/mutation_driver.cc), so parser
overflow inputs additionally prove "deterministic error, never a crash".
"""

import copy
import dataclasses
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.abi.verifier import verify_program
from mesh_ir.builder import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_single_core_program, build_zero_dma_program
from mesh_ir.model import MeshIrError

ARCH_PATH = Path(__file__).resolve().parents[4] / "configs/example/ai_mesh/arch/mesh_1x2.yaml"
DRIVER_SRC = Path(__file__).resolve().parent / "support/mutation_driver.cc"
REPO = Path(__file__).resolve().parents[4]
INCLUDES = [REPO / "src", REPO / "src/dev/ai_mesh"]


def _recompute(blob: bytearray) -> bytearray:
    dir_off = struct.unpack_from("<Q", blob, 24)[0]
    count = struct.unpack_from("<I", blob, 32)[0]
    import hashlib

    for index in range(count):
        entry = dir_off + index * 40
        sec_off, size = struct.unpack_from("<QQ", blob, entry + 8)
        crc = zlib.crc32(bytes(blob[sec_off : sec_off + size])) & 0xFFFFFFFF
        struct.pack_into("<I", blob, entry + 32, crc)
    blob[72:104] = hashlib.sha256(bytes(blob[128:])).digest()
    return blob


def _sections(blob):
    dir_off = struct.unpack_from("<Q", blob, 24)[0]
    count = struct.unpack_from("<I", blob, 32)[0]
    out = []
    for index in range(count):
        entry = dir_off + index * 40
        stype = struct.unpack_from("<H", blob, entry)[0]
        record_bytes = struct.unpack_from("<I", blob, entry + 4)[0]
        off, size, cnt = struct.unpack_from("<QQQ", blob, entry + 8)
        out.append({"entry": entry, "type": stype, "off": off, "size": size,
                    "count": cnt, "record_bytes": record_bytes})
    return out


def _section_offsets(blob):
    return {row["type"]: row for row in _sections(blob)}


def _python_verdict(blob: bytes):
    try:
        program = decode_program(blob)
    except MeshIrError as err:
        return f"DECODE:{err.code}"
    try:
        verify_program(program, _python_verdict.arch)
    except MeshIrError as err:
        return f"VERIFY:{err.code}"
    return "ACCEPTED"


def _write_arch_facts(path: Path, arch):
    def _dtype_mask(table):
        mask = 0
        from mesh_ir.generated import abi as _A
        for name in table:
            value = getattr(_A.DTYPE, name.upper(), None)
            if value:
                mask |= 1 << (value - 1)
        return mask

    lines = [
        arch.digest().hex(),
        str(arch.sram_bytes),
        str(arch.sram_banks),
        str(arch.sram_base_alignment_bytes),
        str(arch.axi_data_bytes),
        str(arch.axi_max_burst_beats),
        ",".join(str(c) for c in arch.core_ids),
        f"{_dtype_mask(arch.tensor_macs_per_cycle)} "
        f"{_dtype_mask(arch.vector_elements_per_cycle)} "
        f"{_dtype_mask(arch.reduce_ops_per_cycle)}",
    ]
    for region_id, region in enumerate(arch.regions):
        kind = {"HBM": 0, "HOST_SHARED": 1}.get(region.kind, 2)
        lines.append(
            f"{region_id} {region.base} {region.bytes} "
            f"{region.tile_stride if region.tile_stride else 0} "
            f"{region.tile_bytes if region.tile_bytes else 0} {kind}"
        )
    path.write_text("\n".join(lines) + "\n")


def _cpp_verdict(blob: bytes, driver: str, tmp: Path, arch):
    path = tmp / "case.mshb"
    path.write_bytes(blob)
    facts = tmp / "arch_facts.txt"
    _write_arch_facts(facts, arch)
    result = subprocess.run(
        [driver, str(path), str(facts)], capture_output=True, text=True,
        timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return f"CRASH:rc={result.returncode}:{result.stderr.strip()[:80]}"
    return result.stdout.strip()


def _mutations(blob: bytes):
    """Yield (name, mutated_bytes, expected_code) triples covering the
    corpus classes; expected_code pins the shared rejection reason."""
    base = bytearray(blob)
    secs = _section_offsets(base)
    rb = {
        "entrypoints": A.ENTRYPOINTS_BYTES, "profiles": A.PROFILES_BYTES,
        "tensors": A.TENSORS_BYTES, "shards": A.SHARDS_BYTES,
        "allocations": A.ALLOCATIONS_BYTES, "streams": A.STREAMS_BYTES,
        "commands": A.COMMANDS_BYTES, "waits": A.COMMAND_WAITS_BYTES,
        "operands": A.COMMAND_OPERANDS_BYTES, "events": A.EVENTS_BYTES,
        "descriptors": A.DMA_DESCRIPTORS_BYTES, "attrs": A.OP_ATTRS_BYTES,
        "relocations": A.RELOCATIONS_BYTES, "traffic": A.EXPECTED_TRAFFIC_BYTES,
    }
    st = {
        "entrypoints": A.SECTION_TYPE.ENTRYPOINTS, "profiles": A.SECTION_TYPE.PROFILES,
        "tensors": A.SECTION_TYPE.TENSORS, "shards": A.SECTION_TYPE.SHARDS,
        "allocations": A.SECTION_TYPE.ALLOCATIONS, "streams": A.SECTION_TYPE.STREAMS,
        "commands": A.SECTION_TYPE.COMMANDS, "waits": A.SECTION_TYPE.COMMAND_WAITS,
        "operands": A.SECTION_TYPE.COMMAND_OPERANDS, "events": A.SECTION_TYPE.EVENTS,
        "descriptors": A.SECTION_TYPE.DMA_DESCRIPTORS, "attrs": A.SECTION_TYPE.OP_ATTRS,
        "relocations": A.SECTION_TYPE.RELOCATIONS,
        "traffic": A.SECTION_TYPE.EXPECTED_TRAFFIC,
    }
    reserved_tables = (
        ("ENTRYPOINTS", "entrypoints", ("reserved",)),
        ("PROFILES", "profiles", ("reserved",)),
        ("TENSORS", "tensors", ("reserved",)),
        ("SHARDS", "shards", ("reserved", "reserved2")),
        ("STREAMS", "streams", ("reserved",)),
        ("COMMAND_OPERANDS", "operands", ("reserved",)),
        ("EVENTS", "events", ("reserved", "reserved2")),
        ("DMA_DESCRIPTORS", "descriptors", ("reserved", "reserved2")),
        ("RELOCATIONS", "relocations", ("reserved", "reserved2")),
        ("EXPECTED_TRAFFIC", "traffic", ("reserved", "reserved2")),
    )
    for record, short, fields in reserved_tables:
        sec = secs[st[short]]
        if sec["count"] == 0:
            continue
        offsets = getattr(A, f"{record}_FIELD_OFFSETS")
        for field in fields:
            m = bytearray(base)
            m[sec["off"] + offsets[field]] = 1
            yield f"{short}_reserved_{field}", bytes(_recompute(m)), None

    commands = secs[st["commands"]]
    m = bytearray(base)
    struct.pack_into("<H", m, commands["off"] + A.COMMANDS_FIELD_OFFSETS["opcode"], 99)
    yield "commands_unknown_opcode", bytes(_recompute(m)), None
    m = bytearray(base)
    struct.pack_into("<H", m, commands["off"] + A.COMMANDS_FIELD_OFFSETS["engine"], A.ENGINE.DMA_READ)
    yield "commands_engine_mismatch", bytes(_recompute(m)), None
    m = bytearray(base)
    struct.pack_into("<I", m, commands["off"] + A.COMMANDS_FIELD_OFFSETS["signal_event"], 9999)
    yield "commands_unknown_signal_event", bytes(_recompute(m)), None

    waits = secs[st["waits"]]
    m = bytearray(base); struct.pack_into("<I", m, waits["off"], 9999)
    yield "waits_unknown_event", bytes(_recompute(m)), None

    operands = secs[st["operands"]]
    for field in ("tensor_id", "shard_id", "allocation_id"):
        m = bytearray(base)
        struct.pack_into(
            "<I", m, operands["off"] + A.COMMAND_OPERANDS_FIELD_OFFSETS[field], 9999
        )
        yield f"operands_unknown_{field}", bytes(_recompute(m)), None

    descriptors = secs[st["descriptors"]]
    endpoint_base = A.DMA_DESCRIPTORS_FIELD_OFFSETS["src"]
    m = bytearray(base)
    struct.pack_into(
        "<H", m, descriptors["off"] + endpoint_base + A.DMA_ENDPOINT_FIELD_OFFSETS["memory_space"], 99
    )
    yield "descriptors_unknown_space", bytes(_recompute(m)), None
    m = bytearray(base)
    struct.pack_into(
        "<H", m, descriptors["off"] + endpoint_base + A.DMA_ENDPOINT_FIELD_OFFSETS["region_id"], 99
    )
    yield "descriptors_unknown_region", bytes(_recompute(m)), None
    m = bytearray(base)
    m[descriptors["off"] + endpoint_base + A.DMA_ENDPOINT_FIELD_OFFSETS["offset_bytes"] + 7] = 0xFF
    yield "descriptors_offset_wrap", bytes(_recompute(m)), None
    m = bytearray(base)
    struct.pack_into(
        "<H", m, descriptors["off"] + A.DMA_DESCRIPTORS_FIELD_OFFSETS["kind"], 2
    )
    yield "descriptors_kind_mismatch", bytes(_recompute(m)), None

    attrs = secs[st["attrs"]]
    m = bytearray(base)
    struct.pack_into("<H", m, attrs["off"] + A.OP_ATTRS_FIELD_OFFSETS["kind"], 99)
    yield "attrs_unknown_kind", bytes(_recompute(m)), None
    m = bytearray(base)
    struct.pack_into("<H", m, attrs["off"] + A.OP_ATTRS_FIELD_OFFSETS["reserved"], 1)
    yield "attrs_reserved", bytes(_recompute(m)), None
    m = bytearray(base)
    yield "attrs_payload_padding", bytes(_recompute(bytearray(base))), None
    m = bytearray(base)
    struct.pack_into(
        "<H", m, attrs["off"] + 4 + A.GEMM_V1_FIELD_OFFSETS["dtype"], 99
    )
    yield "attrs_unknown_gemm_dtype", bytes(_recompute(m)), None

    traffic = secs[st["traffic"]]
    m = bytearray(base)
    struct.pack_into(
        "<I", m, traffic["off"] + A.EXPECTED_TRAFFIC_FIELD_OFFSETS["bursts"], 1 << 30
    )
    yield "traffic_burst_plan_mismatch", bytes(_recompute(m)), None

    allocations = secs[st["allocations"]]
    m = bytearray(base)
    struct.pack_into(
        "<I", m, allocations["off"] + A.ALLOCATIONS_FIELD_OFFSETS["alignment_bytes"], 3
    )
    yield "allocations_alignment_not_pow2", bytes(_recompute(m)), None

    streams = secs[st["streams"]]
    m = bytearray(base)
    struct.pack_into("<H", m, streams["off"] + A.STREAMS_FIELD_OFFSETS["flags"], 0xFF)
    yield "streams_unknown_flag_bits", bytes(_recompute(m)), None

    entrypoints = secs[st["entrypoints"]]
    m = bytearray(base)
    struct.pack_into(
        "<H", m, entrypoints["off"] + A.ENTRYPOINTS_FIELD_OFFSETS["lifecycle_stream_id"], 99
    )
    yield "entrypoints_unknown_stream", bytes(_recompute(m)), None

    # Header: unknown required feature bit (outside payload SHA).
    m = bytearray(base); m[104] = 1
    yield "header_required_feature_bit", bytes(m), None

    # Section directory: unknown section type (recompute covers payload).
    m = bytearray(base); m[struct.unpack_from("<Q", m, 24)[0]] = 77
    yield "directory_unknown_section_type", bytes(_recompute(m)), None

    # Strings: order violation via directory offset.
    strings = secs[A.SECTION_TYPE.STRINGS]
    if strings["count"] >= 2:
        m = bytearray(base)
        struct.pack_into("<I", m, strings["off"] + 4, 1)
        yield "strings_directory_not_dense", bytes(_recompute(m)), None
        m = bytearray(base)
        struct.pack_into("<I", m, strings["off"] + 8, 1 << 30)
        yield "strings_directory_out_of_blob", bytes(_recompute(m)), None

    # ---- Gate 2 review corpus: the reviewer-reproduced divergence fields.

    # H1: NORMAL event with a declared producer that mismatches the single
    # actual producer (flip the declared id).
    events_sec = secs[st["events"]]
    m = bytearray(base)
    struct.pack_into(
        "<I", m, events_sec["off"] + A.EVENTS_FIELD_OFFSETS["producer_command_id"], 9999
    )
    yield "events_producer_mismatch", bytes(_recompute(m)), None
    m = bytearray(base)
    struct.pack_into(
        "<H", m, events_sec["off"] + A.EVENTS_FIELD_OFFSETS["kind"], 99
    )
    yield "events_unknown_kind", bytes(_recompute(m)), None

    # H2: allocation declared HBM instead of CORE_SRAM.
    m = bytearray(base)
    struct.pack_into(
        "<H", m, allocations["off"] + A.ALLOCATIONS_FIELD_OFFSETS["memory_space"],
        A.MEMORY_SPACE.HBM,
    )
    yield "allocations_non_sram_space", bytes(_recompute(m)), None
    m = bytearray(base)
    struct.pack_into(
        "<Q", m, allocations["off"] + A.ALLOCATIONS_FIELD_OFFSETS["offset_bytes"],
        (1 << 64) - 63,
    )
    yield "allocations_offset_wrap", bytes(_recompute(m)), None

    # H3: physical_storage_bytes < useful_bytes and a stride below
    # row_bytes (only meaningful on multi-row descriptors; single-core
    # golden is single-row, so exercise the physical check).
    m = bytearray(base)
    struct.pack_into(
        "<Q", m,
        descriptors["off"] + A.DMA_DESCRIPTORS_FIELD_OFFSETS["physical_storage_bytes"],
        0,
    )
    yield "descriptors_physical_lt_useful", bytes(_recompute(m)), None
    m = bytearray(base)
    struct.pack_into(
        "<I", m,
        descriptors["off"] + A.DMA_DESCRIPTORS_FIELD_OFFSETS["completion_event"],
        9999,
    )
    yield "descriptors_unknown_completion_event", bytes(_recompute(m)), None

    # H8: two descriptors bound to the same DMA command (the second
    # command's descriptor vanishes at the same time).
    m = bytearray(base)
    struct.pack_into(
        "<I", m,
        descriptors["off"] + A.DMA_DESCRIPTORS_BYTES +
        A.DMA_DESCRIPTORS_FIELD_OFFSETS["command_id"],
        1,
    )
    yield "descriptors_command_id_collision", bytes(_recompute(m)), None

    # H7: profile rank above the ABI limit; shard span beyond allocation.
    profiles_sec = secs[st["profiles"]]
    m = bytearray(base)
    struct.pack_into(
        "<H", m, profiles_sec["off"] + A.PROFILES_FIELD_OFFSETS["rank"], 9
    )
    yield "profiles_rank_gt8", bytes(_recompute(m)), None
    shards_sec = secs[st["shards"]]
    m = bytearray(base)
    struct.pack_into(
        "<Q", m,
        shards_sec["off"] + A.SHARDS_FIELD_OFFSETS["allocation_offset"],
        (1 << 64) - 64,
    )
    yield "shards_offset_exceeds_allocation", bytes(_recompute(m)), None

    # H4: fixed-record section size that is a legal factor of the span
    # (PROFILES with record_bytes 40 instead of 80 and count doubled).
    profiles = secs[st["profiles"]]
    m = bytearray(base)
    struct.pack_into("<I", m, profiles["entry"] + 4, A.PROFILES_BYTES // 2)
    struct.pack_into("<Q", m, profiles["entry"] + 24, profiles["count"] * 2)
    yield "profiles_fixed_record_size", bytes(_recompute(m)), "E_ABI_SECTION_RANGE"

    # H5: traffic row command/kind/segments drift.
    m = bytearray(base)
    struct.pack_into(
        "<I", m, traffic["off"] + A.EXPECTED_TRAFFIC_FIELD_OFFSETS["command_id"], 9999
    )
    yield "traffic_command_mismatch", bytes(_recompute(m)), None
    m = bytearray(base)
    struct.pack_into(
        "<H", m, traffic["off"] + A.EXPECTED_TRAFFIC_FIELD_OFFSETS["kind"], 4
    )
    yield "traffic_kind_mismatch", bytes(_recompute(m)), None
    m = bytearray(base)
    struct.pack_into(
        "<I", m, traffic["off"] + A.EXPECTED_TRAFFIC_FIELD_OFFSETS["segments"], 99
    )
    yield "traffic_segments_mismatch", bytes(_recompute(m)), None
    m = bytearray(base)
    struct.pack_into(
        "<I", m, traffic["off"] + A.EXPECTED_TRAFFIC_FIELD_OFFSETS["w_beats"], 1 << 30
    )
    yield "traffic_w_beats_mismatch", bytes(_recompute(m)), None

    # H6: REPEAT count = 0 inside a REPEAT_V1 attr (repeat program below
    # exercises this through the dedicated builder case; mutate the first
    # attr payload here for the single-core program's GEMM: instead flip
    # the GEMM attr kind to REPEAT_V1 whose count field is 0).
    m = bytearray(base)
    struct.pack_into(
        "<H", m, attrs["off"] + A.OP_ATTRS_FIELD_OFFSETS["kind"], A.ATTR_KIND.REPEAT_V1
    )
    yield "attrs_opcode_binding_mismatch", bytes(_recompute(m)), None

    # M1: invalid UTF-8 string byte.
    if strings["count"] >= 1:
        first_off = struct.unpack_from("<I", base, strings["off"] + 4)[0]
        first_len = struct.unpack_from("<I", base, strings["off"] + 8)[0]
        if first_len >= 1:
            m = bytearray(base)
            dir_span = 4 + strings["count"] * 8
            m[strings["off"] + dir_span + first_off] = 0xFF
            yield "strings_invalid_utf8", bytes(_recompute(m)), None

    # Review batch: GEMM workload exactly 2^63 must fail closed on both
    # sides under the shared schedulable-range rule.
    m = bytearray(base)
    for index in range(attrs["count"]):
        kind = struct.unpack_from("<H", base, attrs["off"] + index * A.OP_ATTRS_BYTES)[0]
        if kind == A.ATTR_KIND.GEMM_V1:
            record = attrs["off"] + index * A.OP_ATTRS_BYTES
            dims = A.GEMM_V1_FIELD_OFFSETS
            base_off = record + 4
            m = bytearray(base)
            struct.pack_into("<I", m, base_off + dims["m"], 1 << 21)
            struct.pack_into("<I", m, base_off + dims["n"], 1 << 21)
            struct.pack_into("<I", m, base_off + dims["k"], 1 << 21)
            yield "gemm_work_2e63", bytes(_recompute(m)), "E_ABI_OVERFLOW"
            break

    # Optional zero-record SOURCE_MAP is legal (built via encoder below in
    # the dedicated test); unknown optional type is not.


@pytest.fixture(scope="module")
def arch():
    _python_verdict.arch = load_arch(ARCH_PATH)
    return _python_verdict.arch


@pytest.fixture(scope="module")
def golden_blob(arch):
    return encode_program(build_single_core_program(arch))


@pytest.fixture(scope="session")
def driver(tmp_path_factory):
    out = tmp_path_factory.mktemp("driver")
    binary = out / "mutation_driver"
    compile_cmd = [
        "g++", "-std=c++17", "-g", "-O1",
        "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
        *[f"-I{path}" for path in INCLUDES],
        str(DRIVER_SRC),
        str(REPO / "src/dev/ai_mesh/mesh_binary.cc"),
        str(REPO / "src/dev/ai_mesh/mesh_ir_verifier.cc"),
        str(REPO / "src/dev/ai_mesh/mesh_splitter.cc"),
        "-o", str(binary),
    ]
    result = subprocess.run(compile_cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return str(binary)


def test_mutation_corpus_languages_agree(golden_blob, driver, tmp_path, arch):
    disagreements = []
    count = 0
    for name, mutated, expected_code in _mutations(golden_blob):
        py = _python_verdict(mutated)
        cpp = _cpp_verdict(mutated, driver, tmp_path, arch)
        count += 1
        if py != cpp:
            disagreements.append(f"{name}: python={py} cpp={cpp}")
        if expected_code:
            if py == "ACCEPTED" or expected_code not in py:
                disagreements.append(f"{name}: python={py} want {expected_code}")
            if cpp == "ACCEPTED" or expected_code not in cpp:
                disagreements.append(f"{name}: cpp={cpp} want {expected_code}")
    assert count >= 30, f"corpus too small: {count}"
    assert not disagreements, "\n".join(disagreements)


def test_parser_overflow_inputs_are_deterministic_errors(golden_blob, driver, tmp_path, arch):
    base = bytearray(golden_blob)

    cases = {}

    # offset = UINT64_MAX - 7, size = 16 (wrapping end).
    m = bytearray(base)
    dir_off = struct.unpack_from("<Q", m, 24)[0]
    struct.pack_into("<Q", m, dir_off + 8, (1 << 64) - 7)
    struct.pack_into("<Q", m, dir_off + 16, 16)
    cases["offset_wrap"] = bytes(m)

    # Fixed-table count overflow: COMMANDS (record_bytes 40) claims 2^60.
    commands_entry = None
    for row in _sections(m):
        if row["type"] == A.SECTION_TYPE.COMMANDS:
            commands_entry = row["entry"]
    m = bytearray(base)
    struct.pack_into("<Q", m, commands_entry + 24, 1 << 60)
    cases["count_huge_fixed_table"] = bytes(_recompute(m))

    # STRINGS size < 4 (blob section smaller than its directory header).
    strings_entry = None
    for row in _sections(m):
        if row["type"] == A.SECTION_TYPE.STRINGS:
            strings_entry = row["entry"]
    m = bytearray(base)
    struct.pack_into("<Q", m, strings_entry + 16, 2)
    struct.pack_into("<Q", m, strings_entry + 24, 0)
    cases["strings_size_lt4"] = bytes(_recompute(m))

    # Payload string directory span beyond the section size: the count word
    # inside the blob claims more entries than the section can hold.
    strings_payload = None
    for row in _sections(m):
        if row["type"] == A.SECTION_TYPE.STRINGS:
            strings_payload = row["off"]
    m = bytearray(base)
    struct.pack_into("<I", m, strings_payload, 1 << 20)
    cases["strings_span_overflow"] = bytes(_recompute(m))

    for name, blob in cases.items():
        py = _python_verdict(blob)
        assert py.startswith("DECODE:") or py.startswith("VERIFY:"), name
        cpp = _cpp_verdict(blob, driver, tmp_path, arch)
        assert cpp.startswith("DECODE:") or cpp.startswith("VERIFY:"), f"{name}: {cpp}"
        assert cpp not in ("ACCEPTED",) or py == "ACCEPTED"


def test_optional_section_record_size_parity(arch, driver, tmp_path):
    """An empty optional fixed table (SOURCE_MAP) is legal with its schema
    record size and rejected with record_bytes=0 (both readers)."""
    from mesh_ir.golden_programs import build_single_core_program
    from tests.golden.support.optional_section import rebuild_with_extra_section

    blob = encode_program(build_single_core_program(arch))
    legal = rebuild_with_extra_section(
        blob, A.SECTION_TYPE.SOURCE_MAP, b"", record_bytes=A.SOURCE_MAP_BYTES)
    assert _python_verdict(legal) == "ACCEPTED"
    assert _cpp_verdict(legal, driver, tmp_path, arch) == "ACCEPTED"

    illegal = bytearray(legal)
    for row in _sections(illegal):
        if row["type"] == A.SECTION_TYPE.SOURCE_MAP:
            struct.pack_into("<I", illegal, row["entry"] + 4, 0)
    illegal = bytes(_recompute(illegal))
    py = _python_verdict(illegal)
    cpp = _cpp_verdict(illegal, driver, tmp_path, arch)
    assert py == cpp and "E_ABI_SECTION_RANGE" in py, (py, cpp)


def test_repeat_flags_const_parity(arch, driver, tmp_path):
    """REPEAT payload flags (const 0) is enforced even for const==0."""
    from mesh_ir.golden_programs import build_repeat_program

    blob = encode_program(build_repeat_program(arch))
    attrs = None
    for row in _sections(bytearray(blob)):
        if row["type"] == A.SECTION_TYPE.OP_ATTRS:
            attrs = row
    assert attrs is not None
    mutated = bytearray(blob)
    for index in range(attrs["count"]):
        kind = struct.unpack_from(
            "<H", blob, attrs["off"] + index * A.OP_ATTRS_BYTES)[0]
        if kind == A.ATTR_KIND.REPEAT_V1:
            struct.pack_into(
                "<I", mutated,
                attrs["off"] + index * A.OP_ATTRS_BYTES + 4 +
                A.REPEAT_V1_FIELD_OFFSETS["flags"], 1)
            break
    else:
        raise AssertionError("repeat program carries no REPEAT attr")
    mutated = bytes(_recompute(mutated))
    py = _python_verdict(mutated)
    cpp = _cpp_verdict(mutated, driver, tmp_path, arch)
    assert py == cpp and "E_ABI_RESERVED" in py, (py, cpp)


def _program_verdicts(program, driver, tmp_path, arch):
    blob = encode_program(program)
    return (
        _python_verdict(blob),
        _cpp_verdict(blob, driver, tmp_path, arch),
    )


def _assert_shared_rejection(program, driver, tmp_path, arch, code):
    py, cpp = _program_verdicts(program, driver, tmp_path, arch)
    assert py == cpp, (py, cpp)
    assert py == f"VERIFY:{code}", py


def test_operand_allocation_must_match_shard_allocation(arch, driver, tmp_path):
    program = copy.deepcopy(build_single_core_program(arch))
    command = next(c for c in program.commands if c.opcode == A.OPCODE.GEMM)
    first = command.operand_begin
    program.command_operands[first] = dataclasses.replace(
        program.command_operands[first],
        allocation_id=program.command_operands[first + 1].allocation_id,
    )
    _assert_shared_rejection(program, driver, tmp_path, arch, "E_ABI_BOUNDS")


def test_compute_result_operand_must_be_read_write(arch, driver, tmp_path):
    program = copy.deepcopy(build_single_core_program(arch))
    command = next(c for c in program.commands if c.opcode == A.OPCODE.GEMM)
    result_index = command.operand_begin + command.operand_count - 1
    program.command_operands[result_index] = dataclasses.replace(
        program.command_operands[result_index], access=A.ACCESS_KIND.READ_ONLY
    )
    _assert_shared_rejection(program, driver, tmp_path, arch, "E_ABI_BOUNDS")


def test_endpoint_shard_must_belong_to_endpoint_tensor(arch, driver, tmp_path):
    program = copy.deepcopy(build_single_core_program(arch))
    descriptor = program.dma_descriptors[0]
    other_tensor = next(
        tensor.tensor_id
        for tensor in program.tensors
        if tensor.tensor_id != descriptor.src.tensor_id
    )
    program.dma_descriptors[0] = dataclasses.replace(
        descriptor,
        src=dataclasses.replace(descriptor.src, tensor_id=other_tensor),
    )
    _assert_shared_rejection(program, driver, tmp_path, arch, "E_ABI_BOUNDS")


@pytest.mark.parametrize(
    "side,region_kind",
    (("src", "HOST_SHARED"), ("dst", "HBM")),
)
def test_endpoint_memory_space_must_match_region_kind(
    arch, driver, tmp_path, side, region_kind
):
    program = copy.deepcopy(build_single_core_program(arch))
    descriptor = program.dma_descriptors[0]
    region_id = next(
        index for index, region in enumerate(arch.regions)
        if region.kind == region_kind
    )
    endpoint = dataclasses.replace(getattr(descriptor, side), region_id=region_id)
    program.dma_descriptors[0] = dataclasses.replace(
        descriptor, **{side: endpoint}
    )
    _assert_shared_rejection(program, driver, tmp_path, arch, "E_DMA_RANGE")


def test_remote_endpoint_owner_must_use_sentinel(arch, driver, tmp_path):
    program = copy.deepcopy(build_single_core_program(arch))
    descriptor = program.dma_descriptors[0]
    program.dma_descriptors[0] = dataclasses.replace(
        descriptor,
        src=dataclasses.replace(descriptor.src, owner_core=0),
    )
    _assert_shared_rejection(program, driver, tmp_path, arch, "E_ABI_BOUNDS")


def test_zero_length_endpoint_may_equal_view_and_region_end(
    arch, driver, tmp_path
):
    program = copy.deepcopy(build_zero_dma_program(arch))
    descriptor = program.dma_descriptors[0]
    shard = next(s for s in program.shards if s.shard_id == descriptor.dst.shard_id)
    allocation = next(
        a for a in program.allocations if a.allocation_id == shard.allocation_id
    )
    view_end = allocation.offset_bytes + shard.allocation_offset + shard.span_bytes
    region_end = arch.region_by_id(descriptor.src.region_id).bytes
    program.dma_descriptors[0] = dataclasses.replace(
        descriptor,
        src=dataclasses.replace(descriptor.src, offset_bytes=region_end),
        dst=dataclasses.replace(descriptor.dst, offset_bytes=view_end),
    )
    py, cpp = _program_verdicts(program, driver, tmp_path, arch)
    assert py == cpp == "ACCEPTED", (py, cpp)


def _program_for_compute_opcode(arch, opcode, dtype):
    if opcode in (A.OPCODE.GEMM, A.OPCODE.BMM):
        program = copy.deepcopy(build_single_core_program(arch))
        index = next(
            i for i, command in enumerate(program.commands)
            if command.opcode == A.OPCODE.GEMM
        )
        command = program.commands[index]
        if opcode == A.OPCODE.BMM:
            command = dataclasses.replace(
                command, opcode=opcode, engine=A.OPCODE_ENGINE[opcode]
            )
            program.commands[index] = command
        attr = program.op_attrs[command.attr_index - 1]
        values = dict(zip(attr.payload_fields, attr.payload))
        values["dtype"] = dtype
        program.op_attrs[command.attr_index - 1] = dataclasses.replace(
            attr,
            kind=(
                A.ATTR_KIND.GEMM_V1
                if opcode == A.OPCODE.GEMM
                else A.ATTR_KIND.BMM_V1
            ),
            payload=tuple(values[field] for field in attr.payload_fields),
        )
        return program
    if opcode == A.OPCODE.LOCAL_REDUCE:
        from mesh_ir.golden_programs import build_dual_core_program

        program = copy.deepcopy(build_dual_core_program(arch))
        command = next(
            c for c in program.commands
            if c.opcode == A.OPCODE.LOCAL_REDUCE
        )
        attr = program.op_attrs[command.attr_index - 1]
        values = dict(zip(attr.payload_fields, attr.payload))
        values["dtype"] = dtype
        program.op_attrs[command.attr_index - 1] = dataclasses.replace(
            attr, payload=tuple(values[field] for field in attr.payload_fields)
        )
        return program
    from mesh_ir.golden_programs import build_repeat_program

    program = copy.deepcopy(build_repeat_program(arch))
    index = next(
        i for i, command in enumerate(program.commands)
        if command.opcode == A.OPCODE.ELEMENTWISE
    )
    command = program.commands[index]
    program.commands[index] = dataclasses.replace(
        command, opcode=opcode, engine=A.OPCODE_ENGINE[opcode]
    )
    attr_index = command.attr_index - 1
    if opcode == A.OPCODE.ELEMENTWISE:
        attr = program.op_attrs[attr_index]
        values = dict(zip(attr.payload_fields, attr.payload))
        values["dtype"] = dtype
        program.op_attrs[attr_index] = dataclasses.replace(
            attr, payload=tuple(values[field] for field in attr.payload_fields)
        )
    elif opcode == A.OPCODE.SOFTMAX:
        program.op_attrs[attr_index] = dataclasses.replace(
            program.op_attrs[attr_index],
            kind=A.ATTR_KIND.SOFTMAX_V1,
            payload=(64, dtype, A.VECTOR_ALGORITHM.STANDARD, 0),
            payload_fields=("axis_size", "dtype", "algorithm", "reserved"),
        )
    else:
        program.op_attrs[attr_index] = dataclasses.replace(
            program.op_attrs[attr_index],
            kind=A.ATTR_KIND.NORM_V1,
            payload=(64, dtype, A.VECTOR_ALGORITHM.STANDARD, 0),
            payload_fields=(
                "element_count", "dtype", "algorithm", "reserved"
            ),
        )
    return program


@pytest.mark.parametrize(
    "opcode,dtype",
    (
        (A.OPCODE.GEMM, A.DTYPE.INT32),
        (A.OPCODE.BMM, A.DTYPE.INT32),
        (A.OPCODE.ELEMENTWISE, A.DTYPE.INT32),
        (A.OPCODE.LOCAL_REDUCE, A.DTYPE.INT8),
        (A.OPCODE.SOFTMAX, A.DTYPE.INT32),
        (A.OPCODE.NORM, A.DTYPE.INT32),
    ),
)
def test_known_dtype_without_engine_capability_is_rejected(
    arch, driver, tmp_path, opcode, dtype
):
    _assert_shared_rejection(
        _program_for_compute_opcode(arch, opcode, dtype),
        driver,
        tmp_path,
        arch,
        "E_CAPABILITY_MISMATCH",
    )


@pytest.mark.parametrize("opcode", (A.OPCODE.SOFTMAX, A.OPCODE.NORM))
def test_vector_reduction_without_concrete_phase_plan_is_rejected(
    arch, driver, tmp_path, opcode
):
    _assert_shared_rejection(
        _program_for_compute_opcode(arch, opcode, A.DTYPE.FP16),
        driver,
        tmp_path,
        arch,
        "E_ABI_BOUNDS",
    )


def _mutate_strings_metadata(blob, mutation):
    data = bytearray(blob)
    strings = _section_offsets(data)[A.SECTION_TYPE.STRINGS]
    if mutation == "record_bytes":
        struct.pack_into("<I", data, strings["entry"] + 4, strings["size"])
        struct.pack_into("<Q", data, strings["entry"] + 24, 1)
    elif mutation == "directory_count":
        struct.pack_into("<Q", data, strings["entry"] + 24, strings["count"] + 1)
    else:
        count = struct.unpack_from("<I", data, strings["off"])[0]
        last = strings["off"] + 4 + (count - 1) * A.STRING_DIR_BYTES
        size = struct.unpack_from("<I", data, last + 4)[0]
        assert size > 1
        struct.pack_into("<I", data, last + 4, size - 1)
    return bytes(_recompute(data))


@pytest.mark.parametrize(
    "mutation", ("record_bytes", "directory_count", "trailing_data")
)
def test_strings_section_metadata_is_exact(
    golden_blob, arch, driver, tmp_path, mutation
):
    blob = _mutate_strings_metadata(golden_blob, mutation)
    py = _python_verdict(blob)
    cpp = _cpp_verdict(blob, driver, tmp_path, arch)
    assert py == cpp == "DECODE:E_ABI_SECTION_RANGE", (py, cpp)
