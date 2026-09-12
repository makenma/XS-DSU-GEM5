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
    w_beats_per_cycle = Param.UInt32(
        0, "Local W acceptance per cycle; zero retains legacy burst admission"
    )


class AxiTensorDmaEngine(DmaEngineBase):
    type = "AxiTensorDmaEngine"
    cxx_header = "dev/ai_mesh/axi_tensor_dma_engine.hh"
    cxx_class = "gem5::ai_mesh::AxiTensorDmaEngine"

    bridge = Param.AxiGarnetBridge("Owning core's AXI Garnet bridge")
    data_bus_bytes = Param.UInt32(32, "AXI data bus width in bytes")
    max_burst_beats = Param.UInt32(16, "Maximum AXI burst beats")
    setup_cycles = Param.Cycles(2, "Descriptor setup latency")
    descriptor_queue_depth = Param.UInt32(16, "Finite descriptor queue depth per direction")
    segment_queue_depth = Param.UInt32(8,
        "Per-direction segment/read-return queue capacity (burst issued to "
        "SRAM commit); not the AXI outstanding window")
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


class Gate3ObservationRecorder(SimObject):
    type = "Gate3ObservationRecorder"
    cxx_header = "dev/ai_mesh/gate3_observation_recorder.hh"
    cxx_class = "gem5::ai_mesh::Gate3ObservationRecorder"

    output_path = Param.String("", "Raw Gate3 observation facts path")


class AgentAxiDriver(ClockedObject):
    type = "AgentAxiDriver"
    cxx_header = "dev/ai_mesh/agent_axi_driver.hh"
    cxx_class = "gem5::ai_mesh::AgentAxiDriver"

    master = Param.AxiInitiatorAdapter("Driver AXI initiator")
    target = Param.AxiTargetAdapter("Driver host-memory target")
    recorder = Param.Gate3ObservationRecorder("Gate3 observation recorder")
    data_bus_bytes = Param.UInt32(64, "AXI data bus width in bytes")
    sq_depth = Param.UInt32(2, "Submission queue depth")
    cq_depth = Param.UInt32(2, "Completion queue depth")
    control_bytes = Param.UInt32(8, "Control window transfer bytes")
    max_burst_beats = Param.UInt16(256, "Maximum AXI burst beats")
    host_base = Param.UInt64(0x10000000, "Host memory base")
    sq_ring_base = Param.UInt64(0x10001000, "Submission queue ring base")
    cq_ring_base = Param.UInt64(0x10050000, "Completion queue ring base")
    msi_base = Param.UInt64(0x10060000, "MSI register bank base")
    npu_control_base = Param.UInt64(0x30000000, "NPU control base")
    agent_proxy_control_base = Param.UInt64(0x30100000, "Agent proxy control base")
    profile = Param.String("NORMAL", "Gate3 runtime profile")
    request_count = Param.UInt32(1, "Logical requests to execute")
    local_visibility_delay = Param.Cycles(0, "Local visibility delay")
    request_id_capacity = Param.UInt32(8, "Finite request ID capacity")
    drain_cycles = Param.Cycles(8, "Drain interval before exit")
    doorbell_axi_id = Param.UInt32(16, "Fixed SQ doorbell AXI ID")
    ack_axi_id = Param.UInt32(17, "Fixed CQ ACK AXI ID")
    request_source = Param.String(
        "protocol_profile", "protocol_profile or replay_plan"
    )
    plan_image = Param.String("", "agent plan image path")
    host_compute_tokens = Param.UInt32(24, "Synthetic host compute tokens")
    host_available_fraction_q16 = Param.UInt32(
        65536, "Q16 fraction of host tokens available to the plan"
    )
    host_compile_slots = Param.UInt32(4, "Compile service slots")
    host_test_slots = Param.UInt32(6, "Test service slots")
    cancel_join_entries = Param.UInt32(4,
                     "bounded concurrent cancel join records")
    host_log_parse_slots = Param.UInt32(4, "Log-parse service slots")
    host_service_queue_depth = Param.UInt32(
        64, "Per-kind host service queue depth"
    )
    host_weight_compile = Param.UInt32(4, "SWRR weight for compile")
    host_weight_test = Param.UInt32(3, "SWRR weight for test")
    host_weight_log_parse = Param.UInt32(2, "SWRR weight for log parse")
    host_aging_threshold_ns = Param.UInt64(
        0, "Host queue aging threshold in ns, 0 disables aging"
    )
    host_local_io_enabled = Param.Bool(True, "Analytic host local-I/O model")
    host_local_io_fixed_ns = Param.UInt64(
        1000, "Fixed host local-I/O latency in ns"
    )
    host_local_io_bytes_per_ns = Param.UInt32(
        64, "Host local-I/O analytic bandwidth in bytes per ns"
    )
    agent_object_table_entries = Param.UInt32(
        4096, "Bounded agent object table entries"
    )
    stop_after_completed_tasks = Param.UInt32(
        0, "Stop admitting tasks after this many lifecycle finals, 0 disables"
    )
    stop_accepting_enabled = Param.Bool(
        False, "Whether the stop-accepting tick cutoff is active"
    )
    stop_accepting_at_tick = Param.UInt64(
        0, "Suppress think arrivals from this tick when enabled"
    )
    host_fault_site = Param.String(
        "", "Host local fault site: empty|object_produce|object_read"
    )
    host_fault_task = Param.UInt32(
        0, "Task sequence the host local fault applies to"
    )
    host_fault_round = Param.UInt32(
        0, "Repair round the host local fault applies to"
    )
    control_doorbell_error_ordinal = Param.UInt32(
        0, "Fail the control doorbell B of this control ordinal, 0 disables"
    )
    mutate_cancel_command_ordinal = Param.UInt32(
        0, "Rewrite the cancel command CQ status of this ordinal, 0 disables"
    )
    mutate_cancel_command_status = Param.UInt32(
        0, "Forged CQ status word for the cancel command mutation"
    )
    control_doorbell_b_hold_ns = Param.UInt64(
        0, "Hold the control doorbell B response for this many ns"
    )
    cq_read_delay_ns = Param.UInt64(
        0, "Delay before each driver CQ local read in ns"
    )
    metadata_read_delay_ns = Param.UInt64(
        0, "Delay before each driver metadata local read in ns"
    )


