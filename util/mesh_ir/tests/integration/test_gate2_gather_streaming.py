import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from examples.gather_carrier import HIDDEN, VOCAB
from mesh_ir.abi.decoder import decode_program
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.compile_service import export_and_compile_factory
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.kernel_verify import verify_kernel_memory
from mesh_ir.scheduled.records import variant_memory_records
from mesh_ir.scheduled.verify import verify_program

ROOT = Path(__file__).resolve().parents[4]
ARCH = ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml"
CONFIG = Path(__file__).resolve().parent / "gather_streaming_compile.yaml"
INPUTS = {"schema_version": "mesh-export-inputs-v1", "args": [
    {"dtype": "INT32", "shape": [3], "data": [7, 2, 7]},
]}


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    directory = tmp_path_factory.mktemp("gather")
    inputs = directory / "inputs.json"
    inputs.write_text(json.dumps(INPUTS))
    export_and_compile_factory("examples.gather_carrier:create_model", inputs, None,
                               ARCH, CONFIG, directory / "out")
    return decode_program((directory / "out/program.mshb").read_bytes())


def _gather_command(program):
    gathers = [command for command in program.commands
               if command.opcode == int(A.OPCODE.DMA_GATHER_LOAD)]
    assert len(gathers) == 1
    return gathers[0]


def test_duplicate_index_gather_of_an_external_table_compiles(compiled):
    program = compiled
    command = _gather_command(program)
    descriptors = [row for row in program.dma_descriptors
                   if row.command_id == command.command_id]
    assert len(descriptors) == 1
    gather = descriptors[0]
    assert gather.kind == int(A.DMA_KIND.GATHER_LOAD)
    assert gather.rows == 3 and gather.row_bytes == HIDDEN * 4
    assert gather.useful_bytes == 3 * HIDDEN * 4
    assert gather.src.memory_space == int(A.MEMORY_SPACE.HBM)
    assert gather.src.owner_core == 0xFFFF
    source_use = next(item for item in program.semantics.endpoint_uses
                      if item.descriptor_id == gather.descriptor_id and item.side.value == "SRC")
    assert source_use.use.region.shape == (VOCAB, HIDDEN)
    assert gather.physical_storage_bytes == VOCAB * HIDDEN * 4
    table = next(item for item in program.semantics.kernel_tensors
                 if item.tensor_id == gather.src.tensor_id)
    assert table.dtype.name == "FP32"
    assert table.shape == (VOCAB, HIDDEN)
    assert table.storage_extent_bytes > load_arch(ARCH).sram_bytes
    assert command.attr_index != 0
    attr = program.op_attrs[command.attr_index - 1]
    assert attr.kind == int(A.ATTR_KIND.GATHER_INDEX_V1)
    values = dict(zip(attr.payload_fields, attr.payload))
    assert values == dict(index_operand_index=0, index_offset_bytes=0,
                          index_stride_bytes=4, reserved=0)
    operand = program.command_operands[command.operand_begin + values["index_operand_index"]]
    index_descriptor = next(row for row in program.dma_descriptors
                            if row.kind == int(A.DMA_KIND.PREFETCH))
    assert operand.tensor_id == index_descriptor.src.tensor_id
    assert operand.shard_id == index_descriptor.dst.shard_id
    assert operand.allocation_id != 0
    consumer = next(item for item in program.semantics.kernel_ops
                    if item.opcode.value == "VECTOR")
    assert consumer.attrs.algorithm.value == "EMBEDDING_STAGED_COPY"
    assert len(consumer.reads) == 1


@pytest.mark.parametrize("mutation", ["missing", "foreign"])
def test_invalid_gather_index_binding_is_rejected(compiled, mutation):
    command = _gather_command(compiled)
    if mutation == "missing":
        commands = tuple(replace(item, attr_index=0) if item == command else item
                         for item in compiled.commands)
        changed = replace(compiled, commands=commands)
    else:
        attr = compiled.op_attrs[command.attr_index - 1]
        tainted = replace(attr, payload=(command.operand_count + 3,) + attr.payload[1:])
        changed = replace(compiled, op_attrs=tuple(tainted if item == attr else item
                                                  for item in compiled.op_attrs))
    changed = replace(changed, semantic_sha256=semantic_sha256(changed.semantic_dict()))
    with pytest.raises(MeshIrError, match="ABI attribute projection"):
        verify_program(changed, load_arch(ARCH))


