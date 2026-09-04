"""Hand-written program builder for golden .mshb programs.

This module is the authoring surface for programs in this phase: no
torch.export lowering exists yet.  The builder owns the traffic oracle --
expected_traffic rows are derived from the reference burst splitter, and
the runtime must match them exactly.
"""

from __future__ import annotations

import yaml

from mesh_ir.burst_splitter import checked_mul, plan_descriptor
from mesh_ir.generated import abi as A
from mesh_ir.model import (
    Allocation,
    ArchManifest,
    ArchRegion,
    Command,
    CommandOperand,
    CommandWait,
    DmaDescriptor,
    DmaEndpoint,
    Entrypoint,
    Event,
    ExpectedTrafficRow,
    MeshIrError,
    OpAttr,
    Profile,
    Program,
    Relocation,
    Shard,
    Stream,
    StringEntry,
    Tensor,
)


def _validate_arch(manifest: ArchManifest) -> None:
    from mesh_ir.model import MeshIrError

    def _require(condition, message):
        if not condition:
            raise MeshIrError("E_CAPABILITY_MISMATCH", message)

    _require(manifest.clock_hz > 0, "clock_hz must be positive")
    _require(len(manifest.core_ids) > 0, "core_ids must be non-empty")
    _require(manifest.mesh_rows * manifest.mesh_cols == len(manifest.core_ids),
             "mesh rows*cols must equal the core count")
    _require(len(set(manifest.core_ids)) == len(manifest.core_ids),
             "core_ids must be unique")
    _require(manifest.sram_bytes > 0 and manifest.sram_banks > 0,
             "SRAM capacity/banks must be positive")
    _require(manifest.sram_base_alignment_bytes > 0 and
             manifest.sram_base_alignment_bytes &
             (manifest.sram_base_alignment_bytes - 1) == 0,
             "SRAM alignment must be a power of two")
    _require(manifest.sram_read_ports_per_bank > 0 and
             manifest.sram_write_ports_per_bank > 0,
             "SRAM read/write ports must be positive")
    _require(manifest.sram_bank_queue_depth > 0,
             "SRAM bank queue depth must be positive")
    for queue in (manifest.tensor_queue_depth, manifest.vector_queue_depth,
                  manifest.reduce_queue_depth,
                  manifest.dma_descriptor_queue_depth,
                  manifest.dma_segment_queue_depth,
                  manifest.dma_read_outstanding,
                  manifest.dma_write_outstanding,
                  manifest.admit_window, manifest.decode_width,
                  manifest.event_visibility_cycles):
        _require(queue > 0, "queue/outstanding/window parameters must be positive")
    _require(manifest.axi_data_bytes > 0 and
             manifest.axi_data_bytes &
             (manifest.axi_data_bytes - 1) == 0,
             "AXI data width must be a power of two")
    _require(manifest.axi_data_bytes <= 64,
             "AXI data width above the runtime contract maximum (64 B)")
    _require(0 < manifest.axi_id_bits <= 16, "AXI id_bits out of range")
    _require(manifest.axi_max_burst_beats > 0,
             "AXI max burst beats must be positive")
    _require(manifest.axi_max_burst_beats <= 256,
             "AXI max burst beats above the runtime contract maximum (256)")
    _require(sorted(manifest.core_ids) == manifest.core_ids or
             sorted(set(manifest.core_ids)) == sorted(manifest.core_ids),
             "core ids must be dense")
    for region in manifest.regions:
        _require(region.base <= (1 << 64) - region.bytes,
                 f"region {region.name} base+bytes exceeds the u64 range")
        if region.kind == "CORE_SRAM_APERTURE":
            _require(region.tile_stride > 0 and
                     region.tile_stride & (region.tile_stride - 1) == 0,
                     f"region {region.name} tile stride must be a power of "
                     "two")
            _require(region.tile_stride >= region.tile_bytes,
                     f"region {region.name} tile stride smaller than the "
                     "aperture")
    for throughput in (manifest.tensor_macs_per_cycle,
                       manifest.vector_elements_per_cycle,
                       manifest.reduce_ops_per_cycle):
        _require(throughput, "dtype throughput tables must be non-empty")
        for dtype, value in throughput.items():
            _require(value > 0, f"throughput for {dtype} must be positive")
    for name in ("tensor", "vector", "reduce"):
        table = {
            "tensor": manifest.tensor_macs_per_cycle,
            "vector": manifest.vector_elements_per_cycle,
            "reduce": manifest.reduce_ops_per_cycle,
        }[name]
        _require(set(table) <= set(("fp32", "fp16", "bf16", "int8", "int32")),
                 f"{name} throughput table has unknown dtypes")
    ordered = sorted(manifest.regions, key=lambda r: (r.base, r.base + r.bytes))
    for first, second in zip(ordered, ordered[1:]):
        overlap = first.base < second.base + second.bytes and \
            second.base < first.base + first.bytes
        _require(not overlap, f"regions {first.name}/{second.name} overlap")


