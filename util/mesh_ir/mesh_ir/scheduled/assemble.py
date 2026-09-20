from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from mesh_ir.architecture import ArchManifest
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.model import Allocation, Command, CommandOperand, CommandWait, DmaDescriptor, Entrypoint, Event, ExpectedTrafficRow, OpAttr, Profile, Program, Relocation, Shard, Stream, StringEntry, Tensor
from mesh_ir.scheduled.model import ProgramSemantics, _ProgramFacts
from mesh_ir.traffic import AxiChannel, BindingSlot, DescriptorExecution, TrafficReport, calculate_traffic


@dataclass(frozen=True)
class _TransportSections:
    strings: tuple[StringEntry, ...]
    entrypoints: tuple[Entrypoint, ...]
    profiles: tuple[Profile, ...]
    tensors: tuple[Tensor, ...]
    shards: tuple[Shard, ...]
    allocations: tuple[Allocation, ...]
    streams: tuple[Stream, ...]
    commands: tuple[Command, ...]
    command_waits: tuple[CommandWait, ...]
    command_operands: tuple[CommandOperand, ...]
    events: tuple[Event, ...]
    dma_descriptors: tuple[DmaDescriptor, ...]
    op_attrs: tuple[OpAttr, ...]
    relocations: tuple[Relocation, ...]


@dataclass(frozen=True)
class _PreTrafficSemantics(_ProgramFacts):
    reference_binding_identity_sha256: str
    executions: tuple[DescriptorExecution, ...]


@dataclass(frozen=True)
class _PreTrafficState:
    abi_major: int
    abi_minor: int
    arch_digest: bytes
    transport: _TransportSections
    semantics: _PreTrafficSemantics

    def semantic_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)

    @property
    def semantic_sha256(self) -> str:
        return semantic_sha256(self.semantic_dict())


class _VerifiedPreTrafficState:
    __slots__ = ("state",)

    def __init__(self, state: _PreTrafficState):
        self.state = state


def _expected_traffic(report: TrafficReport, executions: tuple[DescriptorExecution, ...], arch: ArchManifest) -> tuple[ExpectedTrafficRow, ...]:
    kinds = {item.descriptor.identity.descriptor_id: item.descriptor.kind for item in executions}
    rows = []
    for item in report.descriptors:
        channels = {channel.channel: channel for channel in item.channels}
        rows.append(ExpectedTrafficRow(
            entrypoint_id=item.identity.entrypoint_id,
            profile_id=item.identity.profile_id,
            command_id=item.identity.command_id,
            descriptor_id=item.identity.descriptor_id,
            kind=int(kinds[item.identity.descriptor_id]),
            reserved=0,
            useful_bytes=item.useful_bytes,
            physical_beat_bytes=item.physical_beat_bytes,
            segments=item.segments,
            bursts=item.bursts,
            ar_count=channels.get(AxiChannel.AR).messages if AxiChannel.AR in channels else 0,
            r_beats=item.physical_beat_bytes // arch.axi_data_bytes if AxiChannel.R in channels else 0,
            aw_count=channels.get(AxiChannel.AW).messages if AxiChannel.AW in channels else 0,
            w_beats=item.physical_beat_bytes // arch.axi_data_bytes if AxiChannel.W in channels else 0,
            b_count=channels.get(AxiChannel.B).messages if AxiChannel.B in channels else 0,
            min_flits=item.flits,
            reserved2=0,
        ))
    return tuple(rows)


def assemble_program(verified: _VerifiedPreTrafficState, arch: ArchManifest) -> Program:
    if type(verified) is not _VerifiedPreTrafficState:
        raise MeshIrError("E_ABI_BOUNDS", "assembler requires verified pre-traffic state")
    state = verified.state
    report = calculate_traffic(arch, state.semantics.reference_binding_identity_sha256, state.semantics.executions)
    semantics = ProgramSemantics(
        state.semantics.origin,
        state.semantics.variants,
        state.semantics.kernel_tensors,
        state.semantics.computations,
        state.semantics.placements,
        state.semantics.logical_shards,
        state.semantics.partial_sums,
        state.semantics.objects,
        state.semantics.views,
        state.semantics.states,
        state.semantics.tokens,
        state.semantics.kernel_ops,
        state.semantics.object_backings,
        state.semantics.resident_views,
        state.semantics.command_semantics,
        state.semantics.barrier_groups,
        state.semantics.dependencies,
        state.semantics.streams,
        state.semantics.stream_command_ids,
        state.semantics.descriptor_groups,
        state.semantics.endpoint_uses,
        state.semantics.binding_slots,
        state.semantics.reference_binding_identity_sha256,
        report,
    )
    sections = state.transport
    provisional = Program(
        state.abi_major,
        state.abi_minor,
        state.arch_digest,
        sections.strings,
        sections.entrypoints,
        sections.profiles,
        sections.tensors,
        sections.shards,
        sections.allocations,
        sections.streams,
        sections.commands,
        sections.command_waits,
        sections.command_operands,
        sections.events,
        sections.dma_descriptors,
        sections.op_attrs,
        sections.relocations,
        _expected_traffic(report, state.semantics.executions, arch),
        semantics,
        "",
    )
    program = dataclasses.replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))
    from mesh_ir.scheduled.verify import verify_program

    verify_program(program, arch)
    return program


__all__ = ["assemble_program"]
