import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
import torch
import yaml

from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.compiler import compile_backend
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.frontend import FrontendRequest, load_graph
from mesh_ir.tensor_payload import TensorPayload
from mesh_ir.torch_compat import load_exported_program, write_tensor_payload


class PayloadCarrier(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.weight = torch.nn.Parameter(torch.arange(64, dtype=torch.float32).reshape(8, 8))
        self.register_buffer('bias', torch.ones(2, 8))

    def forward(self, data, mask, positions):
        return (data @ self.weight + mask) + positions.to(torch.float32) + self.bias


@pytest.fixture(scope='module')
def payload_carrier(tmp_path_factory, request):
    root = Path(__file__).resolve().parents[4]
    directory = tmp_path_factory.mktemp('tensor-payload')
    inputs = (torch.arange(16, dtype=torch.float32).reshape(2, 8),
              torch.ones(2, 8), torch.arange(16, dtype=torch.int32).reshape(2, 8))
    export = directory / 'model.pt2'
    torch.export.save(torch.export.export(PayloadCarrier(), inputs), export)
    manifest = write_tensor_payload(load_exported_program(export), directory / 'payload')
    architecture = yaml.safe_load((root / 'configs/example/ai_mesh/arch/mesh_1x2.yaml').read_text())
    target = next(row for row in architecture['fabric']['targets'] if row['dst_node'] == 100)
    hbm = next(row for row in target['ranges'] if row['region_id'] == 0)
    first_owner_bytes = 4096 if getattr(request, 'param', None) == 'balanced' else 256
    architecture['fabric']['targets'].append(dict(name='payload_hbm', dst_node=101, router_id=1,
        ranges=[dict(hbm, offset_bytes=first_owner_bytes, size_bytes=hbm['size_bytes'] - first_owner_bytes)]))
    hbm['size_bytes'] = first_owner_bytes
    architecture['fabric']['quotas'].extend(dict(row, dst_node=101)
        for row in list(architecture['fabric']['quotas']) if row['dst_node'] == 100)
    if getattr(request, 'param', False) is True:
        for target in architecture['fabric']['targets']:
            if target['dst_node'] == 101:
                target['synthetic_hbm'] = dict(bytes_per_cycle=32, queue_depth=8)
    arch_path = directory / 'architecture.yaml'
    arch_path.write_text(yaml.safe_dump(architecture))
    arch = load_arch(arch_path)
    config = yaml.safe_load(Path(__file__).with_name('gather_streaming_compile.yaml').read_text())
    config['placement']['allowed_cores'] = list(arch.core_ids)
    effective = resolve_compile_config(load_compile_config_text(yaml.safe_dump(config), arch), arch)
    frontend = load_graph(export, FrontendRequest('forward', 'static', arch.digest().hex(),
        directory / 'frontend', source_project_root=root / 'util/mesh_ir'))
    result = compile_backend(frontend.variants, arch, effective, source_graphs=(frontend.graph,), workers=1)
    return manifest, result.program, arch, inputs, frontend, result, effective, arch_path


def test_payload_projects_real_parameters_and_three_distinct_inputs(payload_carrier):
    manifest, program, arch, inputs, *_ = payload_carrier
    payload = TensorPayload.load(manifest)
    bound = payload.bind(program, arch, entrypoint_id=1, profile_id=1)
    assert bound.memory_image['program_semantic_sha256'] == program.semantic_sha256
    assert bound.memory_image['architecture_sha256'] == arch.digest().hex()
    assert len(bound.memory_image['spans']) == 2
    span = next(row for row in bound.memory_image['spans'] if row['size'] == 256)
    assert span['sha256'] == hashlib.sha256(PayloadCarrier().weight.detach().numpy().tobytes()).hexdigest()
    assert span['size'] == 256
    by_name = {item.name: item for item in bound.inputs}
    assert set(by_name) == {'data', 'mask', 'positions'}
    for name, source in zip(('data', 'mask', 'positions'), inputs):
        item = by_name[name]
        assert item.offset % arch.axi_data_bytes == 0
        assert bound.input_bytes[item.offset:item.offset + item.size] == source.numpy().tobytes()
        assert item.shape == tuple(source.shape)
    assert len({item.offset for item in bound.inputs}) == 3
    assert len(bound.input_bytes) == 3 * 64
    assert dict(bound.owner_bytes) == {100: 256, 101: 64}


@pytest.mark.parametrize('mutation', ['program', 'arch', 'identity', 'missing', 'duplicate', 'alignment', 'access', 'profile'])
def test_invocation_file_rejects_false_identity_or_bindings(payload_carrier, tmp_path, mutation):
    from mesh_ir.runtime_invocation import invocation_document, load_invocation

    manifest, program, arch, *_ = payload_carrier
    invocation = TensorPayload.load(manifest).bind(program, arch, entrypoint_id=1, profile_id=1).invocation
    document = invocation_document(invocation)
    if mutation in ('program', 'arch', 'identity'):
        field = dict(program='program_semantic_sha256', arch='architecture_sha256', identity='bound_identity_sha256')[mutation]
        document[field] = '0' * 64
    elif mutation == 'missing':
        document['bindings'].pop()
    elif mutation == 'duplicate':
        document['bindings'].append(document['bindings'][0])
    elif mutation == 'alignment':
        document['bindings'][0]['allocation_offset_bytes'] += 1
    elif mutation == 'access':
        document['bindings'][0]['access'] = 9
    else:
        document['profile_id'] = 999
    path = tmp_path / 'invocation.json'
    path.write_text(json.dumps(document))
    with pytest.raises(MeshIrError):
        load_invocation(path, program, arch)


def test_bound_burst_prediction_uses_the_external_destination(payload_carrier, tmp_path):
    from mesh_ir.fault_plan import execution_keys, transaction_arch
    from mesh_ir.publication import ArtifactTransaction, publish_compilation
    from mesh_ir.ir.common import MemorySpace

    _, program, arch, _, frontend, compilation, effective, _ = payload_carrier
    directory = tmp_path / 'program'
    with ArtifactTransaction(directory, ()) as transaction:
        publish_compilation(transaction, (frontend,), compilation, arch, effective)
    traffic = {row['identity']['descriptor_id']: row for row in program.semantics.intrinsic_traffic.canonical_dict()['descriptors']}
    selected = next(row for row in traffic.values() if row['dst_memory_space'] == MemorySpace.HBM)
    width = arch.axi_data_bytes
    selected['remote_address'] = arch.regions[0].base + 4096 - width // 2
    selected['execution_count'] = 1
    selected['rows'] = 1
    selected['row_bytes'] = width
    selected['remote_stride_bytes'] = width
    facts = execution_keys(directory, transaction_arch(arch), transaction_arch(arch)['src_nodes'],
        expected_rows={selected['identity']['descriptor_id']: selected})
    writes = [row for rows in facts.values() for row in rows]
    assert [row['address'] for row in writes] == [arch.regions[0].base + 4096 - width, arch.regions[0].base + 4096]
    assert [row['useful_bytes'] for row in writes] == [width // 2, width // 2]
    assert all(row['direction'] == 'write' for row in writes)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'duplicate', 'shape', 'dtype', 'content', 'admitted_content', 'kind', 'truncated', 'overlap', 'stream_hash'])
