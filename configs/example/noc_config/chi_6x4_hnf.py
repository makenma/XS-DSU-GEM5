"""BOSC 6x4 CHI mesh description with the 16 HN-Fs from figure 23.2.

Only routers and HN-F attachments are described here.  CCG, RNI, HNI,
debug, and I/O endpoints in the source diagram are intentionally omitted.

The HN-F selector follows the Arm CMN SCG hash.  For 16 HN-Fs each output
bit is the XOR parity of one PA-bit lane::

    index[0] = PA[6] ^ PA[10] ^ ... ^ PA[50]
    index[1] = PA[7] ^ PA[11] ^ ... ^ PA[51]
    index[2] = PA[8] ^ PA[12] ^ ... ^ PA[48]
    index[3] = PA[9] ^ PA[13] ^ ... ^ PA[49]

Figure 23.2 labels the system address width as 48 bits, so PA[48:51] are
zero in this configuration.  ``cmn_hnf_index`` remains parameterized so the
same helper can validate the complete CMN table for 1--64 HN-Fs.
"""

from dataclasses import dataclass
from math import log2


MESH_COLUMNS = 6
MESH_ROWS = 4
HNF_PORT = 1
HNF_DEVICE = 0
PHYSICAL_ADDRESS_BITS = 48

# Figure 23.2 contains five HN-Fs in each of rows 0--2 and one at (1, 3).
# The order is the programmable CMN SCG target-ID table order used by the
# hash result.  Keeping it explicit avoids accidentally treating a physical
# mesh coordinate as the logical hash index.
HNF_COORDINATES = (
    (0, 0), (1, 0), (2, 0), (3, 0), (4, 0),
    (0, 1), (1, 1), (2, 1), (3, 1), (4, 1),
    (0, 2), (1, 2), (2, 2), (3, 2), (4, 2),
    (1, 3),
)


@dataclass(frozen=True)
class HnfAttachment:
    """One logical HN-F index and its physical router attachment."""

    index: int
    x: int
    y: int
    port: int = HNF_PORT
    device: int = HNF_DEVICE

    @property
    def node_id(self):
        return chi_node_id(self.x, self.y, self.port, self.device)

    @property
    def local_port_index(self):
        # ChiRouterRefModel fixes dnum at four.
        return self.port * 4 + self.device


def chi_node_id(x, y, port=0, device=0):
    """Encode the 11-bit CHI node ID used by ``ChiRouterRefModel``."""

    if not 0 <= x < 16:
        raise ValueError("CHI X coordinate must fit in four bits")
    if not 0 <= y < 8:
        raise ValueError("CHI Y coordinate must fit in three bits")
    if not 0 <= port < 4 or not 0 <= device < 4:
        raise ValueError("CHI port and device must fit in two bits")
    return (x << 7) | (y << 4) | (port << 2) | device


HNF_ATTACHMENTS = tuple(
    HnfAttachment(index, x, y)
    for index, (x, y) in enumerate(HNF_COORDINATES)
)
HNF_NODE_IDS = tuple(attachment.node_id for attachment in HNF_ATTACHMENTS)


def router_index(x, y):
    """Return the row-major index used by the 24-element router vector."""

    if not 0 <= x < MESH_COLUMNS or not 0 <= y < MESH_ROWS:
        raise ValueError("router coordinate is outside the 6x4 mesh")
    return y * MESH_COLUMNS + x


def mesh_links():
    """Yield every undirected nearest-neighbor mesh link exactly once."""

    for y in range(MESH_ROWS):
        for x in range(MESH_COLUMNS):
            if x + 1 < MESH_COLUMNS:
                yield (x, y), (x + 1, y)
            if y + 1 < MESH_ROWS:
                yield (x, y), (x, y + 1)


def _validate_hnf_count(hnf_count):
    if hnf_count < 1 or hnf_count > 64 or hnf_count & (hnf_count - 1):
        raise ValueError("CMN HN-F count must be a power of two in [1, 64]")


def cmn_hnf_xor_masks(hnf_count=16, pa_bits=PHYSICAL_ADDRESS_BITS):
    """Return the one-bit XOR masks from the Arm CMN SCG hash table.

    ``pa_bits`` is an exclusive upper bound.  Bits not implemented by the
    physical address are equivalent to zero, as required by the CMN TRM.
    """

    _validate_hnf_count(hnf_count)
    if not 6 <= pa_bits <= 64:
        raise ValueError("physical address width must be in [6, 64]")
    if hnf_count == 1:
        return ()

    index_bits = int(log2(hnf_count))
    return tuple(
        sum(1 << bit for bit in range(6 + output_bit, pa_bits, index_bits))
        for output_bit in range(index_bits)
    )


CMN_16_HNF_XOR_MASKS = cmn_hnf_xor_masks()


def cmn_hnf_index(address, hnf_count=16, pa_bits=PHYSICAL_ADDRESS_BITS):
    """Hash a physical address to the logical HN-F target-table index."""

    if address < 0:
        raise ValueError("physical address must be non-negative")
    masks = cmn_hnf_xor_masks(hnf_count, pa_bits)
    index = 0
    for output_bit, mask in enumerate(masks):
        # int.bit_count() parity is the XOR reduction of the selected bits.
        index |= ((address & mask).bit_count() & 1) << output_bit
    return index


def hnf_node_id_for_address(address, pa_bits=PHYSICAL_ADDRESS_BITS):
    """Return the figure-23.2 HN-F node ID selected for ``address``."""

    return HNF_NODE_IDS[cmn_hnf_index(address, len(HNF_NODE_IDS), pa_bits)]


def connect_router_hnf_mesh(system):
    """Connect pre-created ``chi_routers`` and ``home_node`` vectors.

    The function deliberately configures only Router-to-Router links and the
    sixteen P1/D0 HN-F endpoints.  Boundary RN/SN adapters, if a runnable
    system needs them, are connected by the caller and are not part of this
    topology description.
    """

    expected_routers = MESH_COLUMNS * MESH_ROWS
    if len(system.chi_routers) != expected_routers:
        raise ValueError(
            "6x4 topology requires {} routers, got {}".format(
                expected_routers, len(system.chi_routers)
            )
        )
    if len(system.home_node) != len(HNF_ATTACHMENTS):
        raise ValueError(
            "figure 23.2 requires {} HN-Fs, got {}".format(
                len(HNF_ATTACHMENTS), len(system.home_node)
            )
        )

    east, south, west, north = 0, 1, 2, 3
    for (left_x, left_y), (right_x, right_y) in mesh_links():
        left = system.chi_routers[router_index(left_x, left_y)]
        right = system.chi_routers[router_index(right_x, right_y)]
        if right_x != left_x:
            left.internal_ports[east] = right.internal_peer_ports[west]
            right.internal_ports[west] = left.internal_peer_ports[east]
        else:
            left.internal_ports[north] = right.internal_peer_ports[south]
            right.internal_ports[south] = left.internal_peer_ports[north]

    for attachment, hnf in zip(HNF_ATTACHMENTS, system.home_node):
        hnf.x = attachment.x
        hnf.y = attachment.y
        hnf.port = attachment.port
        hnf.device = attachment.device
        hnf.node_type = "hnf"
        router = system.chi_routers[router_index(attachment.x, attachment.y)]
        router.local_ports[attachment.local_port_index] = hnf.rxport
