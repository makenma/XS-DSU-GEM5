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


# ---------------------------------------------------------------------------
# D0 boundary attachments from figure 23.2: 16 KMH RN-F endpoints and 4 DDR
# SN endpoints.  Each entry fixes the logical index, the router coordinate,
# the P/D port, and the 11-bit CHI node ID = (x<<7)|(y<<4)|(port<<2)|device.
#
# RN-F bridges attach to the router ``device_ports`` vector; HN-F and DDR SN
# bridges attach to the router ``local_ports`` vector.  The two vectors are
# independent, so one router may carry an RN-F on device_ports and an HN-F or
# DDR endpoint on local_ports without any port conflict.  Within a single
# vector, one (router, P, D) slot hosts at most one endpoint.
# ---------------------------------------------------------------------------

CPU_PORT = 0
CPU_DEVICE = 0
DDR_PORT = 2
DDR_DEVICE = 0
DDR_CHANNEL_COUNT = 4
DDR_INTERLEAVE_SHIFT = 6  # 64-byte cache-line interleave: bits [7:6]


@dataclass(frozen=True)
class RnfAttachment:
    """One logical Linux CPU and its router RN-F attachment."""

    index: int
    x: int
    y: int
    port: int = CPU_PORT
    device: int = CPU_DEVICE

    @property
    def node_id(self):
        return chi_node_id(self.x, self.y, self.port, self.device)

    @property
    def device_port_index(self):
        # ChiRouterRefModel.device_ports is indexed as p * dnum + d (dnum=4).
        return self.port * 4 + self.device


# Figure 23.2 D0: 16 KMH cores.  CPU 15 shares router R_0_3 with CPU 10:
# CPU 10 is on P0/D0 and CPU 15 is on P1/D0.  All other CPUs use P0/D0.
# Node IDs match the seed table exactly (verified against
# (x<<7)|(y<<4)|(port<<2)|device).
CPU_COORDINATES = (
    (0, 1), (1, 1), (2, 1), (3, 1), (4, 1),
    (0, 2), (1, 2), (2, 2), (3, 2), (4, 2),
    (0, 3), (1, 3), (2, 3), (3, 3), (4, 3),
    (0, 3),
)
CPU_PORT_OVERRIDES = {
    # CPU 15 sits on R_0_3 P1/D0 (shares the router with CPU 10 on P0/D0).
    15: 1,
}

CPU_ATTACHMENTS = tuple(
    RnfAttachment(
        index,
        x,
        y,
        port=CPU_PORT_OVERRIDES.get(index, CPU_PORT),
        device=CPU_DEVICE,
    )
    for index, (x, y) in enumerate(CPU_COORDINATES)
)
CPU_NODE_IDS = tuple(attachment.node_id for attachment in CPU_ATTACHMENTS)


@dataclass(frozen=True)
class DdrAttachment:
    """One DDR SN channel and its router attachment at P2/D0."""

    index: int
    x: int
    y: int
    port: int = DDR_PORT
    device: int = DDR_DEVICE

    @property
    def node_id(self):
        return chi_node_id(self.x, self.y, self.port, self.device)

    @property
    def local_port_index(self):
        # ChiRouterRefModel.local_ports is indexed as p * dnum + d (dnum=4).
        return self.port * 4 + self.device


# Figure 23.2 D0 west edge: two DDR-controller groups drive four 32-bit
# DDR5 PHY/DIMM channels, modelled as four cache-line-interleaved DDR SN
# endpoints on R_0_0..R_0_3 P2/D0.  This replaces the legacy single shared SN
# adapter that sat at router (5,0) P0/D0.
DDR_COORDINATES = ((0, 0), (0, 1), (0, 2), (0, 3))
DDR_ATTACHMENTS = tuple(
    DdrAttachment(index, x, y)
    for index, (x, y) in enumerate(DDR_COORDINATES)
)
DDR_NODE_IDS = tuple(attachment.node_id for attachment in DDR_ATTACHMENTS)


def ddr_index_for_address(address):
    """Return the DDR channel index (0..3) for a physical address.

    64-byte cache-line interleave: ``ddr_index = (address >> 6) & 0x3``.
    The HN-F SN target table and the classic-memory AddrRange interleave use
    the same bit field so the CHI-side and classic-side selections agree, and
    every physical address maps to exactly one DDR channel.
    """

    return (address >> DDR_INTERLEAVE_SHIFT) & (DDR_CHANNEL_COUNT - 1)


def ddr_node_id_for_address(address):
    """Return the DDR SN node ID selected for ``address``."""

    return DDR_NODE_IDS[ddr_index_for_address(address)]


