import json
import struct
import subprocess

import pytest

from mesh_ir.abi import decode_program
from mesh_ir.ir.common import TensorRole
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.runtime_reconciliation import payload_digest
from tests.integration.support.runtime_harness import GEM5, CONFIG, REPO, runtime_environment
from tests.integration.test_gather_driver_runtime import gather_source


@pytest.mark.parametrize('indices', [[7, 2, 7], [2, 7, 2]])
def test_seeded_mock_gather_reads_each_occurrence(gather_source, tmp_path, indices):
    directory, arch, payload, _ = gather_source
    program = decode_program((directory / 'program.mshb').read_bytes())
    bound = payload.bind(program, arch, entrypoint_id=1, profile_id=1)
    bindings = ProgramBindingCatalog(program).for_variant(1, 1)
    source, = [row for row in bindings.values() if row.tensor.role is TensorRole.INPUT]
    output, = [row for row in bindings.values() if row.tensor.role is TensorRole.OUTPUT]
    table, = [row for row in bindings.values() if row.tensor.role is TensorRole.WEIGHT]
    source_address = arch.regions[source.slot.reference_binding.region_id].base + source.slot.reference_binding.allocation_offset_bytes
    output_address = arch.regions[output.slot.reference_binding.region_id].base + output.slot.reference_binding.allocation_offset_bytes
    seed = dict(spans=bound.memory_image['spans'], input_address=source_address,
                indices=indices, output_address=output_address, output_bytes=output.object.footprint_bytes)
    (tmp_path / 'seed.json').write_text(json.dumps(seed))
    expected = json.loads((directory / 'expected_traffic.json').read_text())
    gather, = [row for row in program.dma_descriptors if row.kind == 6]
    for row in expected['descriptors']:
        if row['identity']['descriptor_id'] == gather.descriptor_id:
            row.update(index_values=indices, table_rows=table.tensor.shape[0], axi_data_bytes=arch.axi_data_bytes)
    (tmp_path / 'expected.json').write_text(json.dumps(expected))
    index, = [row for row in program.dma_descriptors if row.kind == 4]
    output_bytes = b''.join(struct.pack('<f', value)*512 for value in indices)
    (tmp_path / 'payload-digests.json').write_text(json.dumps({
        index.descriptor_id: payload_digest(b''.join(struct.pack('<i', value) for value in indices)),
        gather.descriptor_id: payload_digest(output_bytes)}))
    probe = tmp_path / 'probe.py'
    probe.write_text('import json, runpy, sys, m5\nfrom pathlib import Path\nfrom m5.objects import Root, MockAxiTransport\n'
        f'sys.path.insert(0, {str(CONFIG.parent)!r})\n'
        f'work = Path({str(tmp_path)!r})\n'
        'seed = json.loads((work / "seed.json").read_text())\n'
        'simulate = m5.simulate\n'
        'def seeded(*args, **kwargs):\n'
        '    transport, = (obj for obj in Root.getInstance().descendants() if isinstance(obj, MockAxiTransport))\n'
        '    for span in seed["spans"]:\n'
        '        with open(span["path"], "rb") as source:\n'
        '            source.seek(span["offset"])\n'
        '            position = 0\n'
        '            while position < span["size"]:\n'
        '                data = source.read(min(1 << 20, span["size"] - position))\n'
        '                assert data\n'
        '                transport.initializeMemoryBytes(span["address"] + position, list(data))\n'
        '                position += len(data)\n'
        '    data = b"".join(value.to_bytes(4, "little", signed=True) for value in seed["indices"])\n'
        '    transport.initializeMemoryBytes(seed["input_address"], list(data))\n'
        '    result = simulate(*args, **kwargs)\n'
        '    (work / "output.bin").write_bytes(bytes(transport.memoryBytes(seed["output_address"], seed["output_bytes"])))\n'
        '    return result\n'
        'm5.simulate = seeded\n'
        f'runpy.run_path({str(CONFIG)!r}, run_name="__main__")\n')
    env = runtime_environment()
    env['AI_MESH_ARTIFACT_DIR'] = str(tmp_path)
    run = subprocess.run([str(GEM5), f'--outdir={tmp_path / "runtime"}', str(probe),
        '--program', 'gather_streaming', '--mesh-program-dir', str(directory),
        '--expected-traffic', str(tmp_path / 'expected.json'),
        '--read-payload-digests', str(tmp_path / 'payload-digests.json'), '--watchdog-ticks', '100000000'],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=180)
    (tmp_path / 'console.log').write_text(run.stdout+run.stderr)
    assert run.returncode == 0, run.stdout+run.stderr
    assert (tmp_path / 'output.bin').read_bytes() == output_bytes
    result = json.loads((tmp_path / 'actual_result.json').read_text())
    execution, = [row for frame in result['instances'] for core in frame['cores'].values()
                  for row in core['observations']['descriptor_executions'] if row['index_reads']]
    assert [row['raw_index'] for row in execution['index_reads']] == indices
    assert [row['occurrence'] for row in execution['index_reads']] == [0, 1, 2]
    assert execution['transfer']['read_bytes'] == 3*512*4
    assert execution['status'] == 'OK'
    assert all(core['live_commands'] == 0 for core in result['cores'])
