from __future__ import annotations

import time
from dataclasses import dataclass

from mesh_ir.analysis.sram import plan_memory_sram
from mesh_ir.architecture import ArchManifest, validate_arch
from mesh_ir.canonical import canonical_json_bytes
from mesh_ir.compile_config import EffectiveCompileConfig, validate_effective_compile_config
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.model import Program
from mesh_ir.passes.addresses import bind_addresses_and_relocations_stage
from mesh_ir.passes.execution import ExecutionDiagnostics, PassExecutor, PassRecord, record_pass, verify_pass_chain
from mesh_ir.passes.expected_traffic import compute_expected_traffic_stage
from mesh_ir.passes.graph_to_kernel import KernelLoweringResult
from mesh_ir.passes.hazards import insert_hazard_dependencies_stage
from mesh_ir.passes.schedule import schedule_per_core_streams_stage
from mesh_ir.passes.segments import lower_dma_to_segments_stage
from mesh_ir.passes.static_sram import _AuthoredVariantSource, _StaticSramState, plan_static_sram_stage, verify_static_sram_state
from mesh_ir.scheduled.model import AuthoredProgramOrigin
from mesh_ir.scheduled.verify import verify_compiled_pretraffic_state, verify_pretraffic_state, verify_program_kernel_correspondence


_PASS_NAMES = (
    "PlanStaticSRAM",
    "InsertHazardDependencies",
    "SchedulePerCoreStreams",
    "LowerDmaToSegments",
    "BindAddressesAndRelocations",
    "VerifyScheduledIR",
    "ComputeExpectedTraffic",
)


@dataclass(frozen=True)
class ScheduledLoweringResult:
    program: Program
    passes: tuple[PassRecord, ...]
    execution: ExecutionDiagnostics


def lower_authored_to_program(static: _StaticSramState, arch: ArchManifest) -> Program:
    if type(static) is not _StaticSramState or type(static.origin) is not AuthoredProgramOrigin:
        raise MeshIrError("E_ABI_BOUNDS", "authored lowering requires an exact authored static SRAM state")
    validate_arch(arch)
    verify_static_sram_state(static)
    if any(type(item.source) is not _AuthoredVariantSource for item in static.variants):
        raise MeshIrError("E_ABI_BOUNDS", "authored lowering requires authored variant sources")
    for ordinal, item in enumerate(static.variants):
        if any(stream.core_id not in arch.core_ids for stream in item.source.streams):
            raise MeshIrError("E_STREAM_CONTRACT", "authored stream owner is not an architecture core", variant_ordinal=ordinal)
        expected = plan_memory_sram(item.source.records, arch, allocations=item.plan.allocations)
        if canonical_json_bytes(item.plan) != canonical_json_bytes(expected):
            raise MeshIrError("E_ABI_CHECKSUM", "authored SRAM plan differs from its verified pinned allocation plan", variant_ordinal=ordinal)
    hazards = insert_hazard_dependencies_stage(static)
    schedule = schedule_per_core_streams_stage(hazards, arch)
    segments = lower_dma_to_segments_stage(schedule)
    pretraffic = bind_addresses_and_relocations_stage(segments, arch)
    verified = verify_pretraffic_state(pretraffic, arch)
    return compute_expected_traffic_stage(verified, arch)


def lower_to_program(lowering: KernelLoweringResult, arch: ArchManifest, effective: EffectiveCompileConfig, executor: PassExecutor) -> ScheduledLoweringResult:
    if type(lowering) is not KernelLoweringResult or type(executor) is not PassExecutor:
        raise MeshIrError("E_CONFIG", "Scheduled lowering inputs have invalid record types")
    validate_arch(arch)
    validate_effective_compile_config(effective, arch)
    lowering.bundle.verify()
    selected = tuple((entrypoint, profile.profile_id) for entrypoint in effective.config.entrypoints for profile in effective.config.profiles_for(entrypoint))
    actual = tuple((module.entrypoint, module.profile_id) for module in lowering.bundle.modules)
    if actual != selected:
        raise MeshIrError("E_CONFIG", "Kernel bundle variants differ from the effective compile selection", expected=selected, actual=actual)
    if type(lowering.passes) is not tuple or not lowering.passes or any(type(item) is not PassRecord for item in lowering.passes):
        raise MeshIrError("E_CONFIG", "Kernel lowering pass chain must be a nonempty tuple")
    verify_pass_chain(lowering.passes, lowering.passes[0].input_hash, lowering.bundle.semantic_sha256, ("PlaceOpsAndTensors", "ShardAndPadTensors", "TileKernels", "BufferizeAndAlias", "LowerCollectives", "InsertDataMovement"))
    records = []
    current = lowering.bundle.semantic_sha256
    started = time.perf_counter_ns()
    static = plan_static_sram_stage(lowering.bundle, arch)
    output = static.semantic_sha256
    records.append(record_pass(_PASS_NAMES[0], current, output, time.perf_counter_ns() - started, (("variants", len(static.variants)),)))
    current = output
    started = time.perf_counter_ns()
    hazards = insert_hazard_dependencies_stage(static)
    output = hazards.semantic_sha256
    records.append(record_pass(_PASS_NAMES[1], current, output, time.perf_counter_ns() - started, (("dependencies", len(hazards.edges)),)))
    current = output
    started = time.perf_counter_ns()
    schedule = schedule_per_core_streams_stage(hazards, arch)
    output = schedule.semantic_sha256
    records.append(record_pass(_PASS_NAMES[2], current, output, time.perf_counter_ns() - started, (("commands", sum(len(item.commands) for item in schedule.variants)), ("streams", sum(len(item.streams) for item in schedule.variants)))))
    current = output
    started = time.perf_counter_ns()
    segments = lower_dma_to_segments_stage(schedule)
    output = segments.semantic_sha256
    records.append(record_pass(_PASS_NAMES[3], current, output, time.perf_counter_ns() - started, (("segments", len(segments.segments)),)))
    current = output
    started = time.perf_counter_ns()
    pretraffic = bind_addresses_and_relocations_stage(segments, arch)
    output = pretraffic.semantic_sha256
    records.append(record_pass(_PASS_NAMES[4], current, output, time.perf_counter_ns() - started, (("descriptors", len(pretraffic.transport.dma_descriptors)),)))
    current = output
    started = time.perf_counter_ns()
    verified = verify_compiled_pretraffic_state(pretraffic, arch, lowering, effective)
    records.append(record_pass(_PASS_NAMES[5], current, current, time.perf_counter_ns() - started))
    started = time.perf_counter_ns()
    program = compute_expected_traffic_stage(verified, arch)
    records.append(record_pass(_PASS_NAMES[6], current, program.semantic_sha256, time.perf_counter_ns() - started, (("traffic_descriptors", len(program.semantics.intrinsic_traffic.descriptors)),)))
    passes = tuple(records)
    verify_pass_chain(passes, lowering.bundle.semantic_sha256, program.semantic_sha256, _PASS_NAMES)
    verify_program_kernel_correspondence(program, lowering.bundle)
    return ScheduledLoweringResult(program, passes, executor.diagnostics)


__all__ = ["ScheduledLoweringResult", "lower_authored_to_program", "lower_to_program"]
