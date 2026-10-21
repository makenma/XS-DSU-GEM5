from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.compiler import compile_backend
from mesh_ir.frontend import FrontendRequest, load_graph
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import MemorySpace, TensorRole
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.scheduled.composition import VariantComposition, compose_program
from mesh_ir.scheduled.model import ExternalStorageAdaptation
from mesh_ir.serving_composition import attach_prefill_request, build_publication_program
from mesh_ir.tensor_payload import TensorPayload
from tests.integration.test_tensor_payload import payload_carrier


@pytest.fixture(scope='module')
def replicated_prefill(payload_carrier, tmp_path_factory):
    root = Path(__file__).resolve().parents[4]
    directory = tmp_path_factory.mktemp('replicated-prefill')
    manifest = payload_carrier[0]
    arch = load_arch(root / 'configs/example/ai_mesh/arch/mesh_4x4_moe.yaml')
    config = yaml.safe_load(Path(__file__).with_name('gather_streaming_compile.yaml').read_text())
    config['parallelism']['tensor_parallel'] = 4
    config['placement']['allowed_cores'] = [0, 1, 4, 5]
    effective = resolve_compile_config(load_compile_config_text(yaml.safe_dump(config), arch), arch)
    frontend = load_graph(manifest.parent.parent / 'model.pt2', FrontendRequest('forward', 'static', arch.digest().hex(),
        directory / 'frontend', source_project_root=root / 'util/mesh_ir'))
    compiled = compile_backend(frontend.variants, arch, effective, source_graphs=(frontend.graph,), workers=1).program
    variant, = compiled.semantics.variants
    bindings = ProgramBindingCatalog(compiled).for_variant(variant.entrypoint_id, variant.profile_id)
    output, = (row for row in bindings.values() if row.tensor.role is TensorRole.OUTPUT)
    publication = build_publication_program(arch, output, staging_bytes=arch.axi_data_bytes)
    adaptations = tuple(ExternalStorageAdaptation(row.object.object_id - variant.membership.objects.first_id + 1,
        MemorySpace.HBM, MemorySpace.HOST_SHARED) for row in sorted(bindings.values(), key=lambda row: row.object.object_id)
        if row.tensor.role is TensorRole.INPUT)
    composed = compose_program((VariantComposition(compiled, 1, 'prefill', 'static', adaptations),
        VariantComposition(publication, 1, 'publish', 'static')), arch)
    packed = TensorPayload.load(manifest).bind(composed, arch, entrypoint_id=1, profile_id=1)
    return composed, packed, arch


def test_replicated_host_reads_count_all_traffic_and_preserve_input_extent(replicated_prefill):
    from mesh_ir.serving_oracle import generate_phase_plan
    from mesh_ir.serving_profiles import HOST_LOAD, instance_io_intervals
    from mesh_ir.scheduled.verify import verify_program
    composed, packed, arch = replicated_prefill
    program = attach_prefill_request(composed, arch, packed.inputs, input_tokens=2, publish_chunk_bytes=arch.axi_data_bytes)
    verify_program(program, arch)
    request, = program.agent_request_profiles
    prefill, _ = program.agent_instance_profiles
    reads = instance_io_intervals(program, request, prefill)['intervals'][HOST_LOAD]
    total = sum(end - start for start, end in reads)
    assert total > len(packed.input_bytes)
    assert request.input_binding_bytes == request.full_input_dma_bytes == len(packed.input_bytes)
    assert prefill.host_input_dma_bytes_per_member == total
    assert generate_phase_plan(program, request, A.PATH_KIND.INITIAL_PREFILL)[0].host_input_dma_bytes_per_member == total


def test_replicated_prefill_native_admission(replicated_prefill, driver, tmp_path):
    from mesh_ir.abi import encode_program
    from tests.golden.test_mutation_corpus import _cpp_verdict
    composed, packed, arch = replicated_prefill
    program = attach_prefill_request(composed, arch, packed.inputs, input_tokens=2, publish_chunk_bytes=arch.axi_data_bytes)
    assert _cpp_verdict(encode_program(program), driver, tmp_path, arch) == 'ACCEPTED'


@pytest.mark.parametrize('mutation', ['underreported', 'unread_tail'])
def test_replicated_prefill_rejects_false_volume_or_coverage(replicated_prefill, mutation, driver, tmp_path):
    from mesh_ir.abi import encode_program
    from mesh_ir.diagnostics import MeshIrError
    from mesh_ir.serving_profiles import apply_request_profile_keys
    from mesh_ir.scheduled.verify import verify_program
    from tests.golden.test_mutation_corpus import _cpp_verdict
    composed, packed, arch = replicated_prefill
    program = attach_prefill_request(composed, arch, packed.inputs, input_tokens=2, publish_chunk_bytes=arch.axi_data_bytes)
    if mutation == 'underreported':
        prefill, publish = program.agent_instance_profiles
        program = replace(program, agent_instance_profiles=(replace(prefill,
            host_input_dma_bytes_per_member=len(packed.input_bytes)), publish))
    else:
        request, = program.agent_request_profiles
        program = replace(program, agent_request_profiles=(replace(request,
            input_binding_bytes=request.input_binding_bytes + 64, full_input_dma_bytes=request.full_input_dma_bytes + 64),))
    program = apply_request_profile_keys(program)
    with pytest.raises(MeshIrError, match='E_HOST_IO_SIZE_MISMATCH'):
        verify_program(program, arch)
    assert _cpp_verdict(encode_program(program), driver, tmp_path, arch) == 'VERIFY:E_HOST_IO_SIZE_MISMATCH'
