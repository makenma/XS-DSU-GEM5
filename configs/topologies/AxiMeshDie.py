from m5.objects import *

from topologies.BaseTopology import SimpleTopology


class AxiMeshDie(SimpleTopology):
    """Regular XY mesh with an explicit controller-to-router map."""

    description = "AxiMeshDie"

    def __init__(self, controllers):
        self.nodes = controllers

    def makeTopology(self, options, network, IntLink, ExtLink, Router):
        num_routers = options.axi_mesh_routers
        num_rows = options.mesh_rows
        if num_rows <= 0 or num_rows > num_routers:
            raise ValueError("AXI_MESH mesh_rows must be in [1, routers]")
        if num_routers % num_rows != 0:
            raise ValueError("AXI_MESH mesh_rows must divide router count")
        num_columns = num_routers // num_rows

        routers = [
            Router(router_id=i, latency=options.router_latency)
            for i in range(num_routers)
        ]
        network.routers = routers

        router_ids = [int(node.router_id) for node in self.nodes]
        if len(router_ids) != len(set(router_ids)):
            raise ValueError("AXI_MESH endpoint router IDs must be unique")
        if any(rid < 0 or rid >= num_routers for rid in router_ids):
            raise ValueError("AXI_MESH endpoint router ID is out of range")

        link_id = 0
        ext_links = []
        for node, router_id in zip(self.nodes, router_ids):
            ext_links.append(
                ExtLink(
                    link_id=link_id,
                    ext_node=node,
                    int_node=routers[router_id],
                    latency=options.link_latency,
                )
            )
            link_id += 1
        network.ext_links = ext_links

        int_links = []

        # Horizontal links have weight 1; vertical links have weight 2.  This
        # is the stock Mesh_XY weighting and keeps table routing equivalent to
        # deterministic XY when requested.
        for row in range(num_rows):
            for col in range(num_columns - 1):
                left = col + row * num_columns
                right = left + 1
                int_links.append(
                    IntLink(
                        link_id=link_id,
                        src_node=routers[left],
                        dst_node=routers[right],
                        src_outport="East",
                        dst_inport="West",
                        latency=options.link_latency,
                        weight=1,
                    )
                )
                link_id += 1
                int_links.append(
                    IntLink(
                        link_id=link_id,
                        src_node=routers[right],
                        dst_node=routers[left],
                        src_outport="West",
                        dst_inport="East",
                        latency=options.link_latency,
                        weight=1,
                    )
                )
                link_id += 1

        for col in range(num_columns):
            for row in range(num_rows - 1):
                north = col + row * num_columns
                south = north + num_columns
                int_links.append(
                    IntLink(
                        link_id=link_id,
                        src_node=routers[north],
                        dst_node=routers[south],
                        src_outport="North",
                        dst_inport="South",
                        latency=options.link_latency,
                        weight=2,
                    )
                )
                link_id += 1
                int_links.append(
                    IntLink(
                        link_id=link_id,
                        src_node=routers[south],
                        dst_node=routers[north],
                        src_outport="South",
                        dst_inport="North",
                        latency=options.link_latency,
                        weight=2,
                    )
                )
                link_id += 1

        network.int_links = int_links

    def registerTopology(self, options):
        # AXI endpoints are not CPUs and do not populate the pseudo filesystem.
        return
