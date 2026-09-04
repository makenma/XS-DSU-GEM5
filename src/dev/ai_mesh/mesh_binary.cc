#include "dev/ai_mesh/mesh_binary.hh"

#include <cstring>

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5
{
namespace ai_mesh
{

namespace mesh_abi
{
// The generated header only carries constants; the fixed reader below uses
// them so Python and C++ literally share one schema source.

using namespace ::gem5::ai_mesh::mesh_abi;
}

namespace
{
bool fail(const char *code, const char *message, MeshLoadError &error)
{
    error.code = code;
    error.message = message;
    return false;
}

struct SectionEntry
{
    uint16_t type = 0;
    uint64_t offset = 0;
    uint64_t size = 0;
    uint64_t count = 0;
    uint32_t record_bytes = 0;
};

uint32_t crc32Compute(const uint8_t *data, size_t size)
{
    // IEEE CRC-32 (zlib-compatible), matching the Python encoder.
    static uint32_t table[256];
    static bool initialized = false;
    if (!initialized) {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++)
                c = (c & 1) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
            table[i] = c;
        }
        initialized = true;
    }
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < size; i++)
        crc = table[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);
    return (crc ^ 0xFFFFFFFFu) & 0xFFFFFFFFu;
}

bool crc32Check(const uint8_t *data, size_t size, uint32_t expected)
{
    return crc32Compute(data, size) == expected;
}

// Minimal SHA-256 matching hashlib.sha256 over the payload span.
struct Sha256
{
    uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                     0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    uint8_t buffer[64]{};
    size_t buffered = 0;
    uint64_t total = 0;

    static uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

    void block(const uint8_t *p)
    {
        uint32_t w[64];
        for (int i = 0; i < 16; i++) {
            // SHA-256 reads message words big-endian.
            w[i] = (uint32_t(p[4 * i]) << 24) | (uint32_t(p[4 * i + 1]) << 16) |
                   (uint32_t(p[4 * i + 2]) << 8) | uint32_t(p[4 * i + 3]);
        }
        for (int i = 16; i < 64; i++) {
            uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
            uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        uint32_t a = h[0], b = h[1], c = h[2], d = h[3];
        uint32_t e = h[4], f = h[5], g = h[6], hh = h[7];
        static const uint32_t k[64] = {
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
            0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
            0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
            0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
            0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
            0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
            0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
            0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
            0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
            0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
            0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
        for (int i = 0; i < 64; i++) {
            uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            uint32_t ch = (e & f) ^ (~e & g);
            uint32_t t1 = hh + S1 + ch + k[i] + w[i];
            uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
            uint32_t t2 = S0 + maj;
            hh = g; g = f; f = e; e = d + t1;
            d = c; c = b; b = a; a = t1 + t2;
        }
        h[0] += a; h[1] += b; h[2] += c; h[3] += d;
        h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }

    void update(const uint8_t *data, size_t size)
    {
        total += size;
        while (size > 0) {
            size_t take = std::min(size, sizeof(buffer) - buffered);
            std::memcpy(buffer + buffered, data, take);
            buffered += take;
            data += take;
            size -= take;
            if (buffered == sizeof(buffer)) {
                block(buffer);
                buffered = 0;
            }
        }
    }

    void final(uint8_t out[32])
    {
        uint64_t bits = total * 8;
        uint8_t pad = 0x80;
        update(&pad, 1);
        uint8_t zero = 0;
        while (buffered != 56)
            update(&zero, 1);
        uint8_t len[8];
        for (int i = 0; i < 8; i++)
            len[i] = static_cast<uint8_t>(bits >> (8 * (7 - i)));
        update(len, 8);
        for (int i = 0; i < 8; i++) {
            out[4 * i + 0] = static_cast<uint8_t>(h[i] >> 24);
            out[4 * i + 1] = static_cast<uint8_t>(h[i] >> 16);
            out[4 * i + 2] = static_cast<uint8_t>(h[i] >> 8);
            out[4 * i + 3] = static_cast<uint8_t>(h[i]);
        }
    }
};

} // anonymous namespace

bool decodeMeshBinary(const MeshBytes &image, DecodedProgram &out, MeshLoadError &error)
{
    // Transactional decode: a failure leaves the caller's program intact.
    DecodedProgram decoded;
    if (decodeMeshBinaryInto(image, decoded, error)) {
        out = std::move(decoded);
        return true;
    }
    return false;
}

bool decodeMeshBinaryInto(const MeshBytes &image, DecodedProgram &out,
                          MeshLoadError &error)
{
    using namespace mesh_abi;
    if (image.size() < kHeaderBytes)
        return fail("E_ABI_SECTION_RANGE", "file smaller than header", error);
    const uint8_t *base = image.data();

    if (rdU64(base + 0) != kMagic)
        return fail("E_ABI_MAGIC", "bad magic", error);
    if (rdU16(base + 8) != kAbiMajor)
        return fail("E_ABI_VERSION", "abi major mismatch", error);
    uint16_t minor = rdU16(base + 10);
    if (minor < kMinReaderMinor || minor > kAbiMinor)
        return fail("E_ABI_VERSION", "abi minor out of range", error);
    if (rdU32(base + 12) != kHeaderBytes)
        return fail("E_ABI_SECTION_RANGE", "header_bytes mismatch", error);
    if (rdU64(base + 16) != image.size())
        return fail("E_ABI_SECTION_RANGE", "file_bytes mismatch", error);
    if (rdU32(base + 36) != 0)
        return fail("E_ABI_RESERVED", "header flags must be zero", error);
    if (rdU64(base + 104) != 0)
        return fail("E_ABI_VERSION", "unknown required feature bits", error);
    for (int i = 112; i < 128; i++)
        if (base[i] != 0)
            return fail("E_ABI_RESERVED", "header reserved must be zero", error);

    Sha256 sha;
    sha.update(base + kHeaderBytes, image.size() - kHeaderBytes);
    uint8_t digest[32];
    sha.final(digest);
    if (std::memcmp(digest, base + 72, 32) != 0)
        return fail("E_ABI_CHECKSUM", "payload sha256 mismatch", error);

    static char hex[] = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        out.arch_digest_hex += hex[base[40 + i] >> 4];
        out.arch_digest_hex += hex[base[40 + i] & 0xF];
    }
    out.abi_major = rdU16(base + 8);
    out.abi_minor = minor;