@pytest.mark.parametrize("mutation", ["index_count", "table_slice", "row_width"])
def test_invalid_gather_geometry_is_rejected(compiled, mutation):
    records = variant_memory_records(compiled.semantics, compiled.semantics.variants[0])
    op = next(item for item in records.ops if getattr(item.attrs, "kind", None) == A.DMA_KIND.GATHER_LOAD)
    if mutation == "index_count":
        index = replace(op.reads[0], region=replace(op.reads[0].region, shape=(2,)))
        changed = replace(op, reads=(index, op.reads[1]))
    elif mutation == "table_slice":
        table = replace(op.reads[1], region=replace(op.reads[1].region, shape=(3, HIDDEN)))
        changed = replace(op, reads=(op.reads[0], table))
    else:
        target = replace(op.writes[0], region=replace(op.writes[0].region, shape=(3, HIDDEN - 1)))
        changed = replace(op, writes=(target,))
    altered = replace(records, ops=tuple(changed if item == op else item for item in records.ops))
    with pytest.raises(MeshIrError):
        verify_kernel_memory(altered)


@pytest.mark.parametrize("mutation", [None, "missing", "foreign", "offset", "stride"])
def test_cpp_admits_only_the_same_gather_contract(compiled, driver, tmp_path, mutation):
    from mesh_ir.abi.encoder import encode_program
    from tests.golden.test_mutation_corpus import _cpp_verdict

    command = _gather_command(compiled)
    changed = compiled
    if mutation == "missing":
        changed = replace(compiled, commands=tuple(
            replace(item, attr_index=0) if item == command else item for item in compiled.commands))
    elif mutation:
        attr = compiled.op_attrs[command.attr_index - 1]
        payload = list(attr.payload)
        field = {"foreign": 0, "offset": 1, "stride": 2}[mutation]
        payload[field] += command.operand_count + 3 if mutation == "foreign" else 4
        changed = replace(compiled, op_attrs=tuple(
            replace(item, payload=tuple(payload)) if item == attr else item for item in compiled.op_attrs))
    changed = replace(changed, semantic_sha256=semantic_sha256(changed.semantic_dict()))
    if mutation:
        with pytest.raises(MeshIrError) as rejected:
            verify_program(changed, load_arch(ARCH))
        expected = "VERIFY:" + rejected.value.code
    else:
        expected = "ACCEPTED"
    assert _cpp_verdict(encode_program(changed), driver, tmp_path, load_arch(ARCH)) == expected


@pytest.mark.parametrize("shape,tp", [((2, 3), 1), ((1, 3), 2), ((2, 3), 2)])
def test_batched_indices_stage_every_valid_occurrence(tmp_path, shape, tp, driver):
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps({"schema_version": "mesh-export-inputs-v1", "args": [
        {"dtype": "INT32", "shape": list(shape), "data": [7, 2, 7] * shape[0]},
    ]}))
    config = yaml.safe_load(CONFIG.read_text())
    config["parallelism"]["tensor_parallel"] = tp
    config["placement"]["allowed_cores"] = list(range(tp))
    config_path = tmp_path / "compile.yaml"
    config_path.write_text(yaml.safe_dump(config))
    export_and_compile_factory("examples.gather_carrier:create_model", inputs, None,
                               ARCH, config_path, tmp_path / "out")
    program = decode_program((tmp_path / "out/program.mshb").read_bytes())
    descriptors = [item for item in program.dma_descriptors
                   if item.kind == A.DMA_KIND.GATHER_LOAD]
    assert sum(item.rows for item in descriptors) == shape[0] * shape[1]
    assert all(item.rows > 0 for item in descriptors)
    assert sum(item.useful_bytes for item in descriptors) == shape[0] * shape[1] * HIDDEN * 4
    from mesh_ir.abi.encoder import encode_program
    from tests.golden.test_mutation_corpus import _cpp_verdict

    assert _cpp_verdict(encode_program(program), driver, tmp_path, load_arch(ARCH)) == "ACCEPTED"
