from dataclasses import dataclass
from enum import Enum

from mesh_ir.analysis.regions import ByteSpan, intersect_spans, merge_spans, subtract_spans, view_access_byte_spans
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as AG
from mesh_ir.ir.kernel_ir import DmaAttrs, StateOrigin
from mesh_ir.scheduled.model import ComputeExecution, EndpointSide, KernelCommandSource
from mesh_ir.serving_profiles import (
    HOST_STORE, WORKSPACE_LOAD, WORKSPACE_STORE, ServingInstanceIo,
    declared_rank_vectors, planned_instance_sequence, verify_request_phase_mode,
)
from mesh_ir.serving_tensor_bindings import ServingTensorBindingCatalog


class ByteOriginKind(Enum):
    COMPUTED = 'computed'
    EXTERNAL = 'external'


@dataclass(frozen=True)
class ByteOrigin:
    span: ByteSpan
    kind: ByteOriginKind
    identity: int
    source_begin: int


class ServingByteOrigins:
    def __init__(self, program):
        facts = program.semantics
        self.states = {row.state_id: row for row in facts.states}
        self.views = {row.view_id: row for row in facts.views}
        self.objects = {row.object_id: row for row in facts.objects}
        self.tensors = {row.tensor_id: row for row in facts.kernel_tensors}
        self.shards = {row.shard_id: row for row in facts.logical_shards}
        self.ops = {row.op_id: row for row in facts.kernel_ops}
        self.producers = {}
        for op in facts.kernel_ops:
            for write in op.writes:
                self.producers.setdefault(write.new_state_id, (op, []))[1].append(write)
        self.computed = {row.source.kernel_op_id for row in facts.command_semantics
                         if isinstance(row.source, KernelCommandSource) and isinstance(row.execution, ComputeExecution)}
        self.endpoints = {(row.descriptor_id, row.side): row.use for row in facts.endpoint_uses}
        descriptors = {row.descriptor_id: row for row in program.dma_descriptors}
        self.descriptors = {row.kernel_op_id: tuple(descriptors[index] for index in row.descriptor_ids)
                            for row in facts.descriptor_groups}

    def access(self, descriptor, side):
        use = self.endpoints[(descriptor.descriptor_id, side)]
        op = self.ops[use.kernel_op_id]
        return (op.reads if side is EndpointSide.SRC else op.writes)[use.access_index]

    def trace(self, state_id, span):
        pending = [(state_id, span, span.begin)]
        result = []
        while pending:
            identity, required, output_begin = pending.pop()
            state = self.states[identity]
            producer = self.producers.get(identity)
            if producer is None:
                if state.origin is not StateOrigin.EXTERNAL:
                    raise MeshIrError('E_BINDING_ROLE', 'published bytes have no initialized origin')
                result.append(ByteOrigin(ByteSpan(output_begin, output_begin + required.end - required.begin),
                    ByteOriginKind.EXTERNAL, state.object_id, required.begin))
                continue
            op, writes = producer
            written = merge_spans(tuple(span for write in writes for span in
                view_access_byte_spans(write, self.views, self.objects, self.tensors, self.shards)))
            for piece in subtract_spans((required,), written):
                pending.append((writes[0].old_state_id, piece, output_begin + piece.begin - required.begin))
            pieces = intersect_spans((required,), written)
            if not pieces:
                continue
            if op.op_id in self.computed and op.computation_id:
                result.extend(ByteOrigin(ByteSpan(output_begin + piece.begin - required.begin,
                    output_begin + piece.end - required.begin), ByteOriginKind.COMPUTED, op.computation_id, piece.begin)
                    for piece in pieces)
                continue
            if not isinstance(op.attrs, DmaAttrs) or op.attrs.kind not in (
                    A.DMA_KIND.LOAD, A.DMA_KIND.STORE, A.DMA_KIND.PREFETCH, A.DMA_KIND.P2P_PUSH):
                raise MeshIrError('E_BINDING_ROLE', 'published bytes are neither computed nor byte-preserving DMA', op_id=op.op_id)
            covered = []
            for descriptor in self.descriptors.get(op.op_id, ()):
                if self.access(descriptor, EndpointSide.DST).new_state_id != identity:
                    continue
                source = self.access(descriptor, EndpointSide.SRC)
                for row in range(descriptor.rows):
                    begin = descriptor.dst.offset_bytes + row * descriptor.dst_stride_bytes
                    for piece in intersect_spans(pieces, (ByteSpan(begin, begin + descriptor.row_bytes),)):
                        source_begin = descriptor.src.offset_bytes + row * descriptor.src_stride_bytes + piece.begin - begin
                        pending.append((source.state_id, ByteSpan(source_begin, source_begin + piece.end - piece.begin),
                            output_begin + piece.begin - required.begin))
                        covered.append(piece)
            if subtract_spans(pieces, tuple(covered)):
                raise MeshIrError('E_BINDING_ROLE', 'published bytes escape their DMA source projection', op_id=op.op_id)
        return tuple(sorted(result, key=lambda row: row.span))


