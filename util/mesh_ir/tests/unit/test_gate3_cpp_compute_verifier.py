import dataclasses

import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.ir.common import Engine
from mesh_ir.model import MeshIrError
from mesh_ir.scheduled.model import ControlExecution, ExecutionWorkPhase
from mesh_ir.scheduled.verify import verify_program
from tests.golden.support.compiler_fixtures import (
    compile_matrix_program,
    compile_softmax_program,
    compile_unary_chain_program,
)
from tests.golden.test_mutation_corpus import _cpp_verdict
from tests.unit.test_gate3_cpp_verifier import ROOT, _run_program


@pytest.fixture(scope="module")
def compiler_compute_program():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    return arch, compile_unary_chain_program(arch)


@pytest.fixture(scope="module")
def compiler_softmax_program():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    return arch, compile_softmax_program(arch, 2)


def _rebind_arch(program, arch):
    changed = dataclasses.replace(
        program,
        arch_digest=arch.digest(),
        semantic_sha256="",
    )
    return dataclasses.replace(
        changed,
        semantic_sha256=semantic_sha256(changed.semantic_dict()),
    )


def _fp32_softmax_arch(arch):
    return dataclasses.replace(
        arch,
        vector_elements_per_cycle={
            "fp32": arch.vector_elements_per_cycle["fp32"],
        },
        reduce_ops_per_cycle={
            "fp32": arch.reduce_ops_per_cycle["fp32"],
        },
    )


def _command_semantic(program, opcode):
    operation = next(
        item for item in program.semantics.kernel_ops
        if item.opcode.name == opcode
    )
    index = next(
        index
        for index, item in enumerate(program.semantics.command_semantics)
        if getattr(item.source, "kernel_op_id", None) == operation.op_id
    )
    return operation, index, program.semantics.command_semantics[index]


def _replace_execution(program, command_index, phases):
    command_semantics = list(program.semantics.command_semantics)
    semantic = command_semantics[command_index]
    command_semantics[command_index] = dataclasses.replace(
        semantic,
        execution=dataclasses.replace(semantic.execution, phases=tuple(phases)),
    )
    changed = dataclasses.replace(
        program,
        semantics=dataclasses.replace(
            program.semantics,
            command_semantics=tuple(command_semantics),
        ),
    )
    return dataclasses.replace(
        changed,
        semantic_sha256=semantic_sha256(changed.semantic_dict()),
    )


def _replace_computation_attrs(program, operation, attrs):
    computations = tuple(
        dataclasses.replace(item, attrs=attrs)
        if item.computation_id == operation.computation_id
        else item
        for item in program.semantics.computations
    )
    changed = dataclasses.replace(
        program,
        semantics=dataclasses.replace(
            program.semantics,
            computations=computations,
        ),
    )
    return dataclasses.replace(
        changed,
        semantic_sha256=semantic_sha256(changed.semantic_dict()),
    )


def test_cpp_compute_verifier_accepts_compiler_multiphase_work(
    verifier_probe, tmp_path, compiler_compute_program
):
    _, program = compiler_compute_program
    _, _, softmax = _command_semantic(program, "SOFTMAX")
    _, _, norm = _command_semantic(program, "NORM")
    _, _, vector = _command_semantic(program, "VECTOR")
    assert len(softmax.execution.phases) == 5
    assert len(norm.execution.phases) == 4
    assert len(vector.execution.phases[0].work) == 4
    assert _run_program(verifier_probe, tmp_path, program) == "ACCEPTED"


def test_cpp_compute_verifier_checks_actual_multiphase_arch_capabilities(
    driver, tmp_path, compiler_softmax_program
):
    arch, program = compiler_softmax_program
    constrained_arch = _fp32_softmax_arch(arch)
    changed = _rebind_arch(program, constrained_arch)
    verify_program(changed, constrained_arch)
    assert _cpp_verdict(
        encode_program(changed), driver, tmp_path, constrained_arch
    ) == "ACCEPTED"


def test_cpp_compute_verifier_empty_work_needs_no_dtype_capability(
    driver, tmp_path
):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = compile_softmax_program(arch, 0)
    constrained_arch = _fp32_softmax_arch(arch)
    changed = _rebind_arch(program, constrained_arch)
    _, _, semantic = _command_semantic(changed, "SOFTMAX")
    assert semantic.execution.phases == ()
    verify_program(changed, constrained_arch)
    assert _cpp_verdict(
        encode_program(changed), driver, tmp_path, constrained_arch
    ) == "ACCEPTED"