def validate_d0_attachments():
    """Assert every D0 endpoint Node ID is unique and no router P/D slot is
    double-booked on the same port vector.

    Returns True on success so callers can write ``assert validate_...()``.
    """

    all_ids = HNF_NODE_IDS + CPU_NODE_IDS + DDR_NODE_IDS
    if len(set(all_ids)) != len(all_ids):
        duplicates = sorted({nid for nid in all_ids if all_ids.count(nid) > 1})
        raise ValueError("duplicate D0 CHI Node IDs: %s" % duplicates)

    # device_ports vector: RN-F endpoints only.
    device_slots = [
        (a.x, a.y, a.port, a.device) for a in CPU_ATTACHMENTS
    ]
    if len(set(device_slots)) != len(device_slots):
        raise ValueError("RN-F device_ports slot collision")

    # local_ports vector: HN-F and DDR endpoints share this vector, so check
    # them together.  HN-F uses P1/D0 and DDR uses P2/D0, so they never
    # overlap, but the assertion guards against future edits.
    local_slots = (
        [(a.x, a.y, a.port, a.device) for a in HNF_ATTACHMENTS]
        + [(a.x, a.y, a.port, a.device) for a in DDR_ATTACHMENTS]
    )
    if len(set(local_slots)) != len(local_slots):
        raise ValueError("HN-F/DDR local_ports slot collision")

    return True


def connect_ddr_bridges(system):
    """Attach the four DDR SN bridges at R_0_0..R_0_3 P2/D0.

    ``system.snf_bridges`` must already hold four Chi2ClassicMemBridge
    instances (created by the system builder).  Each bridge becomes a distinct
    CHI SN endpoint with a unique Node ID on its router's local_ports P2/D0
    slot; the HN-F selects among them by 64-byte cache-line interleave (see
    ``ddr_index_for_address``).  The classic-memory side of every bridge
    forwards to ``system.membus``, preserving the platform's existing
    functional memory backing.

    This replaces the legacy single shared SN adapter at router (5,0) P0/D0.
    """

    bridges = getattr(system, "snf_bridges", None)
    if bridges is None:
        raise ValueError(
            "connect_ddr_bridges requires system.snf_bridges (a list of "
            "four Chi2ClassicMemBridge instances)"
        )
    if len(bridges) != len(DDR_ATTACHMENTS):
        raise ValueError(
            "D0 requires %d DDR SN bridges, got %d"
            % (len(DDR_ATTACHMENTS), len(bridges))
        )

    for attachment, bridge in zip(DDR_ATTACHMENTS, bridges):
        bridge.node_id = attachment.node_id
        bridge.hnf_node_id = 0  # respond to the requesting HN-F SrcID
        bridge.block_size = system.cache_line_size
        bridge.max_outstanding = 512
        router = system.chi_routers[router_index(attachment.x, attachment.y)]
        router.local_ports[attachment.local_port_index] = bridge.chi_side
        bridge.mem_side = system.membus.cpu_side_ports


def format_topology_summary():
    """Return the human-readable D0 topology summary string.

    Kept pure-Python (no gem5 objects) so it can be unit-tested and printed
    from either the gem5 config entry or the standalone test harness.
    """

    lines = []
    lines.append("CHI figure-23.2 D0 topology summary")
    lines.append("  Routers:    %d (%dx%d mesh)" % (
        MESH_COLUMNS * MESH_ROWS, MESH_COLUMNS, MESH_ROWS))
    lines.append("  Mesh links: %d bidirectional nearest-neighbor" %
                 sum(1 for _ in mesh_links()))
    lines.append("  RN-F (CPU): %d endpoints on device_ports" %
                 len(CPU_ATTACHMENTS))
    lines.append("  HN-F (SLC): %d endpoints on local_ports P1/D0" %
                 len(HNF_ATTACHMENTS))
    lines.append("  DDR (SN):   %d endpoints on local_ports P2/D0" %
                 len(DDR_ATTACHMENTS))
    lines.append("")
    lines.append("  Clocks: CPU 2.3GHz, Router/HN-F 1.8GHz, "
                 "DDR controller 600MHz, DDR5-4800 PHY")
    lines.append("  Memory: default 16GiB over 4 channels, "
                 "64B cache-line interleave ddr_index=(pa>>6)&0x3")
    lines.append("")
    lines.append("  CPU -> Router/P/D -> NodeID:")
    for att in CPU_ATTACHMENTS:
        lines.append("    CPU %2d  R_%d_%d P%d/D%d  %#06x" % (
            att.index, att.x, att.y, att.port, att.device, att.node_id))
    lines.append("")
    lines.append("  HN-F -> Router/P/D -> NodeID:")
    for att in HNF_ATTACHMENTS:
        lines.append("    HN-F %2d R_%d_%d P%d/D%d  %#06x" % (
            att.index, att.x, att.y, att.port, att.device, att.node_id))
    lines.append("")
    lines.append("  DDR -> Router/P/D -> NodeID:")
    for att in DDR_ATTACHMENTS:
        lines.append("    DDR %d  R_%d_%d P%d/D%d  %#06x" % (
            att.index, att.x, att.y, att.port, att.device, att.node_id))
    lines.append("")
    lines.append("  CMN SCG HN-F XOR masks: " +
                 ", ".join("%#014x" % m for m in CMN_16_HNF_XOR_MASKS))
    lines.append("  DDR Node IDs (SN target table): " +
                 ", ".join("%#06x" % nid for nid in DDR_NODE_IDS))
    return "\n".join(lines)


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
