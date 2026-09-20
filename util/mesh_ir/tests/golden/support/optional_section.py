"""Optional-section acceptance probes shared by the corpus test."""
import hashlib
import struct
import zlib

from mesh_ir.generated import abi as A


HEADER_OFFSETS = {field["name"]: field["offset"] for field in A.HEADER_FIELDS}
DIRECTORY_OFFSETS = {
    field["name"]: field["offset"] for field in A.SECTION_DIR_FIELDS
}


def rebuild_with_extra_section(blob: bytes, extra_type: int, payload: bytes,
                               record_bytes: int = 16) -> bytes:
    """Append one extra section to a golden .mshb."""
    blob = bytearray(blob)
    dir_off = struct.unpack_from(
        "<Q", blob, HEADER_OFFSETS["section_dir_offset"]
    )[0]
    old_count = struct.unpack_from(
        "<I", blob, HEADER_OFFSETS["section_count"]
    )[0]

    sections = []
    for i in range(old_count):
        entry = dir_off + i * A.SECTION_DIR_BYTES
        section_type = struct.unpack_from(
            "<H", blob, entry + DIRECTORY_OFFSETS["section_type"]
        )[0]
        section_record_bytes = struct.unpack_from(
            "<I", blob, entry + DIRECTORY_OFFSETS["record_bytes"]
        )[0]
        offset = struct.unpack_from(
            "<Q", blob, entry + DIRECTORY_OFFSETS["offset"]
        )[0]
        size = struct.unpack_from(
            "<Q", blob, entry + DIRECTORY_OFFSETS["size"]
        )[0]
        count = struct.unpack_from(
            "<Q", blob, entry + DIRECTORY_OFFSETS["count"]
        )[0]
        sections.append(
            (section_type, section_record_bytes, bytes(blob[offset:offset + size]), count)
        )

    extra_count = len(payload) // record_bytes if record_bytes else 0
    sections.append((extra_type, record_bytes, payload, extra_count))
    sections.sort(key=lambda section: section[0])

    directory_end = A.HEADER_BYTES + len(sections) * A.SECTION_DIR_BYTES
    cursor = (directory_end + 7) & ~7
    directory = bytearray()
    body = bytearray(b"\x00" * (cursor - directory_end))
    for section_type, section_record_bytes, section_payload, count in sections:
        aligned = (cursor + 7) & ~7
        body.extend(b"\x00" * (aligned - cursor))
        cursor = aligned
        directory.extend(
            struct.pack(
                "<HHIQQQII", section_type, 0, section_record_bytes,
                cursor, len(section_payload), count,
                zlib.crc32(section_payload) & 0xFFFFFFFF, 0,
            )
        )
        body.extend(section_payload)
        cursor += len(section_payload)

    header = bytearray(blob[:A.HEADER_BYTES])
    struct.pack_into(
        "<Q", header, HEADER_OFFSETS["file_bytes"],
        A.HEADER_BYTES + len(directory) + len(body),
    )
    struct.pack_into(
        "<Q", header, HEADER_OFFSETS["section_dir_offset"], A.HEADER_BYTES
    )
    struct.pack_into(
        "<I", header, HEADER_OFFSETS["section_count"], len(sections)
    )
    out = header + directory + body
    payload_offset = HEADER_OFFSETS["payload_sha256"]
    out[payload_offset:payload_offset + 32] = hashlib.sha256(
        bytes(out[A.HEADER_BYTES:])
    ).digest()
    return bytes(out)
