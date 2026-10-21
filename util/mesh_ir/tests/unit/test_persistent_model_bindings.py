from dataclasses import replace

import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import agent_abi as AG
from mesh_ir.host_bindings import build_host_binding_plans
from mesh_ir.ir.common import Access, MemorySpace, StorageClass, TensorRole
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.serving_tensor_bindings import ServingTensorBindingCatalog
from tests.integration.support.workspace_program import build_workspace_program


@pytest.mark.parametrize('role', [TensorRole.WEIGHT, TensorRole.STATE, TensorRole.CONSTANT])
def test_host_model_binding_preserves_parameter_or_buffer_role(role):
    program, arch = build_workspace_program(model_role=role)
    verify_program(program, arch)
    plan, = build_host_binding_plans(program, [region.base for region in arch.regions])
    content, = [row for row in plan.requirements if row.kind == AG.BINDING_KIND.WEIGHT_EXTERNAL]
    assert content.platform_address == arch.regions[0].base + 0x1000
    assert content.required_bytes == 128
    binding = ProgramBindingCatalog(program).binding(1, 1, 'weight')
    assert binding.tensor.role is role


@pytest.mark.parametrize('role', [TensorRole.INPUT, TensorRole.OUTPUT, TensorRole.ACTIVATION])
def test_serving_binding_rejects_dynamic_tensor_as_persistent_model_content(role):
    program, arch = build_workspace_program(model_role=role)
    with pytest.raises(MeshIrError, match='E_BINDING_ROLE'):
        ServingTensorBindingCatalog(program)
    with pytest.raises(MeshIrError, match='E_BINDING_ROLE'):
        build_host_binding_plans(program, [region.base for region in arch.regions])


@pytest.mark.parametrize('mutation', ['write', 'digest', 'storage', 'memory'])
def test_model_content_contract_requires_immutable_external_hbm(mutation):
    program, _ = build_workspace_program(model_role=TensorRole.STATE)
    binding = ProgramBindingCatalog(program).binding(1, 1, 'weight')
    assert binding.is_immutable_model_content
    if mutation == 'memory':
        binding = replace(binding, object=replace(binding.object, memory_space=MemorySpace.HOST_SHARED))
    else:
        fields = {'write': {'access': Access.READ_WRITE}, 'digest': {'content_sha256': None},
                  'storage': {'storage_class': StorageClass.CORE_SRAM}}[mutation]
        binding = replace(binding, tensor=replace(binding.tensor, **fields))
    assert not binding.is_immutable_model_content


@pytest.mark.parametrize('role,access,has_content,accepted', [
    (TensorRole.WEIGHT, Access.READ_ONLY, True, True),
    (TensorRole.STATE, Access.READ_ONLY, True, True),
    (TensorRole.CONSTANT, Access.READ_ONLY, True, True),
    (TensorRole.INPUT, Access.READ_ONLY, True, False),
    (TensorRole.STATE, Access.READ_WRITE, True, False),
    (TensorRole.STATE, Access.READ_ONLY, False, False)])
def test_native_model_binding_admission_matches_python(driver, tmp_path, role, access, has_content, accepted):
    from mesh_ir.abi import encode_program
    from tests.golden.test_mutation_corpus import _cpp_verdict
    program, arch = build_workspace_program(model_role=role, model_access=access, model_has_content=has_content)
    expected = 'ACCEPTED' if accepted else 'VERIFY:E_BINDING_ROLE'
    if not accepted:
        with pytest.raises(MeshIrError, match='E_BINDING_ROLE'):
            verify_program(program, arch)
    else:
        verify_program(program, arch)
    assert _cpp_verdict(encode_program(program), driver, tmp_path, arch) == expected