def verify_workspace_publication(program, request, catalog=None):
    if verify_request_phase_mode(program, request):
        raise MeshIrError('E_REQUEST_PROFILE', 'computed workspace publication requires a prefill-only request')
    catalog = catalog if catalog is not None else ServingTensorBindingCatalog(program)
    origins = ServingByteOrigins(program)
    for ranks in declared_rank_vectors(program, request, A.PATH_KIND.INITIAL_PREFILL):
        prefill, publish = planned_instance_sequence(program, request, A.PATH_KIND.INITIAL_PREFILL, ranks)
        saved = ServingInstanceIo(program, request, prefill, catalog)
        restored = ServingInstanceIo(program, request, publish, catalog)
        saved.attribution()
        restored.attribution()
        if any(saved.intervals[(ordinal, WORKSPACE_LOAD)] or restored.intervals[(ordinal, WORKSPACE_STORE)]
               for ordinal in range(len(ranks))):
            raise MeshIrError('E_BINDING_ROLE', 'workspace must be produced by PREFILL and consumed by PUBLISH')
        producers = {}
        for descriptor in saved.descriptors:
            windows = saved.matches.get((descriptor.descriptor_id, WORKSPACE_STORE), ())
            for window in windows:
                key = window.tensor.mapping.member_ordinal, window.tensor.binding.object.object_id
                state_id = origins.access(descriptor, EndpointSide.DST).new_state_id
                previous = producers.get(key)
                if previous is None or origins.states[state_id].version > origins.states[previous[1]].version:
                    producers[key] = window.tensor, state_id
        reader_objects = {row.binding.object.object_id: row for row in catalog.for_instance(publish.instance_profile_id)
                          if row.mapping.binding_kind == AG.BINDING_KIND.REQUEST_WORKSPACE}
        for descriptor in restored.descriptors:
            for role in (WORKSPACE_LOAD, HOST_STORE):
                windows = restored.matches.get((descriptor.descriptor_id, role), ())
                for window in windows:
                    member = window.tensor.mapping.member_ordinal
                    access = origins.access(descriptor, EndpointSide.SRC)
                    for row in range(descriptor.rows):
                        begin = descriptor.src.offset_bytes + row * descriptor.src_stride_bytes
                        for origin in origins.trace(access.state_id, ByteSpan(begin, begin + descriptor.row_bytes)):
                            reader = reader_objects.get(origin.identity) if origin.kind is ByteOriginKind.EXTERNAL else None
                            if reader is None or reader.mapping.member_ordinal != member:
                                raise MeshIrError('E_BINDING_ROLE', 'PUBLISH bytes must preserve the request workspace origin')
                            wanted = ByteSpan(reader.mapping.offset_bytes + origin.source_begin,
                                reader.mapping.offset_bytes + origin.source_begin + origin.span.end - origin.span.begin)
                            covered = []
                            for (ordinal, _), (writer, state_id) in producers.items():
                                if ordinal != member or writer.mapping.request_symbol_id != reader.mapping.request_symbol_id:
                                    continue
                                pieces = intersect_spans((wanted,), (ByteSpan(writer.mapping.offset_bytes,
                                    writer.mapping.offset_bytes + writer.binding.object.footprint_bytes),))
                                if not pieces:
                                    continue
                                left, right = writer.binding.tensor, reader.binding.tensor
                                if (left.dtype, left.shape, left.strides) != (right.dtype, right.shape, right.strides):
                                    raise MeshIrError('E_BINDING_ROLE', 'workspace producer and consumer have different tensor layouts')
                                for piece in pieces:
                                    source = ByteSpan(piece.begin - writer.mapping.offset_bytes, piece.end - writer.mapping.offset_bytes)
                                    if any(value.kind is not ByteOriginKind.COMPUTED for value in origins.trace(state_id, source)):
                                        raise MeshIrError('E_BINDING_ROLE', 'workspace bytes lack a preceding computation')
                                covered.extend(pieces)
                            if subtract_spans((wanted,), tuple(covered)):
                                raise MeshIrError('E_BINDING_ROLE', 'PUBLISH reads an unwritten request workspace range')
