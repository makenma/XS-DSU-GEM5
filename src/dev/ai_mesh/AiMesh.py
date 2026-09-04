from m5.objects.ClockedObject import ClockedObject
from m5.params import *
from m5.SimObject import SimObject


class DmaEngineBase(ClockedObject):
    type = "DmaEngineBase"
    abstract = True
    cxx_header = "dev/ai_mesh/dma_types.hh"
    cxx_class = "gem5::ai_mesh::DmaEngineBase"


class MeshProgramLoader(SimObject):
    type = "MeshProgramLoader"
    cxx_header = "dev/ai_mesh/mesh_program_loader.hh"
    cxx_class = "gem5::ai_mesh::MeshProgramLoader"

    program_file = Param.String("", "Path to the scheduled .mshb binary")
    cores = VectorParam.MeshDummyCore([], "Participating dummy cores")
    transport = Param.MockAxiTransport(
        NULL, "Mock AXI transport (mock runtime only)"
    )
    apertures = VectorParam.PeerSramAperture(
        [], "Per-core SRAM apertures (real Garnet runtime)"
    )
    arch_digest = Param.String("", "Canonical SHA-256 of the runtime architecture manifest")
    effective_arch_digest = Param.String("", "SHA-256 of the effective architecture (overrides included)")
    core_ids = VectorParam.UInt32([], "Architecture core id list")
    sram_bytes = Param.UInt64(0, "Per-core SRAM capacity in bytes")
    sram_banks = Param.UInt32(0, "SRAM bank count")
    sram_alignment = Param.UInt32(0, "SRAM base alignment in bytes")
    axi_data_bytes = Param.UInt32(0, "AXI data bus width in bytes")
    axi_max_burst_beats = Param.UInt32(0, "Maximum AXI burst beats")
    region_ids = VectorParam.UInt32([], "Memory region index per region")
    region_bases = VectorParam.UInt64([], "Physical base address per region")
    region_bytes = VectorParam.UInt64([], "Size in bytes per region")
    region_tile_strides = VectorParam.UInt64([], "Per-core tile stride for SRAM apertures")
    region_tile_bytes = VectorParam.UInt64([], "Per-core tile size for SRAM apertures")
    region_kinds = VectorParam.UInt32([], "Region kind: 0=HBM, 1=HOST_SHARED, 2=SRAM_APERTURE")


class MockAxiTransport(ClockedObject):
    type = "MockAxiTransport"
    cxx_header = "dev/ai_mesh/mock_axi_transport.hh"
    cxx_class = "gem5::ai_mesh::MockAxiTransport"

    data_bus_bytes = Param.UInt32(32, "AXI data bus width in bytes")
    burst_base_latency = Param.Cycles(2, "Fixed latency per accepted burst")
    sram_region_base = Param.UInt64(0, "Physical base of the SRAM aperture region")
    sram_tile_stride = Param.UInt64(0, "Per-core SRAM tile stride")


class TensorDmaEngine(DmaEngineBase):
    type = "TensorDmaEngine"
    cxx_header = "dev/ai_mesh/tensor_dma_engine.hh"
    cxx_class = "gem5::ai_mesh::TensorDmaEngine"

    core_id = Param.UInt16(0, "Owning core id")
    setup_cycles = Param.Cycles(2, "Descriptor setup latency")
    descriptor_queue_depth = Param.UInt32(16, "Finite descriptor queue depth")
    max_outstanding = Param.UInt32(16, "Outstanding transfer limit")
    transport = Param.MockAxiTransport("Functional mock AXI transport")


class AxiGarnetBridge(ClockedObject):
    type = "AxiGarnetBridge"
    cxx_header = "dev/ai_mesh/axi_garnet_bridge.hh"
    cxx_class = "gem5::ai_mesh::AxiGarnetBridge"

    adapter = Param.AxiInitiatorAdapter(
        NULL, "Initiator adapter injecting into the NPU Garnet"
    )
    data_bus_bytes = Param.UInt32(32, "AXI data bus width in bytes")
    aw_queue_depth = Param.UInt32(8, "Finite pending write-burst queue depth")
    ar_queue_depth = Param.UInt32(8, "Finite pending read-burst queue depth")
    axi_id_count = Param.UInt32(8, "Round-robin AXI ID pool size")
    axi_id_base = Param.UInt16(0, "First AXI ID of the pool")


class AxiTensorDmaEngine(DmaEngineBase):
    type = "AxiTensorDmaEngine"
    cxx_header = "dev/ai_mesh/axi_tensor_dma_engine.hh"
    cxx_class = "gem5::ai_mesh::AxiTensorDmaEngine"

    bridge = Param.AxiGarnetBridge("Owning core's AXI Garnet bridge")
    data_bus_bytes = Param.UInt32(32, "AXI data bus width in bytes")
    max_burst_beats = Param.UInt32(16, "Maximum AXI burst beats")
    setup_cycles = Param.Cycles(2, "Descriptor setup latency")
    descriptor_queue_depth = Param.UInt32(16, "Finite descriptor queue depth per direction")
    max_outstanding_bursts = Param.UInt32(8, "Outstanding burst limit per direction")
    axi_id_count = Param.UInt32(8, "Round-robin AXI ID pool size")


