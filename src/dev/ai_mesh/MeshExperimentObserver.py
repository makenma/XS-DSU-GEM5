from m5.params import *
from m5.SimObject import PyBindMethod, SimObject


class MeshExperimentObserver(SimObject):
    type = "MeshExperimentObserver"
    cxx_header = "dev/ai_mesh/mesh_experiment_observer.hh"
    cxx_class = "gem5::ai_mesh::MeshExperimentObserver"
    cxx_exports = [
        PyBindMethod("dumpSnapshot"),
        PyBindMethod("dumpMemoryChecks"),
        PyBindMethod("drained"),
    ]

    cores = VectorParam.MeshDummyCore([], "Observed real-DMA cores")
    targets = VectorParam.AxiTargetAdapter([], "Observed targets including error target")
    network = Param.RubyNetwork("Observed Garnet network")
    core_memory_checks = VectorParam.String(
        [], "Core index:SRAM offset:size:expected byte"
    )
    target_memory_checks = VectorParam.String(
        [], "Target index:address:size:expected byte"
    )
