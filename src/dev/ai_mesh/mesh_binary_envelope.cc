#include "dev/ai_mesh/mesh_binary_envelope.hh"

#include <algorithm>
#include <limits>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/mesh_hash.hh"

namespace gem5
{
namespace ai_mesh
{
namespace mesh_binary_detail
{

namespace
{

bool
fail(const char *code, const char *message, MeshLoadError &error)
{
    error = {code, message};
    return false;
}

bool
checkedDirectoryEnd(uint32_t count, uint64_t &end)
{
    const uint64_t bytes = static_cast<uint64_t>(count) *
                           mesh_abi::kSectionDirBytes;
    if (bytes > std::numeric_limits<uint64_t>::max() -
                    mesh_abi::kHeaderBytes)
        return false;
    end = mesh_abi::kHeaderBytes + bytes;
    return true;
}

uint64_t
alignEight(uint64_t value)
{
    return (value + 7) & ~uint64_t(7);
}

}

bool
validateMeshHeader(const MeshHeader &header, MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    if (header.abi_major != mesh_abi::kAbiMajor)
        return fail(E_ABI_VERSION, "ABI major is unsupported", error);
    if (header.abi_minor < mesh_abi::kMinReaderMinor)
        return fail(E_ABI_VERSION, "ABI minor is too old", error);
    if ((header.required_features & mesh_abi::kRequiredFeatures) !=
            mesh_abi::kRequiredFeatures ||
        (header.required_features & ~mesh_abi::kRequiredFeatures) != 0)
        return fail(E_ABI_VERSION, "required feature set is unsupported", error);
    return true;
}

uint32_t
meshCrc32(const uint8_t *data, size_t size)
{
    uint32_t crc = 0xffffffffu;
    for (size_t index = 0; index < size; ++index) {
        crc ^= data[index];
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

bool
decodeMeshEnvelope(
    const MeshBytes &image, DecodedEnvelope &out, MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    if (image.size() < mesh_abi::kHeaderBytes)
        return fail(E_ABI_SECTION_RANGE, "image is smaller than header", error);
    const uint8_t *header = image.data();
    if (mesh_abi::rdU64(header + mesh_abi::kHeaderMagicOffset) !=
        mesh_abi::kMagic)
        return fail(E_ABI_MAGIC, "binary magic is invalid", error);
    MeshHeader facts;
    facts.abi_major =
        mesh_abi::rdU16(header + mesh_abi::kHeaderAbiMajorOffset);
    facts.abi_minor =
        mesh_abi::rdU16(header + mesh_abi::kHeaderAbiMinorOffset);
    facts.required_features = mesh_abi::rdU64(
        header + mesh_abi::kHeaderRequiredFeaturesOffset);
    if (!validateMeshHeader(facts, error))
        return false;
    if (mesh_abi::rdU32(
            header + mesh_abi::kHeaderHeaderBytesOffset) !=
            mesh_abi::kHeaderBytes ||
        mesh_abi::rdU64(header + mesh_abi::kHeaderFileBytesOffset) !=
            image.size() ||
        mesh_abi::rdU64(
            header + mesh_abi::kHeaderSectionDirOffsetOffset) !=
            mesh_abi::kHeaderBytes)
        return fail(E_ABI_SECTION_RANGE, "header range fields are invalid", error);
    if (mesh_abi::rdU32(header + mesh_abi::kHeaderFlagsOffset) != 0)
        return fail(E_ABI_RESERVED, "header flags must be zero", error);
    if (std::any_of(
            header + mesh_abi::kHeaderReservedOffset,
            header + mesh_abi::kHeaderReservedOffset +
                mesh_abi::kHeaderReservedBytes,
                    [](uint8_t value) { return value != 0; }))
        return fail(E_ABI_RESERVED, "header reserved bytes must be zero", error);
    std::copy(
        header + mesh_abi::kHeaderArchDigestOffset,
        header + mesh_abi::kHeaderArchDigestOffset +
            mesh_abi::kHeaderArchDigestBytes,
        facts.arch_digest.begin());
    mesh_hash::Sha256 payloadHash;
    payloadHash.update(
        image.data() + mesh_abi::kHeaderBytes,
        image.size() - mesh_abi::kHeaderBytes);
    const auto payloadDigest = payloadHash.digest();
    if (!std::equal(
            payloadDigest.begin(), payloadDigest.end(),
            header + mesh_abi::kHeaderPayloadSha256Offset))
        return fail(E_ABI_CHECKSUM, "payload checksum is invalid", error);

    const uint32_t count = mesh_abi::rdU32(
        header + mesh_abi::kHeaderSectionCountOffset);
    uint64_t lastEnd = 0;
    if (!checkedDirectoryEnd(count, lastEnd) || lastEnd > image.size())
        return fail(E_ABI_SECTION_RANGE, "section directory is out of bounds", error);
    std::vector<MeshSectionView> sections;
    sections.reserve(count);
    uint16_t lastType = 0;
    for (uint32_t index = 0; index < count; ++index) {
        const uint64_t entryOffset = mesh_abi::kHeaderBytes +
            static_cast<uint64_t>(index) * mesh_abi::kSectionDirBytes;
        const uint8_t *entry = image.data() + entryOffset;
        MeshSectionView section;
        section.type = mesh_abi::rdU16(
            entry + mesh_abi::kSectionDirSectionTypeOffset);
        section.record_bytes = mesh_abi::rdU32(
            entry + mesh_abi::kSectionDirRecordBytesOffset);
        section.offset = mesh_abi::rdU64(
            entry + mesh_abi::kSectionDirOffsetOffset);
        section.size = mesh_abi::rdU64(
            entry + mesh_abi::kSectionDirSizeOffset);
        section.count = mesh_abi::rdU64(
            entry + mesh_abi::kSectionDirCountOffset);
        if (mesh_abi::rdU16(entry + mesh_abi::kSectionDirFlagsOffset) != 0 ||
            mesh_abi::rdU32(
                entry + mesh_abi::kSectionDirReservedOffset) != 0)
            return fail(E_ABI_RESERVED, "section reserved fields must be zero", error);
        if (!mesh_abi::validSectionType(section.type))
            return fail(E_ABI_ENUM, "section type is unknown", error);
        if (section.type <= lastType)
            return fail(E_ABI_ORDER, "section types must strictly increase", error);
        lastType = section.type;
        if (lastEnd > std::numeric_limits<uint64_t>::max() - 7 ||
            section.offset != alignEight(lastEnd) ||
            section.offset > image.size() || section.size > image.size() - section.offset)
            return fail(E_ABI_SECTION_RANGE, "section range is invalid", error);
        if (std::any_of(
                image.begin() + static_cast<ptrdiff_t>(lastEnd),
                image.begin() + static_cast<ptrdiff_t>(section.offset),
                [](uint8_t value) { return value != 0; }))
            return fail(E_ABI_CORRUPT, "alignment padding must be zero", error);
        if (section.record_bytes != 0 &&
            (section.count > std::numeric_limits<uint64_t>::max() /
                                 section.record_bytes ||
             section.count * section.record_bytes != section.size))
            return fail(E_ABI_SECTION_RANGE, "fixed section size is invalid", error);
        if (meshCrc32(image.data() + section.offset, section.size) !=
            mesh_abi::rdU32(entry + mesh_abi::kSectionDirCrc32Offset))
            return fail(E_ABI_CHECKSUM, "section checksum is invalid", error);
        sections.push_back(section);
        lastEnd = section.offset + section.size;
    }
    if (lastEnd != image.size())
        return fail(E_ABI_SECTION_RANGE, "sections do not cover the image", error);
    DecodedEnvelope candidate;
    candidate.header = facts;
    candidate.sections = std::move(sections);
    out = std::move(candidate);
    return true;
}

bool
encodeMeshEnvelope(
    const MeshHeader &header, const std::vector<MeshSection> &sections,
    MeshBytes &out, MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    if (!validateMeshHeader(header, error))
        return false;
    if (sections.size() > std::numeric_limits<uint32_t>::max())
        return fail(E_ABI_OVERFLOW, "section count exceeds uint32", error);
    uint64_t cursor = 0;
    if (!checkedDirectoryEnd(static_cast<uint32_t>(sections.size()), cursor))
        return fail(E_ABI_OVERFLOW, "section directory overflows", error);
    uint16_t lastType = 0;
    for (const auto &section : sections) {
        if (!mesh_abi::validSectionType(section.type))
            return fail(E_ABI_ENUM, "section type is unknown", error);
        if (section.type <= lastType)
            return fail(E_ABI_ORDER, "section types must strictly increase", error);
        lastType = section.type;
        if (section.record_bytes != 0 &&
            (section.count > std::numeric_limits<uint64_t>::max() /
                                 section.record_bytes ||
             section.count * section.record_bytes != section.payload.size()))
            return fail(E_ABI_SECTION_RANGE, "fixed section size is invalid", error);
        if (cursor > std::numeric_limits<uint64_t>::max() - 7)
            return fail(E_ABI_OVERFLOW, "section alignment overflows", error);
        cursor = alignEight(cursor);
        if (section.payload.size() > std::numeric_limits<uint64_t>::max() - cursor)
            return fail(E_ABI_OVERFLOW, "image size overflows", error);
        cursor += section.payload.size();
    }
    if (cursor > std::numeric_limits<size_t>::max())
        return fail(E_ABI_OVERFLOW, "image is too large", error);
    MeshBytes candidate(static_cast<size_t>(cursor), 0);
    mesh_abi::wrU64(
        candidate.data() + mesh_abi::kHeaderMagicOffset, mesh_abi::kMagic);
    mesh_abi::wrU16(
        candidate.data() + mesh_abi::kHeaderAbiMajorOffset,
        header.abi_major);
    mesh_abi::wrU16(
        candidate.data() + mesh_abi::kHeaderAbiMinorOffset,
        header.abi_minor);
    mesh_abi::wrU32(
        candidate.data() + mesh_abi::kHeaderHeaderBytesOffset,
        mesh_abi::kHeaderBytes);
    mesh_abi::wrU64(
        candidate.data() + mesh_abi::kHeaderFileBytesOffset,
        candidate.size());
    mesh_abi::wrU64(
        candidate.data() + mesh_abi::kHeaderSectionDirOffsetOffset,
        mesh_abi::kHeaderBytes);
    mesh_abi::wrU32(
        candidate.data() + mesh_abi::kHeaderSectionCountOffset,
        static_cast<uint32_t>(sections.size()));
    std::copy(
        header.arch_digest.begin(), header.arch_digest.end(),
        candidate.begin() + mesh_abi::kHeaderArchDigestOffset);
    mesh_abi::wrU64(
        candidate.data() + mesh_abi::kHeaderRequiredFeaturesOffset,
        header.required_features);

    cursor = mesh_abi::kHeaderBytes + sections.size() * mesh_abi::kSectionDirBytes;
    for (size_t index = 0; index < sections.size(); ++index) {
        const auto &section = sections[index];
        cursor = alignEight(cursor);
        uint8_t *entry = candidate.data() + mesh_abi::kHeaderBytes +
                         index * mesh_abi::kSectionDirBytes;
        mesh_abi::wrU16(
            entry + mesh_abi::kSectionDirSectionTypeOffset, section.type);
        mesh_abi::wrU32(
            entry + mesh_abi::kSectionDirRecordBytesOffset,
            section.record_bytes);
        mesh_abi::wrU64(
            entry + mesh_abi::kSectionDirOffsetOffset, cursor);
        mesh_abi::wrU64(
            entry + mesh_abi::kSectionDirSizeOffset,
            section.payload.size());
        mesh_abi::wrU64(
            entry + mesh_abi::kSectionDirCountOffset, section.count);
        mesh_abi::wrU32(
            entry + mesh_abi::kSectionDirCrc32Offset, meshCrc32(
            section.payload.data(), section.payload.size()));
        std::copy(section.payload.begin(), section.payload.end(),
                  candidate.begin() + static_cast<ptrdiff_t>(cursor));
        cursor += section.payload.size();
    }
    mesh_hash::Sha256 payloadHash;
    payloadHash.update(
        candidate.data() + mesh_abi::kHeaderBytes,
        candidate.size() - mesh_abi::kHeaderBytes);
    const auto digest = payloadHash.digest();
    std::copy(
        digest.begin(), digest.end(),
        candidate.begin() + mesh_abi::kHeaderPayloadSha256Offset);
    out = std::move(candidate);
    return true;
}

}
}
}