    uint64_t dir_offset = rdU64(base + 24);
    uint32_t section_count = rdU32(base + 32);
    if (dir_offset != kHeaderBytes)
        return fail("E_ABI_SECTION_RANGE", "section directory offset", error);
    // Bound the directory itself before any entry is dereferenced.
    if (uint64_t(section_count) * kSectionDirBytes > image.size() - kHeaderBytes)
        return fail("E_ABI_SECTION_RANGE", "section directory exceeds file", error);
    constexpr uint32_t kSectionCountMax = 15 + 3; // required + optional set
    if (section_count < 15 || section_count > kSectionCountMax)
        return fail("E_ABI_SECTION_RANGE", "unexpected section count", error);

    SectionEntry strings_entry;
    bool has_strings = false;
    std::vector<SectionEntry> entries;
    uint16_t last_type = 0;
    uint64_t last_end = dir_offset + uint64_t(section_count) * kSectionDirBytes;
    for (uint32_t i = 0; i < section_count; i++) {
        const uint8_t *e = base + dir_offset + i * kSectionDirBytes;
        SectionEntry entry;
        entry.type = rdU16(e + 0);
        if (rdU16(e + 2) != 0 || rdU32(e + 36) != 0)
            return fail("E_ABI_RESERVED", "section dir flags/reserved", error);
        {
            static const uint16_t known[] = {
                1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
                101, 102, 103,
            };
            bool type_known = false;
            for (uint16_t value : known)
                if (entry.type == value)
                    type_known = true;
            if (!type_known)
                return fail("E_ABI_ENUM", "unknown section type", error);
        }
        entry.record_bytes = rdU32(e + 4);
        entry.offset = rdU64(e + 8);
        entry.size = rdU64(e + 16);
        entry.count = rdU64(e + 24);
        if (entry.type <= last_type)
            return fail("E_ABI_ORDER", "section types must increase", error);
        last_type = entry.type;
        if (entry.offset % 8 != 0)
            return fail("E_ABI_SECTION_RANGE", "section not 8-aligned", error);
        if (entry.offset < last_end || entry.offset > image.size() ||
            entry.size > image.size() - entry.offset)
            return fail("E_ABI_SECTION_RANGE", "section out of bounds", error);
        for (uint64_t g = last_end; g < entry.offset; g++)
            if (base[g] != 0)
                return fail("E_ABI_CORRUPT", "padding must be zero", error);
        const uint8_t *payload = base + entry.offset;
        if (!crc32Check(payload, entry.size, rdU32(e + 32)))
            return fail("E_ABI_CHECKSUM", "section crc32 mismatch", error);
        if (entry.record_bytes != 0) {
            if (entry.count > image.size() / entry.record_bytes ||
                entry.size != entry.count * entry.record_bytes)
                return fail("E_ABI_SECTION_RANGE", "fixed table size mismatch", error);
        }
        if (entry.record_bytes == 0 && entry.type == kSectionTypeSTRINGS &&
            entry.size < 4)
            return fail("E_ABI_SECTION_RANGE", "blob section smaller than its "
                        "directory header", error);
        if (entry.type == kSectionTypeSTRINGS)
            strings_entry = entry, has_strings = true;
        entries.push_back(entry);
        last_end = entry.offset + entry.size;
    }
    if (last_end != image.size())
        return fail("E_ABI_SECTION_RANGE", "trailing bytes", error);
    if (!has_strings)
        return fail("E_ABI_SECTION_RANGE", "missing STRINGS section", error);

