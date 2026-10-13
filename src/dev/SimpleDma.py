from m5.params import *
from m5.SimObject import PyBindMethod
from m5.objects.Device import DmaDevice


class SimpleDma(DmaDevice):
    type = "SimpleDma"
    cxx_class = "gem5::SimpleDma"
    cxx_header = "dev/simple_dma.hh"
    cxx_exports = [PyBindMethod("idle")]
    pio_addr = Param.Addr(0x40000000, "MMIO base")
    pio_size = Param.Addr(4096, "MMIO window")
    pio_latency = Param.Latency("10ns", "Register response latency")
    chunk_bytes = Param.Unsigned(64, "Actual bytes per independent chunk")
    parallel_chunks = Param.Unsigned(1, "Finite independently buffered chunks")
    dma_range = Param.AddrRange(
        AddrRange(0x10000000, size=0x400000),
        "Exclusive noncacheable DMA aperture",
    )
