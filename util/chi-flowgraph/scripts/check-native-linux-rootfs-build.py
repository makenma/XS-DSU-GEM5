#!/usr/bin/env python3
"""Validate built rootfs firmware bytes and load layout; never execute a guest."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess


def digest(path, offset=0, length=None):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        stream.seek(offset)
        while length is None or length:
            chunk = stream.read(1024 * 1024 if length is None else min(length, 1024 * 1024))
            if not chunk:
                assert length is None or length == 0, f'short input: {path}'
                break
            result.update(chunk)
            if length is not None:
                length -= len(chunk)
    return result.hexdigest()


def check(work, log_dir):
    prepared = work / 'native-rootfs-kernel-only-final'
    manifest = json.loads((prepared / 'rootfs-manifest.json').read_text())
    image = work / 'kernel-build/arch/riscv/boot/Image'
    firmware = work / 'opensbi-rootfs/platform/generic/firmware/fw_payload.elf'
    cpio = work / 'kernel-build/usr/initramfs_data.cpio'
    config = work / 'kernel-build/.config'
    started = json.loads((log_dir / 'build-started.json').read_text())
    for item in started['files'] + [started['coreSuiteFirmware']]:
        assert digest(Path(item['path'])) == item['sha256'], 'core-suite snapshot changed'
    lines = set(config.read_text().splitlines())
    for required in (prepared / 'kernel-rootfs.config').read_text().splitlines():
        assert required in lines, required
    assert f'CONFIG_INITRAMFS_SOURCE="{prepared}/initramfs.list"' in lines
    assert config.read_bytes() == (log_dir / 'kernel.config').read_bytes()

    sources = {}
    for line in (prepared / 'initramfs.list').read_text().splitlines():
        fields = line.split()
        if fields[0] == 'file':
            sources[fields[1].lstrip('/')] = Path(fields[2])
    # Check the actual uncompressed CPIO, including its embedded SquashFS bytes.
    entries = {}
    with cpio.open('rb') as stream:
        while True:
            header = stream.read(110)
            assert len(header) == 110 and header[:6] == b'070701', 'expected raw newc CPIO'
            values = [int(header[6 + n * 8:14 + n * 8], 16) for n in range(13)]
            size, namesize = values[6], values[11]
            name = stream.read(namesize)
            assert name.endswith(b'\0')
            name = name[:-1].decode()
            stream.seek((-stream.tell()) % 4, 1)
            if name == 'TRAILER!!!':
                break
            assert name not in entries, f'duplicate CPIO path: {name}'
            offset = stream.tell()
            entries[name] = {'offset': offset, 'bytes': size}
            if name in sources:
                source = sources[name]
                assert size == source.stat().st_size
                actual = digest(cpio, offset, size)
                assert actual == digest(source), f'CPIO source mismatch: {name}'
                entries[name]['sha256'] = actual
            stream.seek(size, 1)
            stream.seek((-stream.tell()) % 4, 1)
    assert sources.keys() <= entries.keys()

    ram_start = int(manifest['ramStart'], 0)
    ram_end = int(manifest['ramEndExclusive'], 0)
    dtb = int(manifest['dtbAddress'], 0)
    assert ram_start == 0x80000000 and ram_end - ram_start == 4 * 1024**3
    assert dtb + manifest['dtbReservedBytes'] == ram_end
    with image.open('rb') as stream:
        header = stream.read(64)
    assert header[48:56] == b'RISCV\0\0\0' and header[56:60] == b'RSC\x05'
    text_offset, image_memory_size, flags = struct.unpack_from('<QQQ', header, 8)
    assert text_offset == 0x200000 and flags & 1 == 0
    kernel_address = ram_start + text_offset
    assert image_memory_size >= image.stat().st_size > cpio.stat().st_size
    assert kernel_address + image_memory_size <= dtb, 'kernel overlaps DTB reservation'
    symbols = {}
    for line in subprocess.check_output(
            ['riscv64-linux-gnu-nm', '-n', str(work / 'kernel-build/vmlinux')], text=True).splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2] in ('_start', '__initramfs_start', '__initramfs_size'):
            symbols[fields[2]] = int(fields[0], 16)
    assert len(symbols) == 3, 'missing vmlinux initramfs symbols'
    cpio_offset = symbols['__initramfs_start'] - symbols['_start']
    size_offset = symbols['__initramfs_size'] - symbols['_start']
    assert 0 <= cpio_offset < size_offset < image.stat().st_size
    with image.open('rb') as stream:
        stream.seek(size_offset)
        embedded_size, = struct.unpack('<Q', stream.read(8))
    assert embedded_size == cpio.stat().st_size, 'Image initramfs size mismatch'
    assert cpio_offset + embedded_size <= image.stat().st_size
    assert digest(image, cpio_offset, embedded_size) == digest(cpio), 'Image initramfs bytes differ from checked CPIO'

    loads = []
    payload_verified = False
    with firmware.open('rb') as stream:
        header = stream.read(64)
        assert header[:6] == b'\x7fELF\x02\x01' and struct.unpack_from('<H', header, 18)[0] == 243
        entry, phoff = struct.unpack_from('<QQ', header, 24)
        phsize, phnum = struct.unpack_from('<HH', header, 54)
        assert entry == ram_start and phsize >= 56 and phnum > 0
        for index in range(phnum):
            stream.seek(phoff + index * phsize)
            kind, flags, offset, va, pa, filesz, memsz, align = struct.unpack('<IIQQQQQQ', stream.read(56))
            if kind != 1:
                continue
            assert filesz <= memsz and offset + filesz <= firmware.stat().st_size
            assert ram_start <= pa < dtb and pa + memsz <= dtb, 'PT_LOAD outside RAM or overlaps DTB'
            for previous in loads:
                assert pa + memsz <= int(previous['physicalStart'], 0) or pa >= int(previous['physicalEnd'], 0), 'overlapping PT_LOAD'
            loads.append({'physicalStart': hex(pa), 'physicalEnd': hex(pa + memsz),
                          'fileOffset': offset, 'fileBytes': filesz, 'memoryBytes': memsz, 'flags': flags})
            if pa <= kernel_address and kernel_address + image.stat().st_size <= pa + filesz:
                assert not payload_verified, 'duplicate kernel image mapping'
                payload_verified = digest(firmware, offset + kernel_address - pa, image.stat().st_size) == digest(image)
                assert payload_verified, 'firmware payload bytes differ from built Image'
    assert loads and payload_verified
    assert any(int(seg['physicalStart'], 0) <= entry < int(seg['physicalEnd'], 0) and seg['flags'] & 1 for seg in loads)
    artifacts = [{'path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size}
                 for path in (image, firmware, config, cpio, prepared / 'initramfs.list', prepared / 'rootfs.squashfs')]
    report = {'schemaVersion': 1, 'buildArtifactsVerified': True, 'guestExecuted': False,
              'coreSuiteSnapshot': started['coreSuiteSnapshot'], 'coreSuiteFirmwareUnchanged': True,
              'initramfsCompression': 'none', 'cpioFilesVerified': {name: entries[name] for name in sources},
              'artifacts': artifacts, 'firmwareEntry': hex(entry), 'kernelAddress': hex(kernel_address),
              'kernelMemoryBytes': image_memory_size, 'loadSegments': loads,
              'imageContainsExactCpio': True, 'imageCpioOffset': cpio_offset,
              'firmwareContainsExactImage': True, 'ramStart': hex(ram_start), 'ramEndExclusive': hex(ram_end),
              'dtbAddress': hex(dtb), 'dtbReservedBytes': manifest['dtbReservedBytes'],
              'scope': 'Host build artifacts, embedded initramfs/Image bytes and 4GiB load layout only; no guest rootfs boot or guest kernel make result.'}
    (log_dir / 'build-result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'buildArtifactsVerified': True, 'firmware': str(firmware),
                      'firmwareSha256': digest(firmware), 'imageBytes': image.stat().st_size,
                      'report': str(log_dir / 'build-result.json')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--log-dir', type=Path, required=True)
    args = parser.parse_args()
    check(args.work, args.log_dir)