def test_payload_rejects_unbound_or_false_content(payload_carrier, tmp_path, mutation):
    manifest, program, arch, *_ = payload_carrier
    document = json.loads(manifest.read_text())
    source = manifest.parent / 'tensors.bin'
    (tmp_path / 'tensors.bin').write_bytes(source.read_bytes())
    if mutation == 'missing':
        removed = document['tensors'].pop()
        data = source.read_bytes()[:removed['offset']]
        (tmp_path / 'tensors.bin').write_bytes(data)
        document['payload_sha256'] = hashlib.sha256(data).hexdigest()
    elif mutation == 'extra':
        data = source.read_bytes()
        document['tensors'].append(dict(document['tensors'][0], name='unused', offset=len(data)))
        data += data[:document['tensors'][0]['size']]
        (tmp_path / 'tensors.bin').write_bytes(data)
        document['payload_sha256'] = hashlib.sha256(data).hexdigest()
    elif mutation == 'duplicate':
        document['tensors'][1]['name'] = document['tensors'][0]['name']
    elif mutation == 'shape':
        document['tensors'][0]['shape'] = [16, 4]
    elif mutation == 'dtype':
        document['tensors'][0]['dtype'] = 'INT32'
    elif mutation in ('content', 'admitted_content'):
        data = bytearray(source.read_bytes())
        data[17] ^= 255
        (tmp_path / 'tensors.bin').write_bytes(data)
        if mutation == 'admitted_content':
            document['tensors'][0]['sha256'] = hashlib.sha256(data[:document['tensors'][0]['size']]).hexdigest()
            document['payload_sha256'] = hashlib.sha256(data).hexdigest()
    elif mutation == 'kind':
        document['tensors'][1]['kind'] = 'PARAMETER'
    elif mutation == 'truncated':
        (tmp_path / 'tensors.bin').write_bytes(source.read_bytes()[:-1])
    elif mutation == 'overlap':
        document['tensors'][1]['offset'] = 0
    else:
        document['payload_sha256'] = '0' * 64
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(document))
    with pytest.raises(MeshIrError):
        TensorPayload.load(path).bind(program, arch, entrypoint_id=1, profile_id=1)


