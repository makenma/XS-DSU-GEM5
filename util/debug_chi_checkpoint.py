#!/usr/bin/env python3
"""Inspect the memory image represented by a drained CHI checkpoint.

Classic cache checkpointing writes dirty private-cache lines to physical
memory functionally.  A CHI HN-F may still contain a newer (or stale) SLC
copy, so this utility overlays serialized SLC lines on the pmem image and can
walk an Sv48 page table through that combined view.
"""

import argparse
import collections
import mmap
import pathlib
import struct


BACKEND_SUFFIX = ".slcsf.slcsf.backend]"


def _numbers(line, name):
    prefix = name + "="
    if not line.startswith(prefix):
        return None
    payload = line[len(prefix):].strip()
    return [] if not payload else [int(value, 0) for value in payload.split()]


def load_backend(checkpoint):
    lines = {}
    sf_lines = {}
    section = None
    fields = {}

    def finish():
        if section is None:
            return
        required = ("slcValid", "slcTag", "slcState", "slcOwner",
                    "slcDataSize", "slcData", "sfValid", "sfTag",
                    "sfState", "sfOwner", "sfSharers")
        missing = [name for name in required if name not in fields]
        if missing:
            raise RuntimeError(f"{section}: missing {', '.join(missing)}")
        sets = fields["slcSets"][0]
        ways = fields["slcWays"][0]
        block_size = fields["blockSize"][0]
        valid = fields["slcValid"]
        tags = fields["slcTag"]
        states = fields["slcState"]
        owners = fields["slcOwner"]
        sizes = fields["slcDataSize"]
        data = fields["slcData"]
        expected = sets * ways
        if not all(len(values) == expected
                   for values in (valid, tags, states, owners, sizes)):
            raise RuntimeError(f"{section}: malformed SLC arrays")
        data_offset = 0
        for index in range(expected):
            size = sizes[index]
            payload = bytes(data[data_offset:data_offset + size])
            data_offset += size
            if not valid[index]:
                continue
            set_index = index // ways
            address = (tags[index] * sets + set_index) * block_size
            if address in lines:
                raise RuntimeError(
                    f"duplicate SLC address {address:#x} in {section} and "
                    f"{lines[address][0]}")
            lines[address] = (section, states[index], owners[index], payload)
        if data_offset != len(data):
            raise RuntimeError(f"{section}: trailing SLC data")

        sf_sets = fields["sfSets"][0]
        sf_ways = fields["sfWays"][0]
        sf_valid = fields["sfValid"]
        sf_tags = fields["sfTag"]
        sf_states = fields["sfState"]
        sf_owners = fields["sfOwner"]
        sf_sharers = fields["sfSharers"]
        sf_expected = sf_sets * sf_ways
        if not all(len(values) == sf_expected for values in
                   (sf_valid, sf_tags, sf_states, sf_owners, sf_sharers)):
            raise RuntimeError(f"{section}: malformed SF arrays")
        for index in range(sf_expected):
            if not sf_valid[index]:
                continue
            set_index = index // sf_ways
            address = (sf_tags[index] * sf_sets + set_index) * block_size
            if address in sf_lines:
                raise RuntimeError(
                    f"duplicate SF address {address:#x} in {section} and "
                    f"{sf_lines[address][0]}")
            sf_lines[address] = (section, sf_states[index],
                                 sf_owners[index], sf_sharers[index])

    with checkpoint.open("r", encoding="ascii") as stream:
        for raw_line in stream:
            line = raw_line.rstrip("\n")
            if line.startswith("["):
                if section is not None:
                    finish()
                section = line[1:-1] if line.endswith(BACKEND_SUFFIX) else None
                fields = {}
                continue
            if section is None:
                continue
            for name in ("blockSize", "slcSets", "slcWays", "sfSets",
                         "sfWays", "slcValid",
                         "slcTag", "slcState", "slcOwner", "slcDataSize",
                         "slcData", "sfValid", "sfTag", "sfState",
                         "sfOwner", "sfSharers"):
                values = _numbers(line, name)
                if values is not None:
                    fields[name] = values
                    break
    if section is not None:
        finish()
    return lines, sf_lines


def load_slc(checkpoint):
    """Compatibility helper for callers interested only in SLC data."""
    return load_backend(checkpoint)[0]