    // STRINGS blob (ABI SSOT: encoding utf-8)
    {
        // Strict well-formed UTF-8, matching Python bytes.decode('utf-8'):
        // rejects overlong encodings, surrogates and code points above
        // U+10FFFFFF range guards.
        auto valid_utf8 = [](const uint8_t *s, uint32_t len) -> bool {
            uint32_t i = 0;
            while (i < len) {
                const uint8_t lead = s[i];
                if (lead < 0x80) {
                    i++;
                    continue;
                }
                uint32_t need = 0;
                uint8_t cont_low = 0x80, cont_high = 0xBF;
                uint8_t first_low = 0, first_high = 0;
                if (lead >= 0xC2 && lead <= 0xDF) {
                    need = 1;
                    first_low = 0x80;
                    first_high = 0xBF;
                } else if (lead == 0xE0) {
                    need = 2;
                    first_low = 0xA0; // reject overlong
                    first_high = 0xBF;
                } else if ((lead >= 0xE1 && lead <= 0xEC) || lead == 0xEE ||
                           lead == 0xEF) {
                    need = 2;
                    first_low = 0x80;
                    first_high = 0xBF;
                } else if (lead == 0xED) {
                    need = 2;
                    first_low = 0x80; // reject surrogates
                    first_high = 0x9F;
                } else if (lead == 0xF0) {
                    need = 3;
                    first_low = 0x90; // reject overlong
                    first_high = 0xBF;
                } else if (lead >= 0xF1 && lead <= 0xF3) {
                    need = 3;
                    first_low = 0x80;
                    first_high = 0xBF;
                } else if (lead == 0xF4) {
                    need = 3;
                    first_low = 0x80; // reject above U+10FFFF
                    first_high = 0x8F;
                } else {
                    return false; // C0/C1/F5-FF continuation leads
                }
                if (uint64_t(i) + need > uint64_t(len) - 1)
                    return false;
                if (s[i + 1] < first_low || s[i + 1] > first_high)
                    return false;
                for (uint32_t k = 2; k <= need; k++) {
                    const uint8_t cont = s[i + k];
                    if (cont < cont_low || cont > cont_high)
                        return false;
                }
                i += need + 1;
            }
            return true;
        };
        if (strings_entry.record_bytes != 0)
            return fail("E_ABI_SECTION_RANGE",
                        "STRINGS is a blob section and must declare "
                        "record_bytes 0", error);
        if (strings_entry.count != rdU32(base + strings_entry.offset))
            return fail("E_ABI_SECTION_RANGE",
                        "STRINGS outer count != inner directory count",
                        error);
        const uint8_t *p = base + strings_entry.offset;
        uint32_t count = rdU32(p);
        uint64_t dir_span = 4 + uint64_t(count) * 8;
        if (dir_span > strings_entry.size)
            return fail("E_ABI_SECTION_RANGE", "string directory exceeds "
                        "section", error);
        const uint8_t *blob = p + dir_span;
        uint64_t blob_size = strings_entry.size - dir_span;
        uint64_t previous_end = 0;
        for (uint32_t i = 0; i < count; i++) {
            uint32_t off = rdU32(p + 4 + i * 8);
            uint32_t len = rdU32(p + 4 + i * 8 + 4);
            if (off != previous_end)
                return fail("E_ABI_ORDER", "string offsets must be dense", error);
            if (uint64_t(off) + len > blob_size)
                return fail("E_ABI_SECTION_RANGE", "string exceeds blob", error);
            if (!valid_utf8(blob + off, len))
                return fail("E_ABI_CORRUPT", "string not valid utf-8", error);
            out.strings.emplace_back(reinterpret_cast<const char *>(blob + off), len);
            previous_end = off + len;
        }
        if (previous_end != blob_size)
            return fail("E_ABI_SECTION_RANGE",
                        "STRINGS data does not cover the section exactly",
                        error);
    }

