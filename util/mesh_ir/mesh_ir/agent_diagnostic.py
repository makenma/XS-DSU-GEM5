from __future__ import annotations

import hashlib
import struct

from mesh_ir.generated import agent_abi as A
from mesh_ir.generated import abi as M
from mesh_ir.model import MeshIrError


def terminal_diagnostic(detail, request_id, session_id=0, handle=0, generation=0,
                        *, program_instance_id=0, phase_ordinal=0):
    projection = A.DIAGNOSTIC_PROJECTION_V1.get(detail)
    if projection is None:
        raise MeshIrError('E_AGENT_PROTOCOL_FATAL', 'terminal detail has no diagnostic projection')
    source_kind = getattr(A.DIAGNOSTIC_SOURCE_KIND_V1, projection['source_kind'])
    component = getattr(A.DIAGNOSTIC_COMPONENT, projection['component'])
    site = projection['site']
    if source_kind == A.DIAGNOSTIC_SOURCE_KIND_V1.REQUEST_TERMINAL:
        source = struct.pack('<QQII', request_id, program_instance_id, phase_ordinal, site)
        group = phase_ordinal
    elif source_kind == A.DIAGNOSTIC_SOURCE_KIND_V1.KV_POLICY:
        source = struct.pack('<QQQII', request_id, session_id, handle, generation, site)
        group = generation
    else:
        raise MeshIrError('E_AGENT_PROTOCOL_FATAL', 'error source key is required for this diagnostic')
    code = A.DETAIL_CODE[detail]
    digest = hashlib.sha256(b'AGENT_DIAGNOSTIC_V1\0' +
        struct.pack('<II', source_kind, len(source)) + source + struct.pack('<I', code)).digest()
    return struct.pack('<IHHIIII32sQ', code, component, 0xffff, group, 0, site, source_kind, digest, 0)


def error_source_diagnostic(detail, source_wire):
    projection = A.DIAGNOSTIC_PROJECTION_V1.get(detail)
    if projection is None or projection['source_kind'] != 'ERROR_SOURCE_KEY' or len(source_wire) != 40:
        raise MeshIrError('E_AGENT_PROTOCOL_FATAL', 'error diagnostic requires an exact typed source')
    error_class, core, domain, kind, reserved, group, region, ordinal, generation, aux = struct.unpack(
        '<HHBBHIIII16s', source_wire)
    C, D, K = M.MOE_ERROR_CLASS, M.MESH_OBJECT_DOMAIN, M.ERROR_SOURCE_OBJECT_KIND
    aux_zero = aux == bytes(16)
    valid = False
    if domain in (D.STATIC, D.MOE_OVERLAY):
        kinds = {C.DMA_AXI: (K.DESCRIPTOR,), C.SRAM: (K.DESCRIPTOR, K.COMMAND, K.ALLOCATION),
                 C.COMMAND: (K.COMMAND,), C.EVENT: (K.EVENT,)}
        valid = (core != 65535 and ordinal != 0 and aux_zero and
                 (domain != D.STATIC or (group == region == 0)) and kind in kinds.get(error_class, ()))
    elif domain == D.WEIGHT_FILL:
        fill_core, partition, tag, cache_generation, incarnation = struct.unpack('<HHIII', aux)
        valid = (error_class in (C.WEIGHT_FILL, C.DMA_AXI, C.SRAM) and kind == K.WEIGHT_FILL_OBLIGATION
                 and group == region == ordinal == generation == 0 and core != 65535
                 and fill_core == core and incarnation != 0)
    elif domain == D.KV_RUNTIME:
        request, instance = struct.unpack('<QQ', aux)
        valid = (request != 0 and instance != 0 and (
            error_class in (C.DMA_AXI, C.SRAM) and kind == K.DESCRIPTOR and ordinal != 0 and core != 65535 or
            error_class == C.INTERNAL and kind == K.KV_APPEND and ordinal == 1 and core == 65535))
    elif domain == D.COORDINATOR and core == 65535 and generation == 0:
        valid = (error_class == C.MATERIALIZE and kind == K.MATERIALIZER_CHECK and aux_zero
                 and ordinal <= M.MATERIALIZER_CHECK_SITE.CACHE_BINDING or
                 error_class == C.CACHE_RESERVATION and kind == K.CACHE_RESERVATION and region == 0
                 and ordinal != 0 and aux[12:] == bytes(4))
    elif domain == D.INTERNAL:
        valid = (error_class == C.INTERNAL and kind == K.INTERNAL_CHECK and core == 65535
                 and group == region == generation == 0 and aux_zero and ordinal <= M.INTERNAL_CHECK_SITE.LEDGER)
    if not valid or reserved:
        raise MeshIrError('E_AGENT_PROTOCOL_FATAL', 'invalid error source fields')
    components = A.DIAGNOSTIC_SOURCE_COMPONENTS_V1
    component = components['domain_overrides'].get(domain, components['error_classes'][error_class])
    code = A.DETAIL_CODE[detail]
    digest = hashlib.sha256(b'AGENT_DIAGNOSTIC_V1\0' +
        struct.pack('<II', A.DIAGNOSTIC_SOURCE_KIND_V1.ERROR_SOURCE_KEY, len(source_wire)) +
        source_wire + struct.pack('<I', code)).digest()
    return struct.pack('<IHHIIII32sQ', code, getattr(A.DIAGNOSTIC_COMPONENT, component),
        core, group, region, ordinal, A.DIAGNOSTIC_SOURCE_KIND_V1.ERROR_SOURCE_KEY, digest, 0)