class MemoryView:
    def __init__(self, pmem_path, slc_lines, base):
        self.stream = pmem_path.open("rb")
        self.pmem = mmap.mmap(self.stream.fileno(), 0, access=mmap.ACCESS_READ)
        self.slc_lines = slc_lines
        self.base = base

    def close(self):
        self.pmem.close()
        self.stream.close()

    def pmem_read(self, address, size):
        offset = address - self.base
        if offset < 0 or offset + size > len(self.pmem):
            raise RuntimeError(f"physical read outside pmem: {address:#x}+{size}")
        return self.pmem[offset:offset + size]

    def read(self, address, size):
        result = bytearray()
        while size:
            block = address & ~63
            offset = address - block
            chunk = min(size, 64 - offset)
            entry = self.slc_lines.get(block)
            source = entry[3] if entry is not None else self.pmem_read(block, 64)
            result.extend(source[offset:offset + chunk])
            address += chunk
            size -= chunk
        return bytes(result)

    def u64(self, address, overlay=True):
        data = self.read(address, 8) if overlay else self.pmem_read(address, 8)
        return struct.unpack("<Q", data)[0]


def sv48_walk(memory, satp, virtual_address, overlay=True):
    table = (satp & ((1 << 44) - 1)) << 12
    path = []
    for level in range(3, -1, -1):
        index = (virtual_address >> (12 + 9 * level)) & 0x1ff
        pte_address = table + index * 8
        pte = memory.u64(pte_address, overlay=overlay)
        path.append((level, index, pte_address, pte))
        if not (pte & 1):
            return None, path
        if pte & 0xe:
            page_offset_bits = 12 + 9 * level
            page_mask = (1 << page_offset_bits) - 1
            physical = ((pte >> 10) << 12) | (virtual_address & page_mask)
            return physical, path
        table = (pte >> 10) << 12
    return None, path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=pathlib.Path)
    parser.add_argument("pmem", type=pathlib.Path)
    parser.add_argument("--pmem-base", type=lambda x: int(x, 0),
                        default=0x80000000)
    parser.add_argument("--satp", type=lambda x: int(x, 0))
    parser.add_argument("--virtual", type=lambda x: int(x, 0))
    parser.add_argument(
        "--physical", type=lambda x: int(x, 0), action="append", default=[],
        help="report pmem/SLC/SF contents for a physical address")
    args = parser.parse_args()

    slc, sf = load_backend(args.checkpoint)
    memory = MemoryView(args.pmem, slc, args.pmem_base)
    mismatches = collections.Counter()
    examples = collections.defaultdict(list)
    try:
        for address, (section, state, owner, data) in slc.items():
            physical = memory.pmem_read(address, len(data))
            if physical != data:
                mismatches[state] += 1
                if len(examples[state]) < 5:
                    examples[state].append((address, section, owner,
                                            physical.hex(), data.hex()))
        print(f"serialized SLC lines: {len(slc)}")
        print(f"serialized SF lines: {len(sf)}")
        print("SLC/pmem mismatches by state:", dict(sorted(mismatches.items())))
        for state in sorted(examples):
            for address, section, owner, pmem, data in examples[state]:
                print(f"  state={state} owner={owner} addr={address:#x} "
                      f"hnf={section} pmem={pmem} slc={data}")

        for address in args.physical:
            block = address & ~63
            offset = address - block
            size = min(8, 64 - offset)
            pmem_data = memory.pmem_read(address, size)
            overlay_data = memory.read(address, size)
            print(f"physical {address:#x}: block={block:#x} offset={offset}")
            print(f"  pmem={pmem_data.hex()} overlay={overlay_data.hex()}")
            if block in slc:
                section, state, owner, _ = slc[block]
                print(f"  SLC hnf={section} state={state} owner={owner}")
            else:
                print("  SLC absent")
            if block in sf:
                print(f"  SF={sf[block]}")
            else:
                print("  SF absent")

        if args.satp is not None and args.virtual is not None:
            for overlay in (False, True):
                physical, path = sv48_walk(
                    memory, args.satp, args.virtual, overlay=overlay)
                label = "SLC overlay" if overlay else "pmem only"
                print(f"{label} walk for {args.virtual:#x}: "
                      f"{physical if physical is None else hex(physical)}")
                for level, index, pte_address, pte in path:
                    entry = slc.get(pte_address & ~63)
                    source = "SLC" if overlay and entry is not None else "pmem"
                    print(f"  L{level} idx={index:#x} pte@{pte_address:#x}="
                          f"{pte:#018x} source={source}")
                if physical is not None:
                    block = physical & ~63
                    entry = slc.get(block)
                    print(f"  value={memory.u64(physical, overlay=overlay):#018x} "
                          f"line={block:#x} SLC={entry is not None}")
                    if entry is not None:
                        print(f"  HNF={entry[0]} state={entry[1]} "
                              f"owner={entry[2]}")
            for address in (args.virtual,):
                block = address & ~63
                if block in sf:
                    print(f"SF {block:#x}: {sf[block]}")
    finally:
        memory.close()


if __name__ == "__main__":
    main()
