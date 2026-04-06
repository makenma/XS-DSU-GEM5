from m5.params import *
from m5.proxy import *
from m5.objects.ClockedObject import ClockedObject


class BasicChiComponent(ClockedObject):
    """Base class for CHI nodes/components.

    This SimObject mainly exists so SCons can generate
    params/BasicChiComponent.hh (included by BasicChiComponent.hh).
    """

    type = "BasicChiComponent"
    cxx_header = "mem/cache/CHI/base/BasicChiComponent.hh"
    cxx_class = "gem5::Chi::BasicChiComponent"

    # Coordinates / identity (see BasicChiComponent.cc)
    x = Param.UInt16(0, "X coordinate")
    y = Param.UInt16(0, "Y coordinate")
    port = Param.UInt16(0, "Port id")
    device = Param.UInt16(0, "Device id")

    # Node type string: rni/rnf/hni/hnf/router/sn/unknown
    node_type = Param.String("unknown", "Node type string")
