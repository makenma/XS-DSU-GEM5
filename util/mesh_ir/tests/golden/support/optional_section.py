"""Optional-section acceptance probes shared by the corpus test."""
import hashlib
import struct
import zlib


def rebuild_with_extra_section(blob: bytes, extra_type: int, payload: bytes,
                               record_bytes: int = 16) -> bytes:
    """Append one extra section to a golden .mshb.

    The ABI section directory is a flat array of 40-byte entries (no count
    prefix; the count lives in the header at offset 32).  All original
    section offsets shift by exactly the directory size delta.
    """
    blob = bytearray(blob)
    dir_off = struct.unpack_from("<Q", blob, 24)[0]          # always 128
    old_count = struct.unpack_from("<I", blob, 32)[0]
    old_dir_size = old_count * 40
    first_sec = struct.unpack_from("<Q", blob, dir_off + 8)[0]

    sections = []
    for i in range(old_count):
        e = dir_off + i * 40
        stype = struct.unpack_from("<H", blob, e)[0]
        rbytes = struct.unpack_from("<I", blob, e + 4)[0]
        off, size, cnt = struct.unpack_from("<QQQ", blob, e + 8)
        sections.append((stype, rbytes, off - first_sec, size, cnt))

    old_body = bytes(blob[first_sec:])
    extra_count = len(payload) // record_bytes if record_bytes else 0
    sections.append((extra_type, record_bytes,
                     len(old_body), len(payload), extra_count))
    sections.sort(key=lambda s: s[0])

    new_dir_size = len(sections) * 40
    new_first_sec = (128 + new_dir_size + 7) & ~7
    new_body = old_body + payload

    directory = b""
    for stype, rbytes, body_off, size, cnt in sections:
        directory += struct.pack(
            "<HHIQQQII", stype, 0, rbytes,
            new_first_sec + body_off, size, cnt,
            zlib.crc32(new_body[body_off : body_off + size]) & 0xFFFFFFFF, 0,
        )

    total = new_first_sec + len(new_body)
    header = struct.pack(
        "<QHHIQQII32s32sQ16s",
        0x010000004248534D, 1, 1, 128, total, 128, len(sections), 0,
        bytes(blob[40:72]), b"\x00" * 32, 0, b"\x00" * 16,
    )
    out = bytearray(header) + bytearray(directory) + bytearray(new_body)
    out[72:104] = hashlib.sha256(bytes(out[128:])).digest()
    return bytes(out)
