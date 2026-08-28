#!/usr/bin/env python3
"""Audit live RISC-V stack frames against a raw checkpoint pmem image.

This checker is intentionally independent of serialized cache state.  It
extracts each CPU's architectural integer registers, PC, SATP, and serialized
DTLB entries from ``m5.cpt``; walks Sv48 through the supplied raw physical-
memory image; and checks the saved S0/RA pair at the current SP.  For a hart
stopped inside the standard ``arch_cpu_idle`` frame used by this payload, all
of the following must agree: the live S0 is SP+16, the saved caller S0 is
SP+32, the saved RA is the live RA, and the serialized DTLB translation is the
same as the translation obtained by walking the page tables stored in
physical memory.

The saved-S0 relation is deliberately not ``saved_s0 == live_s0``.  At the
audited PC, ``arch_cpu_idle`` has allocated a 16-byte frame and set its live
S0 to SP+16.  Its caller, ``default_idle_call``, also has a 16-byte frame, so
the S0 saved by ``arch_cpu_idle`` is the caller's frame pointer: SP+32, or
live_s0+16.  This exact relation follows from the instructions in the frozen
Linux payload at ``arch_cpu_idle`` and ``default_idle_call``.
"""

import argparse
import mmap
import pathlib
import re
import struct


CPU_SECTION = re.compile(r"^\[system\.cpu(\d+)\.(xc\.0|isa)\]$")
DTB_ENTRY_SECTION = re.compile(
    r"^\[system\.cpu(\d+)\.mmu\.dtb\.Entry(\d+)\]$")


def parse_cpu_state(checkpoint):
    states = {}
    current = None
    with checkpoint.open("r", encoding="ascii") as stream:
        for raw_line in stream:
            line = raw_line.rstrip("\n")
            match = CPU_SECTION.match(line)
            if match:
                cpu = int(match.group(1))
                state = states.setdefault(cpu, {})
                current = ("cpu", state, match.group(2))
                continue
            match = DTB_ENTRY_SECTION.match(line)
            if match:
                cpu = int(match.group(1))
                state = states.setdefault(cpu, {})
                entry = {"entry": int(match.group(2))}
                state.setdefault("dtlb", []).append(entry)
                current = ("dtlb", entry, None)
                continue
            if line.startswith("["):
                current = None
                continue
            if current is None:
                continue
            kind, state, section = current
            if kind == "dtlb":
                if "=" in line:
                    key, value = line.split("=", 1)
                    if key in ("paddr", "vaddr", "logBytes", "asid"):
                        state[key] = int(value, 0)
            elif section == "xc.0":
                if line.startswith("intRegs.get()="):
                    state["regs"] = [
                        int(value, 0)
                        for value in line.split("=", 1)[1].split()
                    ]
                elif line.startswith("_pc="):
                    state["pc"] = int(line.split("=", 1)[1], 0)
            elif section == "isa" and line.startswith("miscRegFile="):
                state["misc"] = [
                    int(value, 0)
                    for value in line.split("=", 1)[1].split()
                ]
    return states


def dtlb_translate(entries, virtual_address, asid):
    """Translate an address with the most-specific serialized DTLB entry."""
    matches = []
    for entry in entries:
        if not all(key in entry for key in ("paddr", "vaddr", "logBytes")):
            continue
        if entry.get("asid") != asid:
            continue
        size = 1 << entry["logBytes"]
        if entry["vaddr"] <= virtual_address < entry["vaddr"] + size:
            matches.append(entry)
    if not matches:
        return None
    entry = min(matches, key=lambda item: item["logBytes"])
    page_mask = (1 << entry["logBytes"]) - 1
    physical_base = (entry["paddr"] << 12) & ~page_mask
    return physical_base | (virtual_address & page_mask)