class PeerSramAperture(ClockedObject):
    type = "PeerSramAperture"
    cxx_header = "dev/ai_mesh/peer_sram_aperture.hh"
    cxx_class = "gem5::ai_mesh::PeerSramAperture"

    adapter = Param.AxiTargetAdapter("SRAM tile target adapter on the NPU Garnet")
    core_id = Param.UInt16(0, "Owning core id")
    sram_base = Param.UInt64(0, "Absolute base address of this SRAM tile")
    sram_bytes = Param.UInt64(0, "SRAM tile capacity in bytes")


class NpuMemoryEndpoint(ClockedObject):
    type = "NpuMemoryEndpoint"
    cxx_header = "dev/ai_mesh/npu_memory_endpoint.hh"
    cxx_class = "gem5::ai_mesh::NpuMemoryEndpoint"

    adapter = Param.AxiTargetAdapter("Memory target adapter on the NPU Garnet")
    seed_json = Param.String("", "Pre-simulation seed ranges file")
    verify_json = Param.String("", "Drain-time verify ranges file")


class MeshDummyCore(ClockedObject):
    type = "MeshDummyCore"
    cxx_header = "dev/ai_mesh/mesh_dummy_core.hh"
    cxx_class = "gem5::ai_mesh::MeshDummyCore"

    core_id = Param.UInt16(0, "Architecture core id")
    decode_width = Param.UInt32(1, "Commands decoded per cycle per stream")
    event_visibility_cycles = Param.Cycles(1, "Signal-to-consumer visibility delay")
    reference_compute = Param.Bool(False, "Must stay false: numeric compute is forbidden")
    tensor_queue_depth = Param.UInt32(8, "Tensor engine queue depth")
    tensor_setup_cycles = Param.Cycles(8, "GEMM setup latency")
    tensor_flush_cycles = Param.Cycles(4, "GEMM pipeline flush latency")
    tensor_macs_per_cycle = Param.UInt32(256, "Analytic MAC throughput per cycle")
    tensor_macs_by_dtype = VectorParam.UInt32([], "MACs/cycle indexed by dtype [FP32,FP16,BF16,INT8,INT32]")
    vector_queue_depth = Param.UInt32(8, "Vector engine queue depth")
    vector_elements_per_cycle = Param.UInt32(128, "Vector throughput per cycle")
    vector_elements_by_dtype = VectorParam.UInt32([], "Elements/cycle indexed by dtype [FP32,FP16,BF16,INT8,INT32]")
    reduce_queue_depth = Param.UInt32(8, "Reduce engine queue depth")
    reduce_setup_cycles = Param.Cycles(4, "Reduce setup latency")
    reduce_flush_cycles = Param.Cycles(2, "Reduce flush latency")
    reduce_ops_per_cycle = Param.UInt32(64, "Reduce throughput per cycle")
    reduce_ops_by_dtype = VectorParam.UInt32([], "Ops/cycle indexed by dtype [FP32,FP16,BF16,INT8,INT32]")
    admit_window = Param.UInt32(8, "Finite per-stream in-flight command window")
    sram_banks = Param.UInt32(16, "SRAM bank count")
    sram_bytes = Param.UInt64(0, "SRAM capacity in bytes")
    sram_alignment = Param.UInt32(64, "SRAM base alignment")
    sram_line_bytes = Param.UInt32(32, "SRAM bank line size in bytes")
    sram_read_ports = Param.UInt32(1, "Read ports per bank")
    sram_bank_queue_depth = Param.UInt32(1, "Per-bank request queue depth")
    sram_write_ports = Param.UInt32(1, "Write ports per bank")
    sram_read_bytes_per_cycle = Param.UInt32(32, "Read bytes per cycle per bank")
    sram_write_bytes_per_cycle = Param.UInt32(32, "Write bytes per cycle per bank")
    dma = Param.DmaEngineBase("Owned DMA engine")


class MeshDispatcher(ClockedObject):
    type = "MeshDispatcher"
    cxx_header = "dev/ai_mesh/mesh_dispatcher.hh"
    cxx_class = "gem5::ai_mesh::MeshDispatcher"

    loader = Param.MeshProgramLoader("Program loader")
    cores = VectorParam.MeshDummyCore([], "Dispatched cores")
    transport = Param.MockAxiTransport(
        NULL, "Mock transport owning the byte accounting (mock runtime)"
    )
    apertures = VectorParam.PeerSramAperture(
        [], "Per-core SRAM apertures (real Garnet runtime)"
    )
    endpoint = Param.NpuMemoryEndpoint(NULL, "HBM memory endpoint")
    network = Param.RubyNetwork(NULL, "NPU Garnet network for quiescence")
    result_json = Param.String("", "Machine-readable result artifact path")
    instances = Param.UInt32(1, "Program instances dispatched back to back")
    watchdog_ticks = Param.UInt64(0, "Progress watchdog bound, 0 disables")
