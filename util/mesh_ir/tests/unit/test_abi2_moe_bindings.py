from dataclasses import replace
import subprocess

from mesh_ir.abi.encoder import encode_program
from tests.unit.test_abi2_admission import admission_probe

import pytest

from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Access, TensorRole
from mesh_ir.memory_partitions import ArchPartition
from mesh_ir.model import ContentDigest, MoeDynamicRegion, MoeExpertSpec, MoeKernelSpec, MoeLayerSpec
from mesh_ir.scheduled.model import AuthoredProgramOrigin, HaltAttrs, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.scheduled.verify import verify_program
from tests.unit.test_gate2_authored_facade import _arch, _inputs


@pytest.fixture(scope='module')
def moe_profiles():
    arch = replace(_arch(), sram_partitions=(
        ArchPartition(A.SRAM_PARTITION_KIND.STATIC_PROGRAM, 0, 4096, 64, 8),
        ArchPartition(A.SRAM_PARTITION_KIND.RUNTIME_SCRATCH, 4096, 8192, 64, 8),
    ))
    builder = ProgramBuilder(arch, AuthoredProgramOrigin('abi2', 'moe-profiles', 1))
    for ordinal, name in enumerate(('first', 'second'), 1):
        records, allocations, backings, slots = _inputs(arch)
        weight = replace(records.tensors[0], role=TensorRole.WEIGHT, access=Access.READ_ONLY, content_sha256=f'{ordinal:02x}' * 32)
        records = replace(records, tensors=(weight, *records.tensors[1:]))
        variant = builder.variant('forward', name, name, records=records, allocations=allocations, external_backings=backings, binding_slots=slots)
        stream = variant.stream(3, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
        stream.control_command(RequestBeginAttrs())
        for operation in (9, 10, 11):
            stream.kernel_command(operation)
        stream.control_command(RequestEndAttrs())
        stream.control_command(HaltAttrs())
    program = builder.build()
    layers, experts, regions, kernels, digests = [], [], [], [], []
    symbol = next(index for index, item in enumerate(program.strings, 1) if item.value == 'input')
    for index, variant in enumerate(program.semantics.variants):
        layer_id = index + 1
        insert = program.commands[variant.membership.commands.first_id + 1]
        tensor = program.tensors[variant.membership.abi_tensors.first_id - 1]
        digests.append(ContentDigest(A.CONTENT_DIGEST_OBJECT_KIND['TENSOR'], 0, tensor.tensor_id, tensor.content_sha256))
        layers.append(MoeLayerSpec(layer_id, index, index, 1, 1, 8, 8, 0x10000, A.MOE_OVERFLOW_POLICY.DROP, A.MOE_TRANSPORT_MODE.VARIABLE_ALL_TO_ALL_V, index, 1, 1, 1, 1, 16, 8, 4, 8, 0, 17))
        experts.append(MoeExpertSpec(layer_id, 0, 0, 3, 0, symbol, 0, 16, index))
        regions.append(MoeDynamicRegion(1, layer_id, 3, 0, insert.command_id, insert.command_id + 1, insert.signal_event, 4096 + index * 4096, 4096, 64, 16, 16, 8, 4, 8, 0))
        kernels.append(MoeKernelSpec(layer_id, A.OPCODE.GEMM, A.DTYPE.FP32, A.DTYPE.FP32, A.DTYPE.FP32, 1, 2, 2, 0, A.MOE_COMBINE_KIND.LOCAL_REDUCE, 1, 0x10000, 4, 2, 8, 8, 0, 16, 64, 1, 4, 2, 0))
    program = replace(program, required_features=program.required_features | A.DYNAMIC_MOE_V1,
                   content_digests=tuple(digests), moe_layer_specs=tuple(layers),
                   moe_expert_specs=tuple(experts), moe_dynamic_regions=tuple(regions),
                   moe_kernel_specs=tuple(kernels))
    return replace(program, semantic_sha256=semantic_sha256(program.semantic_dict())), arch


def test_moe_same_symbol_resolves_each_layer_admitted_variant(moe_profiles):
    program, arch = moe_profiles
    assert verify_program(program, arch).program is program


def test_tensor_digest_flag_is_derived_from_logical_content(moe_profiles):
    program, _ = moe_profiles
    assert all(bool(tensor.flags & A.TENSOR_FLAGS.HAS_CONTENT_SHA256) == (logical.content_sha256 is not None)
               for tensor, logical in zip(program.tensors, program.semantics.kernel_tensors, strict=True))


def mutate_moe_binding(program, mutation):
    if mutation == 'foreign-digest':
        expert = replace(program.moe_expert_specs[1], weight_digest_index=0)
        program = replace(program, moe_expert_specs=(program.moe_expert_specs[0], expert))
    elif mutation == 'foreign-gate':
        first, second = program.moe_dynamic_regions
        region = replace(second, insert_after_command_id=first.insert_after_command_id,
                         resume_before_command_id=first.resume_before_command_id, entry_event_id=first.entry_event_id)
        program = replace(program, moe_dynamic_regions=(first, region))
    elif mutation == 'missing-symbol':
        expert = replace(program.moe_expert_specs[1], weight_symbol_id=len(program.strings) + 1)
        program = replace(program, moe_expert_specs=(program.moe_expert_specs[0], expert))
    else:
        tensors = list(program.semantics.kernel_tensors)
        tensors[2] = replace(tensors[2], content_sha256='ab' * 32)
        program = replace(program, semantics=replace(program.semantics, kernel_tensors=tuple(tensors)))
    program = replace(program, semantic_sha256=semantic_sha256(program.semantic_dict()))
    return program


@pytest.mark.parametrize('mutation', ['foreign-digest', 'foreign-gate', 'missing-symbol', 'logical-digest'])
def test_moe_binding_rejects_cross_variant_evidence(moe_profiles, mutation):
    program, arch = moe_profiles
    assert verify_program(program, arch).program is program
    program = mutate_moe_binding(program, mutation)
    with pytest.raises(MeshIrError, match='E_RELOCATION|E_ABI_BOUNDS'):
        verify_program(program, arch)


@pytest.mark.parametrize('admission_probe', ['moe'], indirect=True)
@pytest.mark.parametrize('mutation', ['healthy', 'foreign-digest', 'foreign-gate', 'missing-symbol', 'logical-digest'])
def test_cpp_moe_binding_matches_python_admission(admission_probe, moe_profiles, mutation, tmp_path):
    program, arch = moe_profiles
    if mutation != 'healthy':
        program = mutate_moe_binding(program, mutation)
    path = tmp_path / 'program.mshb'
    path.write_bytes(encode_program(program))
    result = subprocess.run([str(admission_probe), str(path)], capture_output=True, text=True)
    if mutation == 'healthy':
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == 'ACCEPTED'
    else:
        assert result.returncode == 1, result.stdout + result.stderr
        assert 'E_RELOCATION' in result.stdout or 'E_ABI_BOUNDS' in result.stdout