    auto find_section = [&](uint16_t type, SectionEntry &entry) -> bool {
        for (const auto &candidate : entries)
            if (candidate.type == type) {
                entry = candidate;
                return true;
            }
        return false;
    };

    auto table = [&](uint16_t type, uint32_t record_bytes) -> const uint8_t * {
        SectionEntry entry;
        if (!find_section(type, entry) || entry.record_bytes != record_bytes)
            return nullptr;
        return base + entry.offset;
    };

    const uint8_t *cmds = table(kSectionTypeCOMMANDS, kCommandsBytes);
    const uint8_t *waits = table(kSectionTypeCOMMAND_WAITS, kCommandWaitsBytes);
    const uint8_t *ops = table(kSectionTypeCOMMAND_OPERANDS, kCommandOperandsBytes);
    const uint8_t *evts = table(kSectionTypeEVENTS, kEventsBytes);
    const uint8_t *dmas = table(kSectionTypeDMA_DESCRIPTORS, kDmaDescriptorsBytes);
    const uint8_t *attrs = table(kSectionTypeOP_ATTRS, kOpAttrsBytes);
    const uint8_t *streams = table(kSectionTypeSTREAMS, kStreamsBytes);
    const uint8_t *allocs = table(kSectionTypeALLOCATIONS, kAllocationsBytes);
    const uint8_t *traffic = table(kSectionTypeEXPECTED_TRAFFIC, kExpectedTrafficBytes);
    const uint8_t *entrypoints = table(kSectionTypeENTRYPOINTS, kEntrypointsBytes);
    if (!cmds || !waits || !ops || !evts || !dmas || !attrs || !streams || !allocs || !traffic || !entrypoints)
        return fail("E_ABI_SECTION_RANGE", "missing required section", error);
    // Every fixed-record section must carry its ABI-constant record size
    // before any record is decoded (mirrors the Python decoder).
    const std::pair<uint16_t, uint32_t> fixed_sections[] = {
        {kSectionTypePROFILES, kProfilesBytes},
        {kSectionTypeTENSORS, kTensorsBytes},
        {kSectionTypeSHARDS, kShardsBytes},
        {kSectionTypeRELOCATIONS, kRelocationsBytes},
        {kSectionTypeSOURCE_MAP, kSourceMapBytes},
        {kSectionTypeCONTENT_DIGESTS, kContentDigestsBytes},
    };
    for (const auto &fixed : fixed_sections) {
        SectionEntry entry;
        if (!find_section(fixed.first, entry))
            continue; // optional section
        if (entry.record_bytes != fixed.second)
            return fail("E_ABI_SECTION_RANGE",
                        "fixed-record section has wrong record size", error);
    }
    for (uint16_t required : {kSectionTypePROFILES, kSectionTypeTENSORS, kSectionTypeSHARDS,
                              kSectionTypeRELOCATIONS}) {
        SectionEntry entry;
        if (!find_section(required, entry))
            return fail("E_ABI_SECTION_RANGE", "missing required section", error);
    }

