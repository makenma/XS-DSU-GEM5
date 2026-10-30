#!/usr/bin/env python3
"""Prepare a serial-login initramfs in a verified local OpenSBI ELF copy.

The original firmware is never modified. No guest program is executed and
device nodes are encoded directly in newc, so root privileges are unnecessary.
This tool intentionally accepts only the known firmware SHA256 and layout.
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import stat
import struct
import zlib


SOURCE = Path("/tmp/opensbi-build-linux-128mb-blacklist/platform/generic/firmware/fw_payload.elf")
SOURCE_SHA256 = "bf6c8f01809ea8e3a685e80d2c78ad1169113d066460343f16a718a9a6b1aa8d"
INITRAMFS_OFFSET = 0x83E3F0
INITRAMFS_SIZE = 2009624
PAYLOAD_OFFSET = 0x32000
PAYLOAD_ADDRESS = 0x80200000
DTB_ADDRESS = 0x8001B000
ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def elf_programs(data):
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    if header[0][:6] != b"\x7fELF\x02\x01" or header[2] != 243:
        raise ValueError("Expected little-endian ELF64 for RISC-V")
    if header[9] != 56:
        raise ValueError("Unexpected ELF program-header size")
    return [struct.unpack_from("<IIQQQQQQ", data, header[5] + i * header[9])
            for i in range(header[10])]


def elf_dependencies(data):
    """Extract PT_INTERP and DT_NEEDED without executing a guest binary."""
    programs = elf_programs(data)
    needed, string_address, interpreter = [], None, None
    for kind, flags, offset, vaddr, paddr, filesz, memsz, align in programs:
        if kind == 3:
            interpreter = data[offset:offset + filesz].rstrip(b"\0").decode()
        if kind == 2:
            for at in range(offset, offset + filesz, 16):
                tag, value = struct.unpack_from("<qQ", data, at)
                if tag == 0:
                    break
                if tag == 1:
                    needed.append(value)
                elif tag == 5:
                    string_address = value
    libraries = []
    if needed:
        strings = next((offset + string_address - vaddr
                        for kind, flags, offset, vaddr, paddr, filesz, memsz, align in programs
                        if kind == 1 and vaddr <= string_address < vaddr + filesz), None)
        if strings is None:
            raise ValueError("ELF dynamic string table is not in a load segment")
        for relative in needed:
            start = strings + relative
            libraries.append(data[start:data.index(b"\0", start)].decode())
    return interpreter, libraries


def unpack_newc(data):
    entries, at = {}, 0
    while True:
        if data[at:at + 6] != b"070701":
            raise ValueError(f"Invalid newc header at {at:#x}")
        fields = [int(data[at + 6 + 8 * i:at + 14 + 8 * i], 16)
                  for i in range(13)]
        name_end = at + 110 + fields[11]
        if data[name_end - 1] != 0:
            raise ValueError("Unterminated newc name")
        name = data[at + 110:name_end - 1].decode()
        start = (name_end + 3) & ~3
        end = start + fields[6]
        if end > len(data):
            raise ValueError("Truncated newc entry")
        if name == "TRAILER!!!":
            break
        if name in entries or name.startswith("/") or ".." in Path(name).parts:
            raise ValueError(f"Unsafe/duplicate newc name: {name}")
        entries[name] = {"mode": fields[1], "data": data[start:end],
                         "major": fields[9], "minor": fields[10]}
        at = (end + 3) & ~3
    return entries


def pack_newc(entries):
    archive = bytearray()
    names = sorted(entries, key=lambda name: (len(Path(name).parts), name))
    for ino, name in enumerate(names + ["TRAILER!!!"], 1):
        entry = entries.get(name, {"mode": 0, "data": b""})
        payload = entry["data"]
        encoded = name.encode() + b"\0"
        fields = [ino, entry["mode"], 0, 0,
                  2 if stat.S_ISDIR(entry["mode"]) else 1, 0,
                  len(payload), 0, 0, entry.get("major", 0),
                  entry.get("minor", 0), len(encoded), 0]
        archive += b"070701" + b"".join(f"{value:08x}".encode() for value in fields)
        archive += encoded
        archive += b"\0" * (-len(archive) % 4)
        archive += payload
        archive += b"\0" * (-len(archive) % 4)
    archive += b"\0" * (-len(archive) % 512)
    return bytes(archive)


def make_rootfs(original):
    entries = {}
    for directory in ("bin", "sbin", "etc", "etc/init.d", "dev", "dev/pts",
                      "lib", "proc", "sys", "tmp", "root", "run", "var",
                      "var/log", "usr", "usr/bin", "usr/sbin", "usr/share"):
        mode = 0o1777 if directory == "tmp" else (0o700 if directory == "root" else 0o755)
        entries[directory] = {"mode": stat.S_IFDIR | mode, "data": b""}

    # This BusyBox is dynamically linked. Preserve its exact binary and every
    # recursively required library/interpreter from the source archive.
    todo, preserved = ["bin/busybox"], []
    while todo:
        name = todo.pop()
        if name in entries:
            continue
        if name not in original or not stat.S_ISREG(original[name]["mode"]):
            raise ValueError(f"Required ELF is missing from the source archive: {name}")
        entries[name] = original[name].copy()
        preserved.append(name)
        interpreter, libraries = elf_dependencies(entries[name]["data"])
        if interpreter:
            todo.append(interpreter.lstrip("/"))
        todo.extend("lib/" + library for library in libraries)

    busybox = entries["bin/busybox"]["data"]
    for applet in ("init", "getty", "login", "sh", "mount", "hostname"):
        if applet.encode() + b"\0" not in busybox:
            raise ValueError(f"Expected BusyBox applet string is missing: {applet}")
    version = re.search(rb"BusyBox v[0-9.]+", busybox).group().decode()
    notice_start = busybox.index(b"BusyBox is copyrighted")
    notice_end = busybox.index(b"\n\n", notice_start)
    notice = busybox[notice_start:notice_end].decode()
    attribution = (f"{version}\n{notice}\n\n"
                   "The BusyBox executable and its runtime libraries are copied unchanged\n"
                   "from the original local firmware identified by manifest.json.\n"
                   "No BusyBox or glibc source code is modified by this tool.\n"
                   "glibc runtime libraries retain their embedded copyright notices.\n"
                   "Upstream projects: https://busybox.net/ and https://www.gnu.org/software/libc/\n")

    def regular(name, text, mode=0o644):
        entries[name] = {"mode": stat.S_IFREG | mode, "data": text.encode()}

    regular("init", "#!/bin/sh\nexport PATH=/sbin:/bin:/usr/sbin:/usr/bin\nexec /bin/busybox init\n", 0o755)
    regular("etc/init.d/rcS", "#!/bin/sh\n"
            "/bin/busybox mount -t proc proc /proc\n"
            "/bin/busybox mount -t sysfs sysfs /sys\n"
            "/bin/busybox mount -t devpts devpts /dev/pts\n"
            "/bin/busybox hostname gem5-chi\n", 0o755)
    regular("etc/inittab", "::sysinit:/etc/init.d/rcS\n"
            "ttyS0::respawn:/sbin/getty -L ttyS0 115200 vt100\n"
            "::ctrlaltdel:/bin/busybox reboot\n"
            "::shutdown:/bin/busybox umount -a -r\n")
    regular("etc/hostname", "gem5-chi\n")
    regular("etc/issue", "gem5-chi RISC-V Linux (simulated serial console)\n\n")
    regular("etc/passwd", "root::0:0:root:/root:/bin/sh\n")
    regular("etc/group", "root:x:0:\n")
    regular("etc/shadow", "root::0:0:99999:7:::\n", 0o600)
    regular("etc/securetty", "ttyS0\nconsole\n")
    regular("etc/shells", "/bin/sh\n")
    regular("etc/nsswitch.conf", "passwd: files\ngroup: files\nshadow: files\nhosts: files\n")
    regular("etc/profile", "export PATH=/sbin:/bin:/usr/sbin:/usr/bin\nexport PS1='gem5-chi:\\w# '\n")
    regular("usr/share/CHI-IMAGE-NOTICES", attribution)
    for name in ("bin/sh", "bin/mount", "bin/umount", "bin/hostname", "bin/cat",
                 "bin/ls", "bin/echo", "bin/sleep", "bin/dmesg", "bin/uname",
                 "bin/login", "sbin/init", "sbin/getty", "sbin/reboot",
                 "sbin/poweroff", "usr/bin/id"):
        entries[name] = {"mode": stat.S_IFLNK | 0o777, "data": b"/bin/busybox"}
    for name, major, minor in (("console", 5, 1), ("null", 1, 3), ("ttyS0", 4, 64),
                               ("tty", 5, 0), ("zero", 1, 5)):
        entries["dev/" + name] = {"mode": stat.S_IFCHR | 0o600, "data": b"",
                                 "major": major, "minor": minor}
    return entries, sorted(preserved), attribution


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--out", type=Path, default=ROOT / "m5out/linux-chi-mesh/assets")
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.out.resolve()
    if source == output / "fw_payload.elf":
        parser.error("Output must not overwrite the source ELF")
    firmware = source.read_bytes()
    if digest(firmware) != SOURCE_SHA256:
        parser.error("Source SHA256 differs from the verified firmware; refusing to patch")
    programs = elf_programs(firmware)
    if not any(p[0] == 1 and p[2] == PAYLOAD_OFFSET and p[4] == PAYLOAD_ADDRESS
               and p[2] <= INITRAMFS_OFFSET
               and INITRAMFS_OFFSET + INITRAMFS_SIZE + 8 <= p[2] + p[5]
               for p in programs):
        parser.error("Verified kernel payload segment is missing")
    size_offset = INITRAMFS_OFFSET + INITRAMFS_SIZE
    if struct.unpack_from("<Q", firmware, size_offset)[0] != INITRAMFS_SIZE:
        parser.error("Original fixed-position initramfs size does not match")
    compressed = firmware[INITRAMFS_OFFSET:size_offset]
    if compressed[:10] != bytes.fromhex("1f8b0800000000000203"):
        parser.error("Original gzip header does not match")
    decoder = zlib.decompressobj(31)
    original_cpio = decoder.decompress(compressed) + decoder.flush()
    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        parser.error("Original gzip stream has an unexpected length")
    original = unpack_newc(original_cpio)
    entries, preserved, attribution = make_rootfs(original)
    archive = pack_newc(entries)
    replacement = gzip.compress(archive, compresslevel=9, mtime=0)
    # gzip's OS byte is platform dependent; fix it to Unix for reproducibility.
    replacement = replacement[:9] + b"\x03" + replacement[10:]
    if len(replacement) > INITRAMFS_SIZE:
        parser.error(f"New initramfs ({len(replacement)} bytes) exceeds the original slot")
    normalized = {name: {**entry, "major": entry.get("major", 0),
                         "minor": entry.get("minor", 0)} for name, entry in entries.items()}
    if unpack_newc(gzip.decompress(replacement)) != normalized:
        raise ValueError("New initramfs failed its round-trip verification")
    patched = bytearray(firmware)
    patched[INITRAMFS_OFFSET:size_offset] = replacement + b"\0" * (INITRAMFS_SIZE - len(replacement))
    struct.pack_into("<Q", patched, size_offset, len(replacement))
    assert patched[:INITRAMFS_OFFSET] == firmware[:INITRAMFS_OFFSET]
    assert patched[size_offset + 8:] == firmware[size_offset + 8:]
    output.mkdir(parents=True, exist_ok=True)
    (output / "fw_payload.elf").write_bytes(patched)
    (output / "initramfs.cpio.gz").write_bytes(replacement)
    (output / "IMAGE-NOTICES.txt").write_text(attribution)
    manifest = {
        "source": str(source), "source_sha256": SOURCE_SHA256,
        "output": str(output / "fw_payload.elf"), "output_sha256": digest(patched),
        "source_unchanged": digest(source.read_bytes()) == SOURCE_SHA256,
        "elf_size": len(firmware), "payload_file_offset": PAYLOAD_OFFSET,
        "payload_physical_address": PAYLOAD_ADDRESS,
        "initramfs_file_offset": INITRAMFS_OFFSET,
        "initramfs_size_word_file_offset": size_offset,
        "original_gzip_size": INITRAMFS_SIZE, "replacement_gzip_size": len(replacement),
        "replacement_cpio_size": len(archive), "replacement_gzip_sha256": digest(replacement),
        "dtb_override_physical_address": DTB_ADDRESS,
        "required_bootargs": "console=ttyS0,115200 rdinit=/init",
        "login": {"terminal": "ttyS0", "baud": 115200, "hostname": "gem5-chi",
                  "user": "root", "password": "", "network_services": []},
        "preserved_binaries": {name: {"bytes": len(entries[name]["data"]),
                                      "sha256": digest(entries[name]["data"])}
                               for name in preserved},
        "entries": {name: {"mode": oct(entry["mode"]), "bytes": len(entry["data"]),
                           "sha256": digest(entry["data"]), "major": entry.get("major", 0),
                           "minor": entry.get("minor", 0)} for name, entry in sorted(entries.items())},
        "tool_sha256": digest(Path(__file__).read_bytes()),
        "validation": "ELF identity/layout, original gzip/size word, dependency closure, newc round trip, patch bounds; not booted",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Prepared {output / 'fw_payload.elf'}")
    print(f"initramfs: {INITRAMFS_SIZE} -> {len(replacement)} bytes; source unchanged")


if __name__ == "__main__":
    main()