def load_arch(path) -> ArchManifest:
    with open(path, "rb") as handle:
        raw = yaml.safe_load(handle)
    mesh = raw["mesh"]
    core = raw["core"]
    axi = raw["axi"]
    sram = core["sram"]
    core_count = len(mesh["core_ids"])

    def _int(value, default=0):
        if value is None:
            return default
        return int(value, 0) if isinstance(value, str) else int(value)

    regions = tuple(
        ArchRegion(
            name=region["name"],
            kind=region["kind"],
            base=_int(region["base"]),
            bytes=_int(region.get("bytes"), _int(region.get("tile_stride")) * core_count),
            tile_stride=_int(region.get("tile_stride")),
            tile_bytes=_int(region.get("tile_bytes")),
        )
        for region in raw["memory_regions"]
    )
    manifest = ArchManifest(
        schema_version=raw["schema_version"],
        arch_name=raw["arch_name"],
        clock_hz=raw["clock_hz"],
        core_ids=tuple(mesh["core_ids"]),
        mesh_rows=mesh["rows"],
        mesh_cols=mesh["cols"],
        mesh_routing=mesh["routing"],
        mesh_endpoint_order=mesh["endpoint_order"],
        command_rom_entries=core["command_rom_entries"],
        event_visibility_cycles=core["event_visibility_cycles"],
        decode_width=core["decode_width"],
        admit_window=core["admit_window"],
        sram_bytes=sram["bytes"],
        sram_banks=sram["banks"],
        sram_read_ports_per_bank=sram["read_ports_per_bank"],
        sram_bank_queue_depth=sram["bank_queue_depth"],
        sram_write_ports_per_bank=sram["write_ports_per_bank"],
        sram_read_bytes_per_cycle_per_bank=sram["read_bytes_per_cycle_per_bank"],
        sram_write_bytes_per_cycle_per_bank=sram["write_bytes_per_cycle_per_bank"],
        sram_base_alignment_bytes=sram["base_alignment_bytes"],
        tensor_queue_depth=core["tensor_engine"]["queue_depth"],
        tensor_setup_cycles=core["tensor_engine"]["setup_cycles"],
        tensor_pipeline_flush_cycles=core["tensor_engine"]["pipeline_flush_cycles"],
        tensor_macs_per_cycle=dict(core["tensor_engine"]["macs_per_cycle"]),
        vector_queue_depth=core["vector_engine"]["queue_depth"],
        vector_elements_per_cycle=dict(core["vector_engine"]["elements_per_cycle"]),
        reduce_queue_depth=core["reduce_engine"]["queue_depth"],
        reduce_setup_cycles=core["reduce_engine"]["setup_cycles"],
        reduce_flush_cycles=core["reduce_engine"]["flush_cycles"],
        reduce_ops_per_cycle=dict(core["reduce_engine"]["ops_per_cycle"]),
        dma_read_engines=core["dma"]["read_engines"],
        dma_write_engines=core["dma"]["write_engines"],
        dma_descriptor_queue_depth=core["dma"]["descriptor_queue_depth"],
        dma_segment_queue_depth=core["dma"]["segment_queue_depth"],
        dma_read_outstanding=core["dma"]["read_outstanding"],
        dma_write_outstanding=core["dma"]["write_outstanding"],
        dma_setup_cycles=core["dma"]["setup_cycles"],
        dma_burst_base_latency=core["dma"]["burst_base_latency"],
        axi_data_bytes=axi["data_bytes"],
        axi_max_burst_beats=axi["max_burst_beats"],
        axi_address_bits=axi["address_bits"],
        axi_id_bits=axi["id_bits"],
        axi_max_outstanding_per_id=axi["max_outstanding_per_id"],
        axi_enforce_4k_boundary=axi["enforce_4k_boundary"],
        axi_qos_default=axi["qos_default"],
        regions=regions,
    )
    _validate_arch(manifest)
    return manifest


