from dataclasses import asdict, dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Mapping

from mesh_ir.acceptance import canonical_digest


CHANNELS = ("AW", "W", "B", "AR", "R")
BASE_DEPTHS = (4, 8, 4, 4, 8)


def validated_yx_vnets(vnets) -> tuple[int, ...]:
    if not isinstance(vnets, (list, tuple)) or any(
            type(vnet) is not int or not 0 <= vnet < len(CHANNELS) for vnet in vnets):
        raise ValueError("yx_vnets requires valid integer vnet IDs")
    if len(set(vnets)) != len(vnets):
        raise ValueError("yx_vnets requires unique vnet IDs")
    return tuple(vnets)


class Workload(StrEnum):
    LOAD_ONLY = "LOAD_ONLY"
    STORE_ONLY = "STORE_ONLY"
    MIXED_1_1 = "MIXED_1_1"


@dataclass(frozen=True)
class Topology:
    name: str
    layouts: ClassVar[Mapping[str, tuple[int, ...]]] = MappingProxyType({
        "H5": (0, 5, 10, 15, 20),
        "H10": (0, 5, 10, 15, 20, 4, 9, 14, 19, 24),
        "H10_EAST2": (4, 4, 9, 9, 14, 14, 19, 19, 24, 24),
    })

    def __post_init__(self):
        if self.name not in self.layouts:
            raise ValueError(f"unknown experiment topology: {self.name}")

    @property
    def core_ids(self):
        return tuple(range(25))

    @property
    def hbm_routers(self):
        return self.layouts[self.name]

    def diagnostic_cores(self, target=0):
        router = self.hbm_routers[target]
        distances = {core: abs(core % 5 - router % 5) + abs(core // 5 - router // 5)
                     for core in self.core_ids}
        return min(distances, key=distances.get), max(distances, key=distances.get)

    @property
    def error_node(self):
        return 25 + len(self.hbm_routers)

    def target_node(self, index):
        if type(index) is not int or not 0 <= index < len(self.hbm_routers):
            raise ValueError("HBM target index out of range")
        return 25 + index

    def endpoints(self):
        return [{"node": core, "router": core, "kind": "core"}
                for core in self.core_ids] + [
            {"node": self.target_node(index), "router": router, "kind": "hbm"}
            for index, router in enumerate(self.hbm_routers)] + [
            {"node": self.error_node, "router": 12, "kind": "error"}]

    def router_tags(self, hot_routers):
        hot_routers = set(hot_routers)
        if not hot_routers <= set(self.core_ids):
            raise ValueError("hotspot label refers to an unknown router")
        labels = {"EDGE": {router for router in self.core_ids if router % 5 in (0, 4) or router // 5 in (0, 4)},
                  "CORNER": {0, 4, 20, 24}, "HOT": hot_routers, "HBM_ATTACH": set(self.hbm_routers)}
        return {str(router): sorted(label for label, members in labels.items() if router in members)
                for router in self.core_ids}


@dataclass(frozen=True)
class WorkloadSpec:
    workload: str
    bytes_per_core: int = 1048576
    tile_bytes: int = 65536
    ring_slots: int = 2
    active_cores: tuple = tuple(range(25))
    distribution: str = "uniform"
    hotspot_target: int = 0
    hbm_port_bytes: int = 67108864

    def __post_init__(self):
        Workload(self.workload)
        for value in (self.bytes_per_core, self.tile_bytes,
                      self.ring_slots, self.hbm_port_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError("workload dimensions must be positive integers")
        if self.tile_bytes % 64 or self.bytes_per_core % self.tile_bytes:
            raise ValueError("workload must contain complete aligned tiles")
        if self.workload == Workload.MIXED_1_1 and self.tiles_per_core % 2:
            raise ValueError("MIXED requires equal complete read/write tiles")
        if self.distribution not in ("uniform", "hotspot", "single_target"):
            raise ValueError("unknown workload distribution")
        if self.distribution == "hotspot" and (
                self.tiles_per_core % (4 if self.workload == Workload.MIXED_1_1 else 2)):
            raise ValueError("hotspot requires exactly divisible half-traffic tiles")
        if not self.active_cores or tuple(sorted(set(self.active_cores))) != self.active_cores:
            raise ValueError("active cores must be sorted, nonempty and unique")
        if any(type(core) is not int or not 0 <= core < 25 for core in self.active_cores):
            raise ValueError("invalid active core")
        if self.hbm_port_bytes < 50 * self.bytes_per_core:
            raise ValueError("HBM window cannot hold disjoint per-core directions")

    @property
    def tiles_per_core(self):
        return self.bytes_per_core // self.tile_bytes

    def document(self):
        return asdict(self)


@dataclass(frozen=True)
class BufferMap:
    entries: tuple

    def __post_init__(self):
        seen, ports = set(), set()
        for entry in self.entries:
            if len(entry) != 4 or any(type(value) is not int for value in entry):
                raise ValueError("buffer entries require integer router/inport/vnet/depth")
            router, port, vnet, depth = entry
            if not 0 <= router < 25 or port < 0 or not 0 <= vnet < 5 or depth <= 0:
                raise ValueError("invalid buffer entry")
            if entry[:3] in seen:
                raise ValueError("duplicate buffer entry")
            seen.add(entry[:3])
            ports.add(entry[:2])
        if not ports or len(seen) != len(ports) * 5:
            raise ValueError("every actual port requires all five vnets")
        object.__setattr__(self, "entries", tuple(sorted(tuple(row) for row in self.entries)))

    @classmethod
    def uniform(cls, ports, depths=BASE_DEPTHS):
        ports = tuple(ports)
        if len(depths) != 5 or len(set(ports)) != len(ports):
            raise ValueError("uniform map requires five depths and unique actual ports")
        return cls(tuple((router, port, vnet, depth)
                         for router, port in ports for vnet, depth in enumerate(depths)))

    def replace(self, changes):
        current = {row[:3]: row[3] for row in self.entries}
        if not set(changes) <= set(current):
            raise ValueError("override refers to nonexistent router/input/vnet")
        current.update(changes)
        return BufferMap(tuple((*key, value) for key, value in current.items()))

    def uniform_depths(self):
        values = [{row[3] for row in self.entries if row[2] == vnet} for vnet in range(5)]
        return tuple(next(iter(group)) for group in values) if all(len(group) == 1 for group in values) else None

    def slots(self, vcs=4):
        if type(vcs) is not int or vcs <= 0:
            raise ValueError("VC count must be positive")
        return vcs * sum(row[3] for row in self.entries)

    def storage_bytes(self, flit_bytes=16, vcs=4):
        if type(flit_bytes) is not int or flit_bytes <= 0:
            raise ValueError("flit width must be positive")
        return flit_bytes * self.slots(vcs)

    def digest(self):
        return canonical_digest(self.entries)

    def overrides(self):
        return [":".join(map(str, row)) for row in self.entries]

    def capacity_table(self, flit_bytes=16, vcs=4):
        return [{"router": router, "input_port": port, "vnet": vnet,
                 "channel": CHANNELS[vnet], "depth": depth, "vcs": vcs,
                 "slots": vcs * depth, "storage_bytes": vcs * depth * flit_bytes}
                for router, port, vnet, depth in self.entries]
