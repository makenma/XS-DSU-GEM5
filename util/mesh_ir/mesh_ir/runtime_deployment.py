from dataclasses import asdict, dataclass, replace

from mesh_ir.architecture import (
    ArchManifest, ControlInitiatorEndpoint, ControlInitiatorRole, InitiatorEndpoint, NetworkConfig,
    SourceTargetQuota, TargetEndpoint, validate_arch, validate_fabric_quotas,
)
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.runtime_memory import MemoryAddressMap, MemoryRange


@dataclass(frozen=True)
class FabricDeployment:
    network: NetworkConfig
    initiators: tuple[InitiatorEndpoint | ControlInitiatorEndpoint, ...]
    targets: tuple[TargetEndpoint, ...]
    quotas: tuple[SourceTargetQuota, ...]
    address_ranges: tuple[MemoryRange, ...]
    memory_nodes: tuple[int, ...]
    peer_nodes: tuple[tuple[int, int], ...]
    default_error_target_node: int

    @classmethod
    def from_arch(cls, arch: ArchManifest):
        validate_arch(arch)
        ranges, memory_nodes, peer_nodes = [], [], []
        for target in arch.fabric.targets:
            kinds = {arch.regions[row.region_id].kind for row in target.ranges}
            if 'CORE_SRAM_APERTURE' in kinds:
                owners = {row.owner_core for row in target.ranges}
                if len(kinds) != 1 or len(owners) != 1:
                    raise MeshIrError('E_CONFIG', 'physical SRAM target must have one core owner', target=target.name)
                peer_nodes.append((next(iter(owners)), target.dst_node))
            elif kinds:
                memory_nodes.append(target.dst_node)
            for row in target.ranges:
                region = arch.regions[row.region_id]
                start = region.base + row.offset_bytes
                if region.kind == 'CORE_SRAM_APERTURE':
                    start += row.owner_core * region.tile_stride
                ranges.append(MemoryRange(target.dst_node, start, start + row.size_bytes))
        if sorted(core for core, node in peer_nodes) != sorted(arch.core_ids):
            raise MeshIrError('E_CONFIG', 'physical deployment needs one SRAM target per core')
        return cls(arch.fabric.network, arch.fabric.initiators, arch.fabric.targets,
                   arch.fabric.quotas, MemoryAddressMap(tuple(ranges)).ranges,
                   tuple(memory_nodes), tuple(peer_nodes), arch.fabric.default_error_target_node)

    @property
    def routers(self):
        return self.network.router_rows * self.network.router_cols

    @property
    def memory_targets(self):
        return tuple(target for target in self.targets if target.dst_node in self.memory_nodes)

    @property
    def memory_map(self):
        return MemoryAddressMap(tuple(row for row in self.address_ranges if row.node in self.memory_nodes))

    def core_initiator_index(self, core_id: int):
        return next(index for index, row in enumerate(self.initiators)
                    if type(row) is InitiatorEndpoint and row.core_id == core_id)

    def target_index(self, node: int):
        return next(index for index, row in enumerate(self.targets) if row.dst_node == node)

    def peer_node(self, core_id: int):
        return dict(self.peer_nodes)[core_id]

    def peer_target_index(self, core_id: int):
        return self.target_index(self.peer_node(core_id))

    def scenario(self, name: str, driver_mode: str):
        return dict(schema_version=1, name=name, driver_mode=driver_mode,
                    default_error_target=self.default_error_target_node,
                    endpoint_to_router=dict(
                        initiators=[dict(src_node=row.src_node, src_port=row.src_port,
                                         router_id=row.router_id, default_target=row.default_target_node)
                                    for row in self.initiators],
                        targets=[dict(dst_node=row.dst_node, router_id=row.router_id) for row in self.targets]),
                    target_ranges=[dict(dst_node=row.node, start=row.begin, end=row.end) for row in self.address_ranges],
                    quotas=[asdict(row) for row in self.quotas])