class ProgramBuilder:
    """Accumulates sections, assigns indices, derives the traffic oracle."""

    def __init__(self, arch: ArchManifest, name: str):
        self.arch = arch
        self.name = name
        self.strings = []
        self._string_ids = {}
        self.entrypoints = []
        self.profiles = []
        self.tensors = []
        self.shards = []
        self.allocations = []
        self.streams = []
        self.commands = []
        self.command_waits = []
        self.command_operands = []
        self.events = []
        self.dma_descriptors = []
        self.op_attrs = []
        self.relocations = []
        self.expected_traffic = []

    def string(self, value: str) -> int:
        if value in self._string_ids:
            return self._string_ids[value]
        self.strings.append(StringEntry(value))
        sid = len(self.strings)
        self._string_ids[value] = sid
        return sid

    def entrypoint(self, name: str, profile_name: str, lifecycle_core: int, lifecycle_stream: int) -> int:
        profile_begin = len(self.profiles)
        entrypoint_id = len(self.entrypoints) + 1
        self.profiles.append(
            Profile(
                profile_id=len(self.profiles) + 1,
                entrypoint_id=entrypoint_id,
                name_sid=self.string(profile_name),
                rank=0,
            )
        )
        self.entrypoints.append(
            Entrypoint(
                entrypoint_id=entrypoint_id,
                name_sid=self.string(name),
                profile_begin=profile_begin,
                profile_count=1,
                lifecycle_core_id=lifecycle_core,
                lifecycle_stream_id=lifecycle_stream,
            )
        )
        return entrypoint_id

    def tensor(self, name, role, dtype, storage_class, access, dims, placement_id=0, sharding_id=0) -> int:
        rank = len(dims)
        assert 1 <= rank <= 8
        padded = tuple(dims) + (0,) * (8 - rank)
        self.tensors.append(
            Tensor(
                tensor_id=len(self.tensors) + 1,
                name_sid=self.string(name),
                role=role,
                dtype=dtype,
                storage_class=storage_class,
                access=access,
                rank=rank,
                layout=A.LAYOUT_KIND.CONTIGUOUS_ROW_MAJOR,
                placement_id=placement_id,
                sharding_id=sharding_id,
                dims=padded,
            )
        )
        return len(self.tensors)

    def shard(self, tensor_id, owner_core, allocation_id, local_shape, span_bytes, sharding_id=0, offset=0) -> int:
        rank = len(local_shape)
        self.shards.append(
            Shard(
                shard_id=len(self.shards) + 1,
                tensor_id=tensor_id,
                sharding_id=sharding_id,
                owner_core=owner_core,
                rank=rank,
                global_origin=(0,) * 8,
                local_shape=tuple(local_shape) + (0,) * (8 - rank),
                valid_shape=tuple(local_shape) + (0,) * (8 - rank),
                allocation_id=allocation_id,
                allocation_offset=offset,
                span_bytes=span_bytes,
            )
        )
        return len(self.shards)

    def allocation(self, owner_core, offset, size, alignment, persistent=False) -> int:
        self.allocations.append(
            Allocation(
                allocation_id=len(self.allocations) + 1,
                owner_core=owner_core,
                memory_space=A.MEMORY_SPACE.CORE_SRAM,
                offset_bytes=offset,
                size_bytes=size,
                alignment_bytes=alignment,
                flags=1 if persistent else 0,
            )
        )
        return len(self.allocations)

    def stream(self, core_id, stream_id, flags=0) -> "StreamScope":
        scope = StreamScope(self, core_id, stream_id, flags)
        self.streams.append(scope.record)
        return scope

    def event(self, kind=A.EVENT_KIND.NORMAL, expected_arrivals=1) -> int:
        event_id = len(self.events) + 1
        self.events.append(
            Event(
                event_id=event_id,
                kind=kind,
                producer_command_id=0,
                expected_arrivals=expected_arrivals,
            )
        )
        return event_id

    def relocation(self, symbol, kind, region_id, tensor_id, offset) -> int:
        self.relocations.append(
            Relocation(
                relocation_id=len(self.relocations) + 1,
                symbol_sid=self.string(symbol),
                kind=kind,
                region_id=region_id,
                tensor_id=tensor_id,
                reserved=0,
                offset_bytes=offset,
                reserved2=0,
            )
        )
        return len(self.relocations)

    def _attr(self, kind, payload_values, payload_fields) -> int:
        self.op_attrs.append(
            OpAttr(kind=kind, reserved=0, payload=tuple(payload_values), payload_fields=tuple(payload_fields))
        )
        return len(self.op_attrs)

    def repeat_attr(self, begin_ordinal, command_count, repeat_count) -> int:
        return self._attr(
            A.ATTR_KIND.REPEAT_V1,
            (begin_ordinal, command_count, repeat_count, 0),
            ("subrange_begin_stream_ordinal", "subrange_command_count", "repeat_count", "flags"),
        )

    def fence_attr(self, scope) -> int:
        return self._attr(
            A.ATTR_KIND.FENCE_V1,
            (scope, 0, 0, 0, 0),
            ("fence_scope", "reserved", "reserved0", "reserved1", "reserved2"),
        )

    def recv_wait_attr(self, transfer_id) -> int:
        return self._attr(
            A.ATTR_KIND.RECV_WAIT_V1,
            (transfer_id, 0, 0, 0),
            ("transfer_id", "reserved0", "reserved1", "reserved2"),
        )

    def gemm_attr(self, batch, m, n, k, dtype, accum_dtype,
                  efficiency_q16=65536) -> int:
        return self._attr(
            A.ATTR_KIND.GEMM_V1,
            (batch, m, n, k, 0, 0, dtype, accum_dtype, 0, efficiency_q16),
            ("batch", "m", "n", "k", "a_transpose", "b_transpose", "dtype", "accum_dtype", "epilogue", "efficiency_q16"),
        )

    def reduce_attr(self, element_count, dtype, accum_dtype, op=0,
                    fan_in=2) -> int:
        return self._attr(
            A.ATTR_KIND.REDUCE_V1,
            (element_count, dtype, accum_dtype, op, fan_in),
            ("element_count", "dtype", "accum_dtype", "op", "fan_in"),
        )

    def elementwise_attr(self, element_count, dtype, op=0,
                         ops_per_element=1) -> int:
        return self._attr(
            A.ATTR_KIND.ELEMENTWISE_V1,
            (element_count, dtype, op, ops_per_element, 0),
            ("element_count", "dtype", "op", "ops_per_element", "reserved"),
        )

    def softmax_attr(self, axis_size, dtype) -> int:
        return self._attr(
            A.ATTR_KIND.SOFTMAX_V1,
            (axis_size, dtype, A.VECTOR_ALGORITHM.STANDARD, 0),
            ("axis_size", "dtype", "algorithm", "reserved"),
        )

    def norm_attr(self, element_count, dtype) -> int:
        return self._attr(
            A.ATTR_KIND.NORM_V1,
            (element_count, dtype, A.VECTOR_ALGORITHM.STANDARD, 0),
            ("element_count", "dtype", "algorithm", "reserved"),
        )

    def fill_attr(self, pattern) -> int:
        return self._attr(
            A.ATTR_KIND.FILL_V1,
            (pattern, 0),
            ("pattern", "reserved"),
        )

    def dma(
        self,
        command: Command,
        kind,
        src,
        dst,
        rows,
        row_bytes,
        src_stride,
        dst_stride,
        completion_event,
        transfer_id=0,
        physical=None,
    ) -> int:
        useful = checked_mul(rows, row_bytes)
        descriptor = DmaDescriptor(
            descriptor_id=len(self.dma_descriptors) + 1,
            command_id=command.command_id,
            transfer_id=transfer_id,
            owner_core=command.core_id,
            kind=kind,
            src=src,
            dst=dst,
            rows=rows,
            row_bytes=row_bytes,
            src_stride_bytes=src_stride,
            dst_stride_bytes=dst_stride,
            useful_bytes=useful,
            physical_storage_bytes=useful if physical is None else physical,
            axi_id=command.command_id & 0xFFFF,
            qos=0,
            reserved=0,
            max_burst_beats=self.arch.axi_max_burst_beats,
            reserved2=0,
            completion_event=completion_event,
        )
        self.dma_descriptors.append(descriptor)
        return descriptor.descriptor_id

    def oracle(self, entrypoint_id, profile_id, descriptor_id, command_id, kind, base, rows, row_bytes, stride) -> None:
        if kind == A.DMA_KIND.LOCAL_FILL:
            bursts, beat_bytes, segments = 0, 0, 0
        else:
            plan = plan_descriptor(
                row_bytes,
                rows,
                base,
                stride,
                self.arch.axi_data_bytes,
                self.arch.axi_max_burst_beats,
            )
            bursts, beat_bytes, segments = len(plan.bursts), plan.beat_bytes, plan.segments
        is_write = kind in (A.DMA_KIND.STORE, A.DMA_KIND.P2P_PUSH)
        beats = beat_bytes // self.arch.axi_data_bytes
        self.expected_traffic.append(
            ExpectedTrafficRow(
                entrypoint_id=entrypoint_id,
                profile_id=profile_id,
                command_id=command_id,
                descriptor_id=descriptor_id,
                kind=kind,
                reserved=0,
                useful_bytes=checked_mul(rows, row_bytes),
                physical_beat_bytes=beat_bytes,
                segments=segments,
                bursts=bursts,
                ar_count=0 if is_write else bursts,
                r_beats=0 if is_write else beats,
                aw_count=bursts if is_write else 0,
                w_beats=beats if is_write else 0,
                b_count=bursts if is_write else 0,
                min_flits=0,
                reserved2=0,
            )
        )

    def build(self) -> Program:
        for event in self.events:
            event.producer_command_id = 0
        for command in self.commands:
            if command.signal_event:
                event = next(e for e in self.events if e.event_id == command.signal_event)
                if event.kind == A.EVENT_KIND.NORMAL:
                    event.producer_command_id = command.command_id
            if command.opcode in (
                A.OPCODE.DMA_LOAD,
                A.OPCODE.DMA_STORE,
                A.OPCODE.DMA_P2P_PUSH,
                A.OPCODE.DMA_PREFETCH,
                A.OPCODE.DMA_FILL,
            ):
                for descriptor in self.dma_descriptors:
                    if descriptor.command_id == command.command_id:
                        event = next(e for e in self.events if e.event_id == descriptor.completion_event)
                        event.producer_command_id = command.command_id
        return Program(
            abi_major=A.ABI_MAJOR,
            abi_minor=A.ABI_MINOR,
            arch_digest=self.arch.digest(),
            strings=self.strings,
            entrypoints=self.entrypoints,
            profiles=self.profiles,
            tensors=self.tensors,
            shards=self.shards,
            allocations=self.allocations,
            streams=self.streams,
            commands=self.commands,
            command_waits=self.command_waits,
            command_operands=self.command_operands,
            events=self.events,
            dma_descriptors=self.dma_descriptors,
            op_attrs=self.op_attrs,
            relocations=self.relocations,
            expected_traffic=self.expected_traffic,
        )


