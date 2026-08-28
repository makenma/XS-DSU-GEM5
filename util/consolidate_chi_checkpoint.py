#!/usr/bin/env python3
"""Consolidate serialized CHI SLC data into a checkpoint pmem image.

Older XS-GEM5 checkpoints serialized SLC/SF contents even though the classic
private caches were not serialized.  Restoring such a checkpoint directly is
unsafe: the SF can name private copies which no longer exist, while the pmem
file can be older than a valid serialized SLC line.

Current SlcSnoopFilter::unserialize() deliberately discards the saved SLC/SF
arrays.  This utility prepares an older checkpoint for that restore policy by
overlaying every valid serialized SLC line onto a copy of physical memory.
The source checkpoint is never modified.  The m5.cpt file is copied verbatim
so gem5 can validate the saved arrays before discarding them.
"""

import argparse
import gzip
import hashlib
import json
import pathlib
import shutil
import tempfile

from debug_chi_checkpoint import load_backend


PMEM_NAME = "system.physmem.store0.pmem"


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_pmem(path):
    with path.open("rb") as stream:
        magic = stream.read(2)
    opener = gzip.open if magic == b"\x1f\x8b" else open
    with opener(path, "rb") as stream:
        return bytearray(stream.read()), magic == b"\x1f\x8b"


def write_pmem(path, data):
    # An empty embedded filename plus mtime=0 makes the artifact reproducible.
    with path.open("wb") as raw_stream:
        with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw_stream,
                compresslevel=6, mtime=0) as stream:
            stream.write(data)


def consolidate(source, destination, pmem_base):
    source = source.resolve()
    destination = destination.resolve()
    source_cpt = source / "m5.cpt"
    source_pmem = source / PMEM_NAME
    if not source.is_dir():
        raise RuntimeError(f"source checkpoint is not a directory: {source}")
    for required in (source_cpt, source_pmem):
        if not required.is_file():
            raise RuntimeError(f"source checkpoint is missing {required.name}")
    if destination.exists():
        raise RuntimeError(f"destination already exists: {destination}")

    slc_lines, sf_lines = load_backend(source_cpt)
    if not slc_lines:
        raise RuntimeError("checkpoint contains no valid serialized SLC lines")

    memory, source_was_gzip = read_pmem(source_pmem)
    changed_lines = 0
    changed_bytes = 0
    for address, (_, _, _, data) in sorted(slc_lines.items()):
        offset = address - pmem_base
        if offset < 0 or offset + len(data) > len(memory):
            raise RuntimeError(
                f"SLC line {address:#x}+{len(data)} is outside pmem "
                f"[{pmem_base:#x}, {pmem_base + len(memory):#x})")
        old = memory[offset:offset + len(data)]
        if old != data:
            changed_lines += 1
            changed_bytes += sum(lhs != rhs for lhs, rhs in zip(old, data))
            memory[offset:offset + len(data)] = data

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = pathlib.Path(tempfile.mkdtemp(
        prefix=f".{destination.name}.tmp-", dir=destination.parent))
    try:
        output_cpt = temporary / "m5.cpt"
        output_pmem = temporary / PMEM_NAME
        shutil.copy2(source_cpt, output_cpt)
        write_pmem(output_pmem, memory)

        manifest = {
            "format": "xs-gem5-chi-pmem-consolidation-v1",
            "source_checkpoint": str(source),
            "pmem_base": hex(pmem_base),
            "pmem_size": len(memory),
            "source_pmem_was_gzip": source_was_gzip,
            "serialized_slc_lines": len(slc_lines),
            "serialized_sf_lines": len(sf_lines),
            "changed_slc_lines": changed_lines,
            "changed_bytes": changed_bytes,
            "source_m5_cpt_sha256": sha256_file(source_cpt),
            "source_pmem_sha256": sha256_file(source_pmem),
            "output_m5_cpt_sha256": sha256_file(output_cpt),
            "output_pmem_sha256": sha256_file(output_pmem),
            "output_uncompressed_pmem_sha256": hashlib.sha256(memory).hexdigest(),
        }
        with (temporary / "consolidation.json").open(
                "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, sort_keys=True)
            stream.write("\n")
        temporary.rename(destination)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(
        description="copy a CHI checkpoint and overlay serialized SLC data "
                    "onto its physical-memory image")
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("destination", type=pathlib.Path)
    parser.add_argument(
        "--pmem-base", type=lambda value: int(value, 0), default=0x80000000)
    args = parser.parse_args()

    manifest = consolidate(args.source, args.destination, args.pmem_base)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
