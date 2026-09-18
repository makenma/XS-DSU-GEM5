from m5.params import *
from m5.SimObject import SimObject


class PrestartCancelDriver(SimObject):
    type = "PrestartCancelDriver"
    cxx_header = "dev/ai_mesh/prestart_cancel_driver.hh"
    cxx_class = "gem5::ai_mesh::PrestartCancelDriver"

    dispatcher = Param.MeshDispatcher("Dispatcher under test")
    cores = VectorParam.MeshDummyCore([], "Cores observed for late work")
    evidence_path = Param.String("", "Cancel driver evidence artifact path")
    poll_interval = Param.Tick(
        1000, "Wait-window observation interval in ticks"
    )
    wait_timeout = Param.Tick(
        8000000, "Bound on reaching a live reservation wait"
    )
    observe_ticks = Param.Tick(
        8000000, "Horizon observed after the cancel entry is applied"
    )
    cancel_on_wait = Param.Bool(
        True, "Apply the prestart cancel entry when the wait is observed"
    )
