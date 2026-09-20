#include "dev/ai_mesh/mesh_binary_storage.hh"

#include <algorithm>
#include <limits>
#include <type_traits>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/mesh_canonical.hh"

namespace gem5
{
namespace ai_mesh
{
namespace mesh_binary_detail
{

namespace
{

using mesh_abi::semantic_abi::SemanticIntegerValue;
using mesh_abi::semantic_abi::SemanticRef;

struct StringBlobLayout
{
    uint32_t header_bytes;
    uint32_t count_offset;
    uint32_t directory_record_bytes;
    uint32_t directory_offset_offset;
    uint32_t directory_length_offset;
};

constexpr StringBlobLayout TransportStringsLayout{
    mesh_abi::kStringsBlobHeaderBytes,
    mesh_abi::kStringsBlobCountOffset,
    mesh_abi::kStringsDirectoryRecordBytes,
    mesh_abi::kStringsDirectoryOffsetOffset,
    mesh_abi::kStringsDirectoryLengthOffset,
};

constexpr StringBlobLayout SemanticStringsLayout{
    mesh_abi::kSemanticStringsBlobHeaderBytes,
    mesh_abi::kSemanticStringsBlobCountOffset,
    mesh_abi::kSemanticStringsDirectoryRecordBytes,
    mesh_abi::kSemanticStringsDirectoryOffsetOffset,
    mesh_abi::kSemanticStringsDirectoryLengthOffset,
};

static_assert(mesh_abi::kStringsBlobCountBytes == sizeof(uint32_t));
static_assert(mesh_abi::kStringsDirectoryOffsetBytes == sizeof(uint32_t));
static_assert(mesh_abi::kStringsDirectoryLengthBytes == sizeof(uint32_t));
static_assert(mesh_abi::kSemanticStringsBlobCountBytes == sizeof(uint32_t));
static_assert(mesh_abi::kSemanticStringsDirectoryOffsetBytes == sizeof(uint32_t));
static_assert(mesh_abi::kSemanticStringsDirectoryLengthBytes == sizeof(uint32_t));

bool
fail(const char *code, const char *message, MeshLoadError &error)
{
    error = {code, message};
    return false;
}

bool
fail(const mesh_abi::AbiError &source, MeshLoadError &error)
{
    error = {source.code, source.message};
    return false;
}

const MeshSectionView *
findSection(const DecodedEnvelope &envelope, uint16_t type)
{
    const auto found = std::lower_bound(
        envelope.sections.begin(), envelope.sections.end(), type,
        [](const MeshSectionView &section, uint16_t value) {
            return section.type < value;
        });
    return found != envelope.sections.end() && found->type == type ?
        &*found : nullptr;
}

bool
decodeStrings(
    const MeshBytes &image, const MeshSectionView &section,
    const StringBlobLayout &layout, std::vector<std::string> &out,
    MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    if (section.record_bytes != 0 || section.size < layout.header_bytes ||
        section.count > std::numeric_limits<uint32_t>::max())
        return fail(E_ABI_SECTION_RANGE, "string section header is invalid", error);
    const uint8_t *data = image.data() + section.offset;
    const uint32_t count = mesh_abi::rdU32(data + layout.count_offset);
    if (count != section.count ||
        count > (section.size - layout.header_bytes) /
            layout.directory_record_bytes)
        return fail(E_ABI_SECTION_RANGE, "string directory is invalid", error);
    const uint64_t directoryBytes = layout.header_bytes +
        static_cast<uint64_t>(count) * layout.directory_record_bytes;
    const uint64_t blobBytes = section.size - directoryBytes;
    uint64_t previousEnd = 0;
    std::vector<std::string> candidate;
    candidate.reserve(count);
    for (uint32_t index = 0; index < count; ++index) {
        const uint8_t *entry = data + layout.header_bytes +
            static_cast<uint64_t>(index) * layout.directory_record_bytes;
        const uint32_t offset = mesh_abi::rdU32(
            entry + layout.directory_offset_offset);
        const uint32_t length = mesh_abi::rdU32(
            entry + layout.directory_length_offset);
        if (offset > blobBytes || length > blobBytes - offset)
            return fail(E_ABI_SECTION_RANGE,
                        "string range is out of bounds", error);
        if (offset != previousEnd)
            return fail(E_ABI_ORDER, "string ranges are not dense", error);
        const char *value = reinterpret_cast<const char *>(
            data + directoryBytes + offset);
        if (!mesh_canonical::validUtf8(std::string_view(value, length)))
            return fail(E_ABI_CORRUPT, "string is not valid UTF-8", error);
        candidate.emplace_back(value, length);
        previousEnd += length;
    }
    if (previousEnd != blobBytes)
        return fail(E_ABI_SECTION_RANGE, "string data has trailing bytes", error);
    out = std::move(candidate);
    return true;
}

bool
encodeStrings(
    uint16_t type, const std::vector<std::string> &values,
    const StringBlobLayout &layout, MeshSection &out, MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    if (values.size() > std::numeric_limits<uint32_t>::max())
        return fail(E_ABI_OVERFLOW, "string count exceeds uint32", error);
    uint64_t blobBytes = 0;
    for (const auto &value : values) {
        if (!mesh_canonical::validUtf8(value))
            return fail(E_ABI_CORRUPT, "string is not valid UTF-8", error);
        if (value.size() > std::numeric_limits<uint32_t>::max() - blobBytes)
            return fail(E_ABI_OVERFLOW, "string blob exceeds uint32", error);
        blobBytes += value.size();
    }
    if (values.size() >
        (std::numeric_limits<uint64_t>::max() - layout.header_bytes) /
            layout.directory_record_bytes)
        return fail(E_ABI_OVERFLOW, "string directory size overflows", error);
    const uint64_t directoryBytes = layout.header_bytes +
        static_cast<uint64_t>(values.size()) * layout.directory_record_bytes;
    if (directoryBytes > std::numeric_limits<size_t>::max() - blobBytes)
        return fail(E_ABI_OVERFLOW, "string section size overflows", error);
    MeshBytes payload;
    if (directoryBytes + blobBytes > payload.max_size())
        return fail(E_ABI_OVERFLOW, "string section exceeds container capacity", error);
    payload.resize(static_cast<size_t>(directoryBytes + blobBytes));
    mesh_abi::wrU32(
        payload.data() + layout.count_offset,
        static_cast<uint32_t>(values.size()));
    uint32_t offset = 0;
    for (size_t index = 0; index < values.size(); ++index) {
        uint8_t *entry = payload.data() + layout.header_bytes +
            index * layout.directory_record_bytes;
        mesh_abi::wrU32(entry + layout.directory_offset_offset, offset);
        mesh_abi::wrU32(
            entry + layout.directory_length_offset,
            static_cast<uint32_t>(values[index].size()));
        std::copy(
            values[index].begin(), values[index].end(),
            payload.begin() + static_cast<ptrdiff_t>(directoryBytes + offset));
        offset += static_cast<uint32_t>(values[index].size());
    }
    out = {type, 0, values.size(), std::move(payload)};
    return true;
}

template <typename Traits, typename Value>
bool
decodeRecords(
    const MeshBytes &image, const MeshSectionView &section,
    std::vector<Value> &out, MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    if (section.record_bytes != Traits::record_bytes ||
        section.count > std::numeric_limits<size_t>::max())
        return fail(E_ABI_SECTION_RANGE, "record section width is invalid", error);
    std::vector<Value> candidate;
    candidate.reserve(static_cast<size_t>(section.count));
    for (uint64_t index = 0; index < section.count; ++index) {
        Value value{};
        mesh_abi::AbiError abiError;
        if (!Traits::decode(
                image.data() + section.offset + index * Traits::record_bytes,
                value, abiError))
            return fail(abiError, error);
        candidate.push_back(std::move(value));
    }
    out = std::move(candidate);
    return true;
}

template <typename Value>
bool
decodeTransportTable(
    const MeshBytes &image, const MeshSectionView &section,
    std::vector<Value> &out, MeshLoadError &error)
{
    if constexpr (std::is_same_v<Value, std::string>)
        return decodeStrings(
            image, section, TransportStringsLayout, out, error);
    else
        return decodeRecords<mesh_abi::TransportRecordTraits<Value>>(
            image, section, out, error);
}

template <typename Values, typename Encoder>
bool
encodeFixedSection(
    uint16_t type, uint32_t recordBytes, const Values &values,
    Encoder &&encoder, MeshSection &out, MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    MeshBytes payload;
    if (recordBytes == 0 ||
        values.size() > std::numeric_limits<size_t>::max() / recordBytes)
        return fail(E_ABI_OVERFLOW, "fixed section size overflows", error);
    if (values.size() > payload.max_size() / recordBytes)
        return fail(E_ABI_OVERFLOW, "fixed section exceeds container capacity", error);
    payload.resize(values.size() * recordBytes);
    for (size_t index = 0; index < values.size(); ++index) {
        if (!encoder(values[index], payload.data() + index * recordBytes))
            return false;
    }
    out = {type, recordBytes, values.size(), std::move(payload)};
    return true;
}

template <typename Value>
bool
encodeTransportTable(
    const mesh_abi::TransportSectionDescriptor &descriptor,
    const std::vector<Value> &values, MeshSection &out,
    MeshLoadError &error)
{
    if constexpr (std::is_same_v<Value, std::string>)
        return encodeStrings(
            descriptor.section_type, values, TransportStringsLayout,
            out, error);
    else {
        using Traits = mesh_abi::TransportRecordTraits<Value>;
        auto encoder = [&](const Value &value, uint8_t *destination) {
            std::array<uint8_t, Traits::record_bytes> encoded{};
            mesh_abi::AbiError abiError;
            if (!Traits::encode(value, encoded, abiError))
                return fail(abiError, error);
            std::copy(encoded.begin(), encoded.end(), destination);
            return true;
        };
        return encodeFixedSection(
            descriptor.section_type, Traits::record_bytes,
            values, encoder, out, error);
    }
}

bool
decodeSupportSection(
    const MeshBytes &image, const MeshSectionView &section,
    ProgramStorage &out, bool &matched, MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    using namespace mesh_abi::semantic_abi;
    const uint8_t *data = image.data() + section.offset;
    auto width = [&](uint32_t expected) {
        return section.record_bytes == expected;
    };
    if (section.count > std::numeric_limits<size_t>::max())
        return fail(E_ABI_SECTION_RANGE, "support section count is too large", error);
    switch (section.type) {
      case mesh_abi::kSectionTypePROGRAM_METADATA: {
        matched = true;
        if (!width(kProgramMetadataBytes) || section.count != 1)
            return fail(E_ABI_SECTION_RANGE, "PROGRAM_METADATA shape is invalid", error);
        mesh_abi::AbiError abiError;
        if (!decodeProgramMetadata(data, out.metadata, abiError))
            return fail(abiError, error);
        return true;
      }
      case mesh_abi::kSectionTypeSEMANTIC_STRINGS:
        matched = true;
        return decodeStrings(
            image, section, SemanticStringsLayout,
            out.semantic_strings, error);
      case mesh_abi::kSectionTypeSEMANTIC_U64_VALUES:
        matched = true;
        if (!width(mesh_abi::kSemanticU64ValuesBytes))
            return fail(E_ABI_SECTION_RANGE, "semantic u64 width is invalid", error);
        out.semantic_u64_values.resize(section.count);
        for (uint64_t index = 0; index < section.count; ++index)
            out.semantic_u64_values[index] = mesh_abi::rdU64(
                data + index * mesh_abi::kSemanticU64ValuesBytes);
        return true;
      case mesh_abi::kSectionTypeSEMANTIC_I64_VALUES:
        matched = true;
        if (!width(mesh_abi::kSemanticI64ValuesBytes))
            return fail(E_ABI_SECTION_RANGE, "semantic i64 width is invalid", error);
        out.semantic_i64_values.resize(section.count);
        for (uint64_t index = 0; index < section.count; ++index)
            out.semantic_i64_values[index] = rdI64(
                data + index * mesh_abi::kSemanticI64ValuesBytes);
        return true;
      case mesh_abi::kSectionTypeSEMANTIC_REFERENCES:
        matched = true;
        if (!width(mesh_abi::kSemanticReferencesBytes))
            return fail(E_ABI_SECTION_RANGE, "semantic ref width is invalid", error);
        out.semantic_references.resize(section.count);
        for (uint64_t index = 0; index < section.count; ++index) {
            mesh_abi::AbiError abiError;
            if (!decodeSemanticRef(
                    data + index * mesh_abi::kSemanticReferencesBytes,
                    out.semantic_references[index], abiError))
                return fail(abiError, error);
        }
        return true;
      case mesh_abi::kSectionTypeSEMANTIC_BYTES:
        matched = true;
        if (!width(mesh_abi::kSemanticBytesBytes))
            return fail(E_ABI_SECTION_RANGE, "semantic byte width is invalid", error);
        out.semantic_bytes.assign(data, data + section.count);
        return true;
      case mesh_abi::kSectionTypeSEMANTIC_INTEGER_VALUES:
        matched = true;
        if (!width(mesh_abi::kSemanticIntegerValuesBytes))
            return fail(E_ABI_SECTION_RANGE, "semantic integer width is invalid", error);
        out.semantic_integer_values.resize(section.count);
        for (uint64_t index = 0; index < section.count; ++index) {
            mesh_abi::AbiError abiError;
            if (!decodeSemanticIntegerValue(
                    data + index * mesh_abi::kSemanticIntegerValuesBytes,
                    out.semantic_integer_values[index], abiError))
                return fail(abiError, error);
        }
        return true;
      default:
        return false;
    }
}

bool
encodeU64Values(
    const std::vector<uint64_t> &values, MeshSection &out,
    MeshLoadError &error)
{
    return encodeFixedSection(
        mesh_abi::kSectionTypeSEMANTIC_U64_VALUES,
        mesh_abi::kSemanticU64ValuesBytes, values,
        [](uint64_t value, uint8_t *destination) {
            mesh_abi::wrU64(destination, value);
            return true;
        }, out, error);
}

bool
encodeI64Values(
    const std::vector<int64_t> &values, MeshSection &out,
    MeshLoadError &error)
{
    return encodeFixedSection(
        mesh_abi::kSectionTypeSEMANTIC_I64_VALUES,
        mesh_abi::kSemanticI64ValuesBytes, values,
        [](int64_t value, uint8_t *destination) {
            mesh_abi::semantic_abi::wrI64(destination, value);
            return true;
        }, out, error);
}

bool
encodeReferences(
    const std::vector<SemanticRef> &values, MeshSection &out,
    MeshLoadError &error)
{
    return encodeFixedSection(
        mesh_abi::kSectionTypeSEMANTIC_REFERENCES,
        mesh_abi::kSemanticReferencesBytes, values,
        [](const SemanticRef &value, uint8_t *destination) {
            mesh_abi::semantic_abi::encodeSemanticRef(destination, value);
            return true;
        }, out, error);
}

bool
encodeIntegerValues(
    const std::vector<SemanticIntegerValue> &values, MeshSection &out,
    MeshLoadError &error)
{
    return encodeFixedSection(
        mesh_abi::kSectionTypeSEMANTIC_INTEGER_VALUES,
        mesh_abi::kSemanticIntegerValuesBytes, values,
        [](const SemanticIntegerValue &value, uint8_t *destination) {
            const auto encoded =
                mesh_abi::semantic_abi::encodeSemanticIntegerValue(value);
            std::copy(encoded.begin(), encoded.end(), destination);
            return true;
        }, out, error);
}

template <typename Traits, typename Value>
bool
encodeSemanticTable(
    const std::vector<Value> &values, MeshSection &out,
    MeshLoadError &error)
{
    return encodeFixedSection(
        Traits::section_type, Traits::record_bytes, values,
        [](const Value &value, uint8_t *destination) {
            const auto encoded = Traits::encode(value);
            std::copy(encoded.begin(), encoded.end(), destination);
            return true;
        }, out, error);
}

}

bool
validateProgramMetadata(const ProgramStorage &program, MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    if (!validateMeshHeader(program.header, error))
        return false;
    if (program.metadata.min_reader_minor <
            mesh_abi::kRequiredFeatureScheduledSemanticsMinReaderMinor ||
        program.metadata.min_reader_minor > mesh_abi::kAbiMinor ||
        program.metadata.min_reader_minor > program.header.abi_minor)
        return fail(E_ABI_VERSION, "minimum reader minor is unsupported", error);
    if (program.metadata.semantics.section_type !=
            mesh_abi::kSectionTypeSEMANTIC_PROGRAM_SEMANTICS ||
        program.metadata.semantics.row_id != 1)
        return fail(E_ABI_BOUNDS, "PROGRAM_METADATA root is invalid", error);
    return true;
}

bool
decodeProgramStorage(
    const MeshBytes &image, const DecodedEnvelope &envelope,
    ProgramStorage &out, MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    ProgramStorage candidate;
    candidate.header = envelope.header;
    for (const auto &section : envelope.sections) {
        bool matched = false;
        bool decoded = true;
        mesh_abi::dispatchTransportTable(
            section.type, candidate.transport,
            [&](const auto &, auto &table) {
                matched = true;
                decoded = decodeTransportTable(image, section, table, error);
                return decoded;
            });
        if (matched) {
            if (!decoded)
                return false;
            continue;
        }
        mesh_abi::semantic_abi::dispatchSemanticTable(
            section.type, candidate.semantic_tables,
            [&](const auto &traits, auto &table) {
                matched = true;
                decoded = decodeRecords<std::decay_t<decltype(traits)>>(
                    image, section, table, error);
                return decoded;
            });
        if (matched) {
            if (!decoded)
                return false;
            continue;
        }
        decoded = decodeSupportSection(
            image, section, candidate, matched, error);
        if (matched && !decoded)
            return false;
        if (!matched)
            return fail(E_ABI_ENUM, "section type is unknown", error);
    }

    bool complete = true;
    mesh_abi::visitTransportTablesWire(
        candidate.transport, [&](const auto &descriptor, const auto &table) {
            const bool present = findSection(envelope, descriptor.section_type);
            if (descriptor.optional && present && table.empty()) {
                error = {E_ABI_BOUNDS, "empty optional section must be omitted"};
                complete = false;
                return false;
            }
            return true;
        });
    if (!complete)
        return false;
    for (const auto &required : mesh_abi::kRequiredSections) {
        const MeshSectionView *section = findSection(
            envelope, required.section_type);
        if (!section)
            return fail(E_ABI_SECTION_RANGE, "required section is missing", error);
        if (section->record_bytes != required.record_bytes)
            return fail(E_ABI_SECTION_RANGE, "required section width is invalid", error);
    }
    if (!validateProgramMetadata(candidate, error))
        return false;
    out = std::move(candidate);
    return true;
}

bool
encodeProgramStorage(
    const ProgramStorage &program, std::vector<MeshSection> &out,
    MeshLoadError &error)
{
    if (!validateProgramMetadata(program, error))
        return false;
    std::vector<MeshSection> sections;
    bool encoded = true;
    mesh_abi::visitTransportTablesWire(
        program.transport,
        [&](const auto &descriptor, const auto &table) {
            if (descriptor.optional && table.empty())
                return true;
            MeshSection section;
            if (!encodeTransportTable(descriptor, table, section, error)) {
                encoded = false;
                return false;
            }
            sections.push_back(std::move(section));
            return true;
        });
    if (!encoded)
        return false;
    const auto metadata =
        mesh_abi::semantic_abi::encodeProgramMetadata(program.metadata);
    sections.push_back({
        mesh_abi::kSectionTypePROGRAM_METADATA,
        mesh_abi::semantic_abi::kProgramMetadataBytes, 1,
        MeshBytes(metadata.begin(), metadata.end())});
    MeshSection semanticStrings;
    if (!encodeStrings(
            mesh_abi::kSectionTypeSEMANTIC_STRINGS,
            program.semantic_strings, SemanticStringsLayout,
            semanticStrings, error))
        return false;
    sections.push_back(std::move(semanticStrings));
    MeshSection support;
    if (!encodeU64Values(program.semantic_u64_values, support, error))
        return false;
    sections.push_back(std::move(support));
    if (!encodeI64Values(program.semantic_i64_values, support, error))
        return false;
    sections.push_back(std::move(support));
    if (!encodeReferences(program.semantic_references, support, error))
        return false;
    sections.push_back(std::move(support));
    if (!encodeFixedSection(
            mesh_abi::kSectionTypeSEMANTIC_BYTES,
            mesh_abi::kSemanticBytesBytes, program.semantic_bytes,
            [](uint8_t value, uint8_t *destination) {
                *destination = value;
                return true;
            }, support, error))
        return false;
    sections.push_back(std::move(support));
    if (!encodeIntegerValues(program.semantic_integer_values, support, error))
        return false;
    sections.push_back(std::move(support));
    mesh_abi::semantic_abi::visitSemanticTables(
        program.semantic_tables, [&](const auto &traits, const auto &table) {
            MeshSection section;
            encoded = encodeSemanticTable<std::decay_t<decltype(traits)>>(
                table, section, error);
            if (encoded)
                sections.push_back(std::move(section));
            return encoded;
        });
    if (!encoded)
        return false;
    std::sort(
        sections.begin(), sections.end(),
        [](const MeshSection &left, const MeshSection &right) {
            return left.type < right.type;
        });
    out = std::move(sections);
    return true;
}

}
}
}