class NpuServingFrontend(ClockedObject):
    type = "NpuServingFrontend"
    cxx_header = "dev/ai_mesh/npu_serving_frontend.hh"
    cxx_class = "gem5::ai_mesh::NpuServingFrontend"

    master = Param.AxiInitiatorAdapter("NPU serving AXI initiator")
    control_target = Param.AxiTargetAdapter("NPU control target")
    recorder = Param.Gate3ObservationRecorder("Gate3 observation recorder")
    driver = Param.AgentAxiDriver(
        NULL, "Owning plan-mode driver for control trigger notifications"
    )
    data_bus_bytes = Param.UInt32(64, "AXI data bus width in bytes")
    sq_depth = Param.UInt32(2, "Submission queue depth")
    cq_depth = Param.UInt32(2, "Completion queue depth")
    control_bytes = Param.UInt32(8, "Control window transfer bytes")
    max_burst_beats = Param.UInt16(256, "Maximum AXI burst beats")
    host_base = Param.UInt64(0x10000000, "Host memory base")
    sq_ring_base = Param.UInt64(0x10001000, "Submission queue ring base")
    cq_ring_base = Param.UInt64(0x10050000, "Completion queue ring base")
    msi_base = Param.UInt64(0x10060000, "MSI register bank base")
    npu_control_base = Param.UInt64(0x30000000, "NPU control base")
    agent_proxy_control_base = Param.UInt64(0x30100000, "Agent proxy control base")
    profile = Param.String("NORMAL", "Gate3 runtime profile")
    request_count = Param.UInt32(1, "Logical requests to execute")
    sq_read_issue_delay = Param.Cycles(0, "SQ read issue delay")
    doorbell_axi_id = Param.UInt32(16, "Fixed SQ doorbell AXI ID")
    sq_head_axi_id = Param.UInt32(18, "Fixed SQ head AXI ID")
    cq_tail_axi_id = Param.UInt32(19, "Fixed CQ tail AXI ID")
    cq_entry_axi_id = Param.UInt32(20, "CQ entry AXI ID")
    msi_axi_id = Param.UInt32(32, "MSI AXI ID pool base")
    msi_axi_id_count = Param.UInt32(4, "MSI AXI ID pool size")
    executor = Param.String(
        "protocol_probe", "protocol_probe or full_context_surrogate"
    )
    plan_image = Param.String("", "agent plan image path")
    kv_session_record_entries = Param.UInt32(
        64, "Bounded plan-mode session record table entries"
    )
    accepted_queue_entries = Param.UInt32(
        64, "Bounded plan-mode accepted generate queue entries"
    )
    output_b_error_request = Param.UInt64(
        0, "Fail the first OUTPUT write B of this request id, 0 disables"
    )
    output_b_error_segment = Param.UInt32(
        0, "Output segment whose B fails, counting committed segments"
    )
    control_cq_first = Param.Bool(
        False, "Publish the cancel command CQ before the target CQ"
    )


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
