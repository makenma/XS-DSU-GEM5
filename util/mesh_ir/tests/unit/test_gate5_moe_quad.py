import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.abi.moe_verifier import verify_moe_v1
from mesh_ir.abi.verifier import verify_program
from mesh_ir.builder import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.moe_programs import (
    QUAD_CORES,
    QUAD_PAIRS,
    moe_quad_program,
    quad_reducer,
    quad_sender,
)

REPO = Path(__file__).resolve().parents[4]
QUAD_ARCH = REPO / "configs/example/ai_mesh/arch/mesh_4x4_moe.yaml"


def arch():
    return load_arch(QUAD_ARCH)


def test_gate5_quad_arch_covers_a_four_by_four_mesh():
    manifest = arch()
    assert (manifest.mesh_rows, manifest.mesh_cols) == (4, 4)
    assert len(manifest.core_ids) == QUAD_CORES
    cache = manifest.partition(A.SRAM_PARTITION_KIND.WEIGHT_CACHE)
    assert cache is not None
    assert manifest.sram_weight_cache_slot_bytes > 0
    assert cache.metadata_entries == (
        cache.bytes // manifest.sram_weight_cache_slot_bytes)


def test_gate5_quad_program_verifies_and_round_trips():
    manifest = arch()
    program = moe_quad_program(manifest)
    verify_program(program, manifest)
    verify_moe_v1(program, manifest)
    blob = encode_program(program)
    decoded = decode_program(blob)
    assert encode_program(decoded) == blob
    assert decoded.semantic_sha256() == program.semantic_sha256()


def test_gate5_quad_program_places_one_region_and_expert_per_core():
    program = moe_quad_program(arch())
    layer = program.moe_layer_specs[0]
    assert layer.expert_count == QUAD_CORES
    assert layer.top_k == 2
    assert layer.dynamic_region_count == QUAD_CORES
    assert [spec.core_id for spec in program.moe_expert_specs] == \
        list(range(QUAD_CORES))
    assert [spec.expert_id for spec in program.moe_expert_specs] == \
        list(range(QUAD_CORES))
    assert [region.core_id for region in program.moe_dynamic_regions] == \
        list(range(QUAD_CORES))
    region_ids = {region.region_id for region in program.moe_dynamic_regions}
    assert len(region_ids) == QUAD_CORES
    for region in program.moe_dynamic_regions:
        assert region.layer_id == layer.layer_id
        assert region.stream_id == 0


def test_gate5_quad_program_gates_inside_the_unique_lifecycle():
    program = moe_quad_program(arch())
    begins = [c for c in program.commands
              if c.opcode == A.OPCODE.REQUEST_BEGIN]
    ends = [c for c in program.commands if c.opcode == A.OPCODE.REQUEST_END]
    halts = [c for c in program.commands if c.opcode == A.OPCODE.HALT]
    assert len(begins) == 1 and len(ends) == 1
    assert begins[0].core_id == 0 and ends[0].core_id == 0
    assert len(halts) == QUAD_CORES
    assert sorted(halt.core_id for halt in halts) == list(range(QUAD_CORES))
    for region in program.moe_dynamic_regions:
        assert region.insert_after_command_id in [
            command.command_id for command in program.commands]
        assert region.resume_before_command_id in [
            command.command_id for command in program.commands]
    first = program.moe_dynamic_regions[0]
    assert first.insert_after_command_id < first.resume_before_command_id


def test_gate5_quad_program_pairs_spread_across_the_mesh():
    assert QUAD_PAIRS == QUAD_CORES // 2
    for pair in range(QUAD_PAIRS):
        reducer = quad_reducer(pair)
        sender = quad_sender(pair)
        assert sender == reducer + 1
        assert reducer % 2 == 0
    program = moe_quad_program(arch())
    transfers = {command.command_id: command for command in program.commands
                 if command.opcode == A.OPCODE.DMA_P2P_PUSH}
    assert len(transfers) == QUAD_PAIRS
    receives = [command for command in program.commands
                if command.opcode == A.OPCODE.RECV_WAIT]
    assert len(receives) == QUAD_PAIRS
    assert {command.core_id for command in receives} == \
        {quad_reducer(pair) for pair in range(QUAD_PAIRS)}
    assert {command.core_id for command in transfers.values()} == \
        {quad_sender(pair) for pair in range(QUAD_PAIRS)}