class PhysicalMemory:
    def __init__(self, path, base):
        self._stream = path.open("rb")
        self._data = mmap.mmap(
            self._stream.fileno(), 0, access=mmap.ACCESS_READ)
        self.base = base

    def close(self):
        self._data.close()
        self._stream.close()

    def read(self, address, size):
        offset = address - self.base
        if offset < 0 or offset + size > len(self._data):
            raise RuntimeError(
                f"physical read outside pmem: {address:#x}+{size}")
        return self._data[offset:offset + size]

    def u64(self, address):
        return struct.unpack("<Q", self.read(address, 8))[0]


def sv48_walk(memory, satp, virtual_address):
    table = (satp & ((1 << 44) - 1)) << 12
    for level in range(3, -1, -1):
        index = (virtual_address >> (12 + 9 * level)) & 0x1ff
        pte = memory.u64(table + index * 8)
        if not (pte & 1):
            return None
        if pte & 0xe:
            page_offset_bits = 12 + 9 * level
            page_mask = (1 << page_offset_bits) - 1
            return ((pte >> 10) << 12) | (virtual_address & page_mask)
        table = (pte >> 10) << 12
    return None


def hexadecimal(value):
    return "unmapped" if value is None else f"0x{value:016x}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=pathlib.Path)
    parser.add_argument("pmem", type=pathlib.Path,
                        help="uncompressed physical-memory image")
    parser.add_argument("--pmem-base", type=lambda value: int(value, 0),
                        default=0x80000000)
    parser.add_argument("--satp-index", type=int, default=143)
    parser.add_argument("--idle-pc", type=lambda value: int(value, 0),
                        default=0xffffffff8046f112)
    args = parser.parse_args()

    states = parse_cpu_state(args.checkpoint)
    memory = PhysicalMemory(args.pmem, args.pmem_base)
    idle_total = 0
    idle_passed = 0
    print(
        "cpu pc sp walk_pa dtlb_pa saved_s0 live_s0 saved_ra live_ra "
        "live_s0_frame saved_s0_chain dtlb_match pass")
    try:
        for cpu, state in sorted(states.items()):
            missing = [key for key in ("regs", "pc", "misc")
                       if key not in state]
            if missing:
                raise RuntimeError(f"CPU{cpu}: missing {', '.join(missing)}")
            regs = state["regs"]
            misc = state["misc"]
            if len(regs) <= 8 or len(misc) <= args.satp_index:
                raise RuntimeError(f"CPU{cpu}: truncated architectural state")
            live_ra = regs[1]
            sp = regs[2]
            live_s0 = regs[8]
            satp = misc[args.satp_index]
            physical = sv48_walk(memory, satp, sp)
            satp_asid = (satp >> 44) & 0xffff
            dtlb_physical = dtlb_translate(
                state.get("dtlb", []), sp, satp_asid)
            saved_s0 = None
            saved_ra = None
            if physical is not None:
                saved_s0 = memory.u64(physical)
                saved_ra = memory.u64(physical + 8)
            is_idle = state["pc"] == args.idle_pc
            frame_ok = live_s0 == ((sp + 16) & ((1 << 64) - 1))
            saved_s0_chain = saved_s0 == (
                (live_s0 + 16) & ((1 << 64) - 1))
            dtlb_match = (dtlb_physical is not None and
                          dtlb_physical == physical)
            passed = (is_idle and physical is not None and frame_ok and
                      saved_s0_chain and saved_ra == live_ra and dtlb_match)
            if is_idle:
                idle_total += 1
                idle_passed += int(passed)
            print(
                f"{cpu:02d} {state['pc']:#018x} {sp:#018x} "
                f"{hexadecimal(physical)} {hexadecimal(dtlb_physical)} "
                f"{hexadecimal(saved_s0)} {live_s0:#018x} "
                f"{hexadecimal(saved_ra)} {live_ra:#018x} "
                f"{'yes' if frame_ok else 'no'} "
                f"{'yes' if saved_s0_chain else 'no'} "
                f"{'yes' if dtlb_match else 'no'} "
                f"{'PASS' if passed else ('FAIL' if is_idle else 'n/a')}")
    finally:
        memory.close()
    print(f"idle_summary passed={idle_passed} total={idle_total}")
    return 0 if idle_total > 0 and idle_passed == idle_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