    for (const auto &entry : entries) {
        // Blob sections (record_bytes == 0, e.g. STRINGS) carry no records;
        // their directory count is validated inside the blob decoder.
        if (entry.record_bytes == 0)
            continue;
        const uint8_t *p = base + entry.offset;
        auto abi_fail = [&](const mesh_abi::AbiError &abi_error) {
            return fail(abi_error.code, abi_error.message, error);
        };
        for (uint64_t i = 0; i < entry.count; i++) {
            const uint8_t *r = p + i * entry.record_bytes;
            mesh_abi::AbiError abi_error;
            bool ok = false;
            switch (entry.type) {
            case kSectionTypeENTRYPOINTS: {
                out.entrypoints.emplace_back();
                ok = mesh_abi::decodeEntrypoint(r, out.entrypoints.back(),
                                                abi_error);
                break;
            }
            case kSectionTypePROFILES: {
                out.profiles.emplace_back();
                ok = mesh_abi::decodeProfile(r, out.profiles.back(), abi_error);
                break;
            }
            case kSectionTypeTENSORS: {
                out.tensors.emplace_back();
                ok = mesh_abi::decodeTensor(r, out.tensors.back(), abi_error);
                break;
            }
            case kSectionTypeSHARDS: {
                out.shards.emplace_back();
                ok = mesh_abi::decodeShard(r, out.shards.back(), abi_error);
                break;
            }
            case kSectionTypeALLOCATIONS: {
                out.allocations.emplace_back();
                ok = mesh_abi::decodeAllocation(r, out.allocations.back(),
                                                abi_error);
                break;
            }
            case kSectionTypeSTREAMS: {
                out.streams.emplace_back();
                ok = mesh_abi::decodeStream(r, out.streams.back(), abi_error);
                break;
            }
            case kSectionTypeCOMMANDS: {
                out.commands.emplace_back();
                ok = mesh_abi::decodeCommand(r, out.commands.back(), abi_error);
                break;
            }
            case kSectionTypeCOMMAND_WAITS: {
                mesh_abi::CommandWait wait;
                ok = mesh_abi::decodeCommandWait(r, wait, abi_error);
                if (ok)
                    out.waits.push_back(wait.event_id);
                break;
            }
            case kSectionTypeCOMMAND_OPERANDS: {
                out.operands.emplace_back();
                ok = mesh_abi::decodeCommandOperand(r, out.operands.back(),
                                                    abi_error);
                break;
            }
            case kSectionTypeEVENTS: {
                out.events.emplace_back();
                ok = mesh_abi::decodeEvent(r, out.events.back(), abi_error);
                break;
            }
            case kSectionTypeDMA_DESCRIPTORS: {
                out.descriptors.emplace_back();
                ok = mesh_abi::decodeDmaDescriptor(r, out.descriptors.back(),
                                                   abi_error);
                break;
            }
            case kSectionTypeOP_ATTRS: {
                DecodedAttr attr;
                attr.kind = rdU16(r + kOpAttrsKindOffset);
                if (rdU16(r + kOpAttrsReservedOffset) != 0) {
                    ok = false;
                    abi_error = {"E_ABI_RESERVED", "attr reserved must be zero"};
                    break;
                }
                const uint32_t payload_bytes =
                    mesh_abi::attrPayloadBytes(attr.kind);
                if (payload_bytes == 0) {
                    ok = false;
                    abi_error = {"E_ABI_ENUM", "unknown attr kind"};
                    break;
                }
                std::memcpy(attr.payload.data(), r + kOpAttrsPayloadOffset,
                            attr.payload.size());
                ok = mesh_abi::decodeAttrPayload(attr.kind,
                                                 attr.payload.data(),
                                                 attr.typed, abi_error);
                for (uint32_t pad = ok ? payload_bytes : 0;
                     ok && pad < attr.payload.size(); pad++)
                    if (attr.payload[pad] != 0) {
                        ok = false;
                        abi_error = {"E_ABI_RESERVED",
                                     "attr payload padding nonzero"};
                    }
                if (ok)
                    out.attrs.push_back(std::move(attr));
                break;
            }
            case kSectionTypeRELOCATIONS: {
                out.relocations.emplace_back();
                ok = mesh_abi::decodeRelocation(r, out.relocations.back(),
                                                abi_error);
                break;
            }
            case kSectionTypeEXPECTED_TRAFFIC: {
                out.traffic.emplace_back();
                ok = mesh_abi::decodeExpectedTraffic(r, out.traffic.back(),
                                                     abi_error);
                break;
            }
            case kSectionTypeSOURCE_MAP: {
                out.source_map.emplace_back();
                ok = mesh_abi::decodeSourceMap(r, out.source_map.back(),
                                               abi_error);
                break;
            }
            case kSectionTypeCONTENT_DIGESTS: {
                out.content_digests.emplace_back();
                ok = mesh_abi::decodeContentDigest(r, out.content_digests.back(),
                                                   abi_error);
                break;
            }
            default:
                continue;
            }
            if (!ok)
                return abi_fail(abi_error);
        }
    }
    return true;
}


void recomputeMeshChecksums(MeshBytes &image)
{
    using namespace mesh_abi;
    uint8_t *base = image.data();
    uint64_t dir_offset = rdU64(base + 24);
    uint32_t section_count = rdU32(base + 32);
    for (uint32_t i = 0; i < section_count; i++) {
        uint8_t *entry = base + dir_offset + i * kSectionDirBytes;
        uint64_t offset = rdU64(entry + 8);
        uint64_t size = rdU64(entry + 16);
        uint32_t value = crc32Compute(base + offset, size);
        entry[32] = uint8_t(value);
        entry[33] = uint8_t(value >> 8);
        entry[34] = uint8_t(value >> 16);
        entry[35] = uint8_t(value >> 24);
    }
    Sha256 sha;
    sha.update(base + kHeaderBytes, image.size() - kHeaderBytes);
    uint8_t digest[32];
    sha.final(digest);
    std::memcpy(base + 72, digest, 32);
}

} // namespace ai_mesh
} // namespace gem5