@pytest.mark.parametrize('layout', ['reference', 'relocated', 'balanced'])
def test_bound_tensor_payload_reaches_real_axi_reads(payload_carrier, tmp_path, layout):
    from mesh_ir.publication import ArtifactTransaction, publish_compilation
    from mesh_ir.runtime_reconciliation import payload_digest
    from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
    from mesh_ir.scheduled.model import EndpointSide
    from tests.integration.support.garnet_harness import output_of, run_garnet

    manifest, program, arch, _, frontend, compilation, effective, arch_path = payload_carrier
    payload = TensorPayload.load(manifest)
    catalog = ProgramBindingCatalog(program)
    bindings = None
    if layout == 'relocated':
        bindings = tuple(replace(row.slot.reference_binding, allocation_offset_bytes=
            4096 if row.tensor.name == 'weight' else 0 if row.tensor.name == 'bias' else
            row.slot.reference_binding.allocation_offset_bytes + 8192)
            for row in catalog.for_variant(1, 1).values())
    elif layout == 'balanced':
        from mesh_ir.binding_layout import balanced_bindings
        bindings = balanced_bindings(tuple(row.slot for row in catalog.for_variant(1, 1).values()), arch)
    bound = payload.bind(program, arch, entrypoint_id=1, profile_id=1, bindings=bindings)
    sources = {row.name: row for row in payload.records}
    image = bound.memory_image
    for item in bound.inputs:
        reference = next(row for row in bound.invocation.bindings if row.slot_id == item.slot_id)
        row = sources[item.name]
        image['spans'].append(dict(address=arch.regions[reference.region_id].base + reference.allocation_offset_bytes,
            path=str(payload.path), offset=row.offset, size=row.size, sha256=row.sha256))
    image_path = tmp_path / 'memory-image.json'
    image_path.write_text(json.dumps(image))
    output = tmp_path / 'program'
    with ArtifactTransaction(output, ()) as transaction:
        publish_compilation(transaction, (frontend,), compilation, arch, effective)
    extra_args = ('--memory-file-image', str(image_path))
    if layout != 'reference':
        from mesh_ir.runtime_invocation import invocation_document
        invocation_path = tmp_path / 'invocation.json'
        invocation_path.write_text(json.dumps(invocation_document(bound.invocation)))
        extra_args += ('--invocation', str(invocation_path))
        assert dict(bound.owner_bytes) == ({100: 64, 101: 256} if layout == 'relocated' else {100: 256, 101: 64})
    run = run_garnet(tmp_path, 'runtime', 'ring_workload', output,
        arch=arch_path,
        extra_args=extra_args)
    assert run.returncode == 0, output_of(run)[-12000:]
    actual = json.loads((output / 'actual_result.json').read_text())
    observed = {row['descriptor_id']: row for row in actual['transport']}
    covered = set()
    blob = payload.path.read_bytes()
    for descriptor in program.dma_descriptors:
        binding = catalog.descriptor_binding(descriptor.descriptor_id, EndpointSide.SRC)
        if binding is None or binding.tensor.name not in sources:
            continue
        source = sources[binding.tensor.name]
        expected = b''.join(blob[source.offset + descriptor.src.offset_bytes + index * descriptor.src_stride_bytes:
                                source.offset + descriptor.src.offset_bytes + index * descriptor.src_stride_bytes + descriptor.row_bytes]
                            for index in range(descriptor.rows))
        assert observed[descriptor.descriptor_id]['payload_digest'] == payload_digest(expected)
        covered.add(binding.tensor.name)
    assert covered == set(sources)
    assert actual['terminal'] == 'DONE'
    assert all(row['idle'] for row in actual['memory_endpoints'])
    assert all(not row['seeds'] for row in actual['memory_endpoints'])