@pytest.mark.parametrize(
    "missing",
    ("vector", "reduce"),
)
def test_cpp_compute_verifier_rejects_missing_multiphase_arch_capability(
    driver, tmp_path, compiler_softmax_program, missing
):
    arch, program = compiler_softmax_program
    constrained_arch = _fp32_softmax_arch(arch)
    if missing == "vector":
        constrained_arch = dataclasses.replace(
            constrained_arch,
            vector_elements_per_cycle={
                "fp16": arch.vector_elements_per_cycle["fp16"],
            },
        )
    else:
        constrained_arch = dataclasses.replace(
            constrained_arch,
            reduce_ops_per_cycle={
                "fp16": arch.reduce_ops_per_cycle["fp16"],
            },
        )
    changed = _rebind_arch(program, constrained_arch)
    with pytest.raises(MeshIrError) as caught:
        verify_program(changed, constrained_arch)
    assert caught.value.code == "E_CAPABILITY_MISMATCH"
    assert _cpp_verdict(
        encode_program(changed), driver, tmp_path, constrained_arch
    ) == "VERIFY:E_CAPABILITY_MISMATCH"


def test_cpp_compute_verifier_checks_matrix_epilogue_capability(
    driver, tmp_path
):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = compile_matrix_program(arch)
    _, _, semantic = _command_semantic(program, "MATRIX_EPILOGUE")
    assert tuple(work.engine for work in semantic.execution.phases[0].work) == (
        Engine.VECTOR,
        Engine.VECTOR,
    )
    constrained_arch = dataclasses.replace(
        arch,
        vector_elements_per_cycle={
            "fp16": arch.vector_elements_per_cycle["fp16"],
        },
    )
    changed = _rebind_arch(program, constrained_arch)
    with pytest.raises(MeshIrError) as caught:
        verify_program(changed, constrained_arch)
    assert caught.value.code == "E_CAPABILITY_MISMATCH"
    assert _cpp_verdict(
        encode_program(changed), driver, tmp_path, constrained_arch
    ) == "VERIFY:E_CAPABILITY_MISMATCH"


@pytest.mark.parametrize(
    "mutation",
    ("compensated", "work_order", "split", "phase_order", "merge"),
)
def test_cpp_compute_verifier_checks_ordered_phase_structure(
    verifier_probe, tmp_path, compiler_compute_program, mutation
):
    arch, program = compiler_compute_program
    opcode = "VECTOR" if mutation in ("compensated", "work_order", "split") else "SOFTMAX"
    _, command_index, semantic = _command_semantic(program, opcode)
    phases = list(semantic.execution.phases)
    if mutation == "compensated":
        work = list(phases[0].work)
        work[0] = dataclasses.replace(work[0], operations=work[0].operations + 1)
        work[1] = dataclasses.replace(work[1], operations=work[1].operations - 1)
        phases[0] = dataclasses.replace(phases[0], work=tuple(work))
    elif mutation == "work_order":
        work = list(phases[0].work)
        work[0], work[1] = work[1], work[0]
        phases[0] = dataclasses.replace(phases[0], work=tuple(work))
    elif mutation == "split":
        work = phases[0].work
        phases = [
            dataclasses.replace(phases[0], work=work[:2]),
            dataclasses.replace(phases[0], work=work[2:]),
        ]
    elif mutation == "phase_order":
        phases[0], phases[1] = phases[1], phases[0]
    else:
        phases = [
            ExecutionWorkPhase(phases[0].work + phases[2].work),
            phases[1],
            *phases[3:],
        ]
    changed = _replace_execution(program, command_index, phases)
    with pytest.raises(MeshIrError) as caught:
        verify_program(changed, arch)
    assert caught.value.code == "E_ABI_ENUM"
    assert _run_program(verifier_probe, tmp_path, changed).startswith(
        "VERIFY:E_ABI_ENUM:"
    )