class StreamScope:
    """Append-only command cursor for one (core, stream)."""

    def __init__(self, builder: ProgramBuilder, core_id: int, stream_id: int, flags: int):
        self.builder = builder
        self.record = Stream(
            core_id=core_id,
            stream_id=stream_id,
            command_begin=0,
            command_count=0,
            flags=flags,
            reserved=0,
        )
        self._ordinal = 0

    def command(self, opcode, waits=(), operands=(), signal_event=0, attr_index=0, source_op_id=0) -> Command:
        wait_begin = len(self.builder.command_waits)
        for event_id in waits:
            self.builder.command_waits.append(CommandWait(event_id=event_id))
        operand_begin = len(self.builder.command_operands)
        for tensor_id, shard_id, allocation_id, access in operands:
            self.builder.command_operands.append(
                CommandOperand(
                    tensor_id=tensor_id,
                    shard_id=shard_id,
                    allocation_id=allocation_id,
                    access=access,
                    reserved=0,
                )
            )
        command = Command(
            command_id=len(self.builder.commands) + 1,
            source_op_id=source_op_id,
            core_id=self.record.core_id,
            stream_id=self.record.stream_id,
            engine=A.OPCODE_ENGINE[opcode],
            opcode=opcode,
            wait_begin=wait_begin,
            wait_count=len(waits),
            operand_begin=operand_begin,
            operand_count=len(operands),
            signal_event=signal_event,
            attr_index=attr_index,
            debug_loc_id=0,
        )
        self.builder.commands.append(command)
        if self.record.command_count == 0:
            self.record.command_begin = command.command_id - 1
        self.record.command_count += 1
        self._ordinal += 1
        return command

    @property
    def ordinal(self) -> int:
        return self._ordinal
