import json
from pathlib import Path

import pytest
import yaml

from mesh_ir.architecture import ControlInitiatorEndpoint, ControlInitiatorRole, architecture_document, load_arch, load_arch_text
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.fault_plan import src_nodes_from_arch

ROOT = Path(__file__).resolve().parents[4]
ARCH = ROOT / 'configs/example/ai_mesh/arch/mesh_1x2.yaml'


def control_document():
    raw = yaml.safe_load(ARCH.read_text())
    raw['fabric']['initiators'].append(dict(name='host', role='HOST_DRIVER',
        src_node=2000, src_port=0, router_id=0, default_target_node=100))
    raw['fabric']['quotas'].extend(dict(row, src_node=2000) for row in
                                  list(raw['fabric']['quotas']) if row['src_node'] == 0)
    return raw


def test_control_endpoint_is_not_a_compute_participant():
    arch = load_arch_text(yaml.safe_dump(control_document()))
    assert arch.core_ids == (0, 1)
    assert tuple(item.core_id for item in arch.fabric.core_initiators) == arch.core_ids
    control, = arch.fabric.control_initiators
    assert isinstance(control, ControlInitiatorEndpoint)
    assert control.role is ControlInitiatorRole.HOST_DRIVER
    assert not hasattr(control, 'core_id')
    assert src_nodes_from_arch(arch) == {0: 0, 1: 1}
    assert arch.fabric.core_initiators[0].router_id == control.router_id
    assert load_arch_text(json.dumps(architecture_document(arch))).digest() == arch.digest()
    assert load_arch_text(yaml.safe_dump(architecture_document(arch))).digest() == arch.digest()


@pytest.mark.parametrize('damage', ['fake_core', 'duplicate_source', 'missing_quota', 'unknown_role', 'replace_core'])
def test_invalid_control_identity_is_rejected(damage):
    raw = control_document()
    host = raw['fabric']['initiators'][-1]
    if damage == 'fake_core':
        host['core_id'] = 65534
    elif damage == 'duplicate_source':
        host['src_node'] = 0
    elif damage == 'missing_quota':
        raw['fabric']['quotas'] = [row for row in raw['fabric']['quotas'] if row['src_node'] != 2000]
    elif damage == 'unknown_role':
        host['role'] = 'CORE'
    else:
        raw['fabric']['initiators'].pop(0)
    with pytest.raises(MeshIrError):
        load_arch_text(yaml.safe_dump(raw))


def test_core_only_architecture_keeps_its_existing_identity():
    assert load_arch(ARCH).digest().hex() == \
        'be63f5362c5890ba64460511b6aae70cee265f6587376330599dfdd272ac1058'


def test_control_endpoint_is_excluded_from_compiled_gather_participants(tmp_path):
    from mesh_ir.abi.decoder import decode_program
    from mesh_ir.compile_service import export_and_compile_factory
    from tests.integration.test_gate2_gather_streaming import CONFIG, INPUTS

    raw = control_document()
    raw['fabric']['initiators'].insert(0, raw['fabric']['initiators'].pop())
    arch_file = tmp_path / 'arch.yaml'
    arch_file.write_text(yaml.safe_dump(raw))
    inputs = tmp_path / 'inputs.json'
    inputs.write_text(json.dumps(INPUTS))
    output = tmp_path / 'program'
    export_and_compile_factory('examples.gather_carrier:create_model', inputs, None,
                               arch_file, CONFIG, output)
    program = decode_program((output / 'program.mshb').read_bytes())
    assert {command.core_id for command in program.commands} == {0}
    assert {shard.owner_core for shard in program.shards} <= {0, 0xffff}
    traffic = json.loads((output / 'expected_traffic.json').read_text())
    assert {row['identity']['issuing_core'] for row in traffic['descriptors']} == {0}
    assert all(channel['source_node'] != 2000 and channel['destination_node'] != 2000
               for row in traffic['descriptors'] for channel in row['channels'])
