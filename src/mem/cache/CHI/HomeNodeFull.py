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

    # No additional params/ports are declared in the provided C++ headers.
    # If you later expose sub-components (e.g., linklayer ports) via Params,
    # add them here and include PARAMS(HomeNodeFull) in C++.