def test_cpp_compute_verifier_derives_row_domain_from_semantic_axes(
    verifier_probe, tmp_path, compiler_compute_program
):
    arch, program = compiler_compute_program
    operation, _, _ = _command_semantic(program, "SOFTMAX")
    operations = list(program.semantics.kernel_ops)
    operation_index = operations.index(operation)
    semantic_attrs = dataclasses.replace(
        operation.attrs.semantic_attrs,
        axis=0,
    )
    operations[operation_index] = dataclasses.replace(
        operation,
        attrs=dataclasses.replace(
            operation.attrs,
            semantic_attrs=semantic_attrs,
        ),
    )
    changed = dataclasses.replace(
        program,
        semantics=dataclasses.replace(
            program.semantics,
            kernel_ops=tuple(operations),
        ),
    )
    changed = _replace_computation_attrs(changed, operation, semantic_attrs)
    with pytest.raises(MeshIrError) as caught:
        verify_program(changed, arch)
    assert caught.value.code == "E_EXPORT_LAYOUT"
    assert _run_program(verifier_probe, tmp_path, changed).startswith(
        "VERIFY:E_EXPORT_LAYOUT:"
    )


def test_cpp_compute_verifier_checks_logical_computation_attributes(
    driver, tmp_path, compiler_softmax_program
):
    arch, program = compiler_softmax_program
    operation, _, _ = _command_semantic(program, "SOFTMAX")
    computation = program.semantics.computations[operation.computation_id - 1]
    changed = _replace_computation_attrs(
        program,
        operation,
        dataclasses.replace(
            computation.attrs,
            zero_fully_masked_rows=not computation.attrs.zero_fully_masked_rows,
        ),
    )
    with pytest.raises(MeshIrError) as caught:
        verify_program(changed, arch)
    assert caught.value.code == "E_ABI_CHECKSUM"
    assert _cpp_verdict(
        encode_program(changed), driver, tmp_path, arch
    ) == "VERIFY:E_ABI_CHECKSUM"


def test_cpp_compute_verifier_rejects_wrong_execution_union(
    verifier_probe, tmp_path, compiler_softmax_program
):
    arch, program = compiler_softmax_program
    _, command_index, semantic = _command_semantic(program, "SOFTMAX")
    semantics = list(program.semantics.command_semantics)
    semantics[command_index] = dataclasses.replace(
        semantic,
        execution=ControlExecution(),
    )
    changed = dataclasses.replace(
        program,
        semantics=dataclasses.replace(
            program.semantics,
            command_semantics=tuple(semantics),
        ),
    )
    changed = dataclasses.replace(
        changed,
        semantic_sha256=semantic_sha256(changed.semantic_dict()),
    )
    with pytest.raises(MeshIrError) as caught:
        verify_program(changed, arch)
    assert caught.value.code == "E_ABI_ENUM"
    assert _run_program(verifier_probe, tmp_path, changed).startswith(
        "VERIFY:E_ABI_ENUM:"
    )


def test_cpp_compute_verifier_distinguishes_empty_command_engine(
    verifier_probe, tmp_path
):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = compile_softmax_program(arch, 0)
    _, _, semantic = _command_semantic(program, "SOFTMAX")
    commands = tuple(
        dataclasses.replace(command, engine=int(Engine.VECTOR))
        if command.command_id == semantic.command_id
        else command
        for command in program.commands
    )
    changed = dataclasses.replace(program, commands=commands)
    changed = dataclasses.replace(
        changed,
        semantic_sha256=semantic_sha256(changed.semantic_dict()),
    )
    assert _run_program(verifier_probe, tmp_path, changed).startswith(
        "VERIFY:E_ENGINE_MISMATCH:"
    )


def test_cpp_compute_verifier_distinguishes_command_engine_projection(
    verifier_probe, tmp_path, compiler_compute_program
):
    _, program = compiler_compute_program
    _, _, semantic = _command_semantic(program, "SOFTMAX")
    commands = tuple(
        dataclasses.replace(command, engine=int(Engine.REDUCE))
        if command.command_id == semantic.command_id
        else command
        for command in program.commands
    )
    changed = dataclasses.replace(program, commands=commands)
    changed = dataclasses.replace(
        changed,
        semantic_sha256=semantic_sha256(changed.semantic_dict()),
    )
    assert _run_program(verifier_probe, tmp_path, changed).startswith(
        "VERIFY:E_ENGINE_MISMATCH:"
    )
