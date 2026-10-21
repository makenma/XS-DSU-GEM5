import hashlib
import json
import struct
import subprocess
from pathlib import Path

import pytest

from mesh_ir.agent_plan_image import build_agent_plan_image, verify_workload_projection
from mesh_ir.agent_workload import PlanError, load_workload_plan
from tests.unit.test_agent_plan_image import FIXTURES, regions


@pytest.fixture
def input_plan(tmp_path):
    document = json.loads((FIXTURES / 'agent_workload_plan_two_user.json').read_text())
    round_ = document['users'][0]['tasks'][0]['rounds'][0]
    payload = bytes((i * 31 + i // 17) % 256 for i in range(round_['full_context_bytes']))
    (tmp_path / 'input.bin').write_bytes(payload)
    round_['input_payload_file'] = 'input.bin'
    round_['input_content_digest'] = hashlib.sha256(payload).hexdigest()
    path = tmp_path / 'workload.json'
    path.write_text(json.dumps(document))
    return path, payload


def test_payload_is_frozen_in_workload_and_plan(input_plan):
    path, payload = input_plan
    workload = load_workload_plan(path)
    assert workload.users[0].tasks[0].rounds[0].input_payload == payload
    (path.parent / 'input.bin').write_bytes(b'changed after load')
    image, _ = build_agent_plan_image(workload, None, 'EXPLICIT_ONLY', regions())
    assert payload in image
    verify_workload_projection(workload, image)
    position = image.index(payload)
    mutation = bytearray(image)
    mutation[position] ^= 1
    mutation[-32:] = hashlib.sha256(mutation[:-32]).digest()
    with pytest.raises(PlanError):
        verify_workload_projection(workload, bytes(mutation))


@pytest.mark.parametrize('fault', ['missing', 'length', 'digest'])
def test_payload_file_is_verified(input_plan, fault):
    path, payload = input_plan
    source = path.parent / 'input.bin'
    if fault == 'missing':
        source.unlink()
    elif fault == 'length':
        source.write_bytes(payload[:-1])
    else:
        source.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
    with pytest.raises(PlanError, match='input_payload_file'):
        load_workload_plan(path)


@pytest.fixture(scope='module')
def native_payload_reader(tmp_path_factory):
    root = Path(__file__).resolve().parents[4]
    directory = tmp_path_factory.mktemp('native-input-reader')
    source = directory / 'reader.cc'
    source.write_text('''#include <iostream>
#include "dev/ai_mesh/agent_plan_image.hh"
int main(int argc, char **argv) {
    auto image = gem5::ai_mesh::loadAgentPlanImageFile(argv[1]);
    if (!image) return 2;
    const auto &payload = image->users()[0].tasks[0].rounds[0].inputPayload;
    std::cout.write(reinterpret_cast<const char *>(payload.data()), payload.size());
}
''')
    binary = directory / 'reader'
    subprocess.run(['g++', '-std=c++17', '-I', str(root / 'src'), str(source),
        *[str(root / 'src/dev/ai_mesh' / name) for name in
          ('agent_plan_image.cc', 'agent_surrogate_codec.cc', 'agent_sha256.cc')],
        '-o', str(binary)], check=True, capture_output=True)
    return binary


@pytest.mark.parametrize('fault', ['', 'digest', 'length', 'unknown_item', 'duplicate', 'missing'])
def test_native_payload_section_admission(input_plan, native_payload_reader, fault):
    path, payload = input_plan
    workload = load_workload_plan(path)
    image, _ = build_agent_plan_image(workload, None, 'EXPLICIT_ONLY', regions())
    image = bytearray(image)
    position = image.index(payload)
    if fault == 'digest':
        image[position] ^= 1
    elif fault == 'length':
        struct.pack_into('<Q', image, position - 8, len(payload) - 1)
    elif fault == 'unknown_item':
        struct.pack_into('<I', image, position - 12, 999999)
    elif fault == 'duplicate':
        start = position - 26
        section = image[start:-32]
        image[-32:-32] = section
        struct.pack_into('<I', image, 8, struct.unpack_from('<I', image, 8)[0] + 1)
    elif fault == 'missing':
        del image[position:position + len(payload)]
    image[-32:] = hashlib.sha256(image[:-32]).digest()
    image_path = path.parent / 'image.bin'
    image_path.write_bytes(image)
    result = subprocess.run([str(native_payload_reader), str(image_path)], capture_output=True)
    assert result.returncode == (2 if fault else 0)
    if not fault:
        assert result.stdout == payload
