from m5.objects.Probe import *
from m5.SimObject import *


class GuestCallStackProfiler(ProbeListenerObject):
    """ROI-gated guest shadow-call-stack sampler for an O3 CPU.

    The listener observes retired guest macro-instructions. It never schedules
    a simulation event, and therefore changes host work only, not modeled
    timing. Addresses are intentionally symbolized offline against the ELF
    belonging to this CPU, which is required for multiprogram SE runs.
    """

    type = "GuestCallStackProfiler"
    cxx_class = "gem5::o3::GuestCallStackProfiler"
    cxx_header = "cpu/o3/probe/guest_call_stack_profiler.hh"
    cxx_exports = [
        PyBindMethod("start"),
        PyBindMethod("stop"),
    ]

    profile_file = Param.String(
        "guest_stack_samples.csv",
        "Raw guest PC and ROI-local shadow-call-stack sample CSV",
    )
    metadata_file = Param.String(
        "guest_stack_samples_metadata.json",
        "Recorder method, interval, and quality counters",
    )
    sample_period_insts = Param.UInt64(
        1000,
        "Take one sample per this many retired guest macro-instructions",
    )
