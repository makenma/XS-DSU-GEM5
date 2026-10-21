import pytest

from mesh_ir.agent_diagnostic import terminal_diagnostic
from mesh_ir.generated import agent_abi as A
from mesh_ir.model import MeshIrError


GOLDEN = {'E_REQUEST_CANCELLED': 'a8812ed39d651c07e644bb96d0cd62f71eb55955b9ae3f65ea2db8aeb9c72806', 'E_KV_REUSE_REQUIRED': '0b6b8ef31dfde54dec210fc5a964be174b415adbc16c26074c144676981c933b', 'E_KV_TOKEN_MISMATCH': '61177de90bb64047306f90a1fa16e3a4655160091391de3e9c8f7158d57b9677', 'E_KV_STATE': 'b45289370ebe8e9736e68cd7d1a54c19d4d36fa3d5fd385ab7cd115e92f5d976'}


@pytest.mark.parametrize('detail', GOLDEN)
def test_terminal_diagnostic_golden(detail):
    payload = terminal_diagnostic(detail, 9, 4, 5, 3)
    assert len(payload) == 64
    assert payload[24:56].hex() == GOLDEN[detail]
    assert payload[-8:] == bytes(8)
    assert payload != terminal_diagnostic(detail, 10, 4, 5, 3)


def test_projection_registry_is_closed():
    runtime = {name for name, row in A.DETAIL_DISPOSITION_V1.items()
               if row[0] == 'REQUEST_RECOVERABLE'
               and (name.startswith(('E_DCORE_', 'E_MOE_')) or name == 'E_AXI_RESPONSE')}
    assert set(GOLDEN) | runtime == set(A.DIAGNOSTIC_PROJECTION_V1)
    with pytest.raises(MeshIrError, match='no diagnostic projection'):
        terminal_diagnostic('E_REQUEST_BINDING', 9)


def test_full_typed_error_source_diagnostic_golden():
    import struct
    from mesh_ir.agent_diagnostic import error_source_diagnostic
    source = struct.pack('<HHBBHIIII16s', 2, 256, 0, 2, 0, 0, 0, 257, 3, bytes(16))
    payload = error_source_diagnostic('E_AXI_RESPONSE', source)
    assert payload.hex() == '010002000300000100000000000000000101000000000000aa8ab98b7749beebcc10227cd6cb3ebb408e61fd4d364500f719c5d2a06d9a340000000000000000'
    with pytest.raises(MeshIrError):
        error_source_diagnostic('E_AXI_RESPONSE', source[:-1])


def test_cancellation_binds_the_frozen_instance_and_phase():
    payload = terminal_diagnostic('E_REQUEST_CANCELLED', 9, program_instance_id=4, phase_ordinal=3)
    assert payload[24:56].hex() == 'b47e9ee613e3cb3b9d6c6fbb3c0cea8a2e3934d6129415efec515663564a4fe0'
    assert payload[8:12] == bytes((3, 0, 0, 0))
    assert payload != terminal_diagnostic('E_REQUEST_CANCELLED', 9)


@pytest.mark.parametrize('detail', [name for name, row in A.DETAIL_DISPOSITION_V1.items()
    if row[0] == 'REQUEST_RECOVERABLE' and name.startswith(('E_DCORE_', 'E_MOE_'))])
def test_every_recoverable_runtime_detail_projects_the_exact_source(detail):
    import struct
    from mesh_ir.agent_diagnostic import error_source_diagnostic
    source = struct.pack('<HHBBHIIII16s', 4, 2, 1, 0, 0, 8, 9, 10, 0, bytes(16))
    payload = error_source_diagnostic(detail, source)
    assert int.from_bytes(payload[:4], 'little') == A.DETAIL_CODE[detail]
    assert len(payload) == 64


@pytest.mark.parametrize('source', [
    (4, 2, 0, 2, 0, 0, 0, 1, 0, bytes(16)),
    (5, 2, 1, 0, 0, 7, 3, 1, 0, bytes(16)),
    (2, 2, 0, 2, 0, 7, 0, 1, 0, bytes(16)),
    (6, 2, 3, 7, 0, 7, 0, 0, 0, bytes(16)),
    (6, 65535, 3, 7, 0, 7, 0, 6, 0, bytes(16)),
    (7, 65535, 5, 9, 0, 0, 0, 4, 0, bytes(16)),
    (0, 2, 2, 5, 0, 0, 0, 0, 0, bytes(16)),
    (2, 2, 4, 2, 0, 0, 0, 0, 1, bytes(16)),
])
def test_error_source_must_match_one_closed_constructor(source):
    import struct
    from mesh_ir.agent_diagnostic import error_source_diagnostic
    with pytest.raises(MeshIrError):
        error_source_diagnostic('E_DCORE_ENGINE', struct.pack('<HHBBHIIII16s', *source))


def test_cache_tag_index_zero_is_a_valid_full_key_source():
    import struct
    from mesh_ir.agent_diagnostic import error_source_diagnostic
    fill = struct.pack('<HHIII', 0, 1, 0, 0, 1)
    source = struct.pack('<HHBBHIIII16s', 2, 0, 2, 5, 0, 0, 0, 0, 0, fill)
    assert len(error_source_diagnostic('E_AXI_RESPONSE', source)) == 64
