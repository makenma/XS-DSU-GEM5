from m5.params import *
from m5.proxy import *

# Inherit parameters (x/y/port/device/node_type) from BasicChiComponent.
from m5.objects.BasicChiComponent import BasicChiComponent


class HomeNodeFull(BasicChiComponent):
    """A full home node model built on top of BasicChiComponent."""

    type = "HomeNodeFull"
    cxx_header = "mem/cache/CHI/HomeNodeFull.hh"
    cxx_class = "gem5::Chi::HomeNodeFull"

    rxport = SlavePort("CHI RX port")

    # QoS threshold per priority — order matches QosPool::PoolPriority:
    # [HighHigh, High, Medium, Low]
    qos_thresholds = VectorParam.Int(
        [100, 80, 60, 40],
        "QoS threshold per priority [HH, H, M, L]")