@dataclass(frozen=True)
class ServingDeployment:
    fabric: FabricDeployment
    driver_index: int
    frontend_index: int
    npu_control_target_index: int
    host_target_index: int

    @classmethod
    def from_arch(cls, arch: ArchManifest, npu_control_base: int,
                  proxy_control_base: int, host_base: int, host_bytes: int,
                  *, router_id: int = 0,
                  control_quota: tuple[int, int, int, int] = (1, 128, 1, 128)):
        base = FabricDeployment.from_arch(arch)
        if arch.fabric.control_initiators:
            raise MeshIrError('E_CONFIG', 'serving composition requires a compute-only architecture')
        if type(router_id) is not int or not 0 <= router_id < base.routers:
            raise MeshIrError('E_CONFIG', 'serving control router is out of range')
        if len(control_quota) != 4 or any(type(value) is not int or value <= 0
                                        for value in control_quota):
            raise MeshIrError('E_CONFIG', 'serving control quota must contain four positive integers')
        occupied = {row.src_node for row in base.initiators} | {row.dst_node for row in base.targets}
        available = tuple(node for node in range(65536) if node not in occupied)
        if len(available) < 4:
            raise MeshIrError('E_CONFIG', 'serving protocol node identities are exhausted')
        driver_node, frontend_node, npu_node, proxy_node = available[:4]
        host_range = MemoryRange(proxy_node, host_base, host_base + host_bytes)
        MemoryAddressMap((host_range,))
        intersecting = tuple(row for row in base.address_ranges
                             if row.begin < host_range.end and host_range.begin < row.end)
        targets = list(base.targets)
        ranges = list(base.address_ranges)
        memory_nodes = base.memory_nodes
        if intersecting:
            owners = {row.node for row in intersecting}
            base.memory_map.partition([(host_base, host_bytes)])
            if len(owners) != 1:
                raise MeshIrError('E_CONFIG', 'serving host arena must have one physical owner')
            proxy_node = next(iter(owners))
        else:
            targets.append(TargetEndpoint('serving_host', proxy_node, router_id, ()))
            ranges.append(host_range)
            memory_nodes += (proxy_node,)
        targets.append(TargetEndpoint('serving_control', npu_node, router_id, ()))
        ranges.extend((MemoryRange(npu_node, npu_control_base, npu_control_base + 0x1000),
                       MemoryRange(proxy_node, proxy_control_base, proxy_control_base + 0x1000)))
        address_map = MemoryAddressMap(tuple(ranges))
        controls = (
            ControlInitiatorEndpoint('host_driver', ControlInitiatorRole.HOST_DRIVER,
                                     driver_node, 0, router_id, npu_node),
            ControlInitiatorEndpoint('serving_frontend', ControlInitiatorRole.SERVING_FRONTEND,
                                     frontend_node, 0, router_id, proxy_node),
        )
        sources = base.initiators + controls
        existing = {(row.src_node, row.src_port, row.dst_node) for row in base.quotas}
        quotas = base.quotas + tuple(
            SourceTargetQuota(source.src_node, source.src_port, target.dst_node, *control_quota)
            for source in sources for target in targets
            if (source.src_node, source.src_port, target.dst_node) not in existing)
        validate_fabric_quotas(sources, targets, quotas, arch.fabric.target)
        fabric = replace(base, initiators=sources, targets=tuple(targets), quotas=quotas,
                         address_ranges=address_map.ranges, memory_nodes=memory_nodes)
        return cls(fabric, len(base.initiators), len(base.initiators) + 1,
                   fabric.target_index(npu_node), fabric.target_index(proxy_node))

    def scenario(self):
        return dict(self.fabric.scenario('gate6_serving', 'gate3_protocol'),
                    cpu_core_count=0, cpu_garnet_ports=0, ucie_links=0,
                    traffic_shaper='AgentAxiDriver',
                    channel_injection_delay_cycles=dict(aw=0, w=0, ar=0),
                    response_ejection_stall_until_cycle=dict(b=0, r=0),
                    consumer_stall_until_cycle=dict(b=0, r=0),
                    mesh_planned_extra_latency=[], mesh_planned_faults=[])
