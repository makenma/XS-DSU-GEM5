#include "dev/ai_mesh/mesh_binary_validation.hh"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <functional>
#include <limits>
#include <string_view>
#include <type_traits>
#include <unordered_map>
#include <unordered_set>

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

using namespace mesh_abi::semantic_abi;

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

bool
byteLess(const std::string &left, const std::string &right)
{
    const size_t common = std::min(left.size(), right.size());
    const int compared = std::memcmp(left.data(), right.data(), common);
    return compared < 0 || (compared == 0 && left.size() < right.size());
}

bool
strictlySorted(const std::vector<std::string> &values)
{
    for (size_t index = 1; index < values.size(); ++index) {
        if (!byteLess(values[index - 1], values[index]))
            return false;
    }
    return true;
}

bool
allUsed(const std::vector<bool> &used)
{
    return std::all_of(used.begin(), used.end(), [](bool value) {
        return value;
    });
}

bool
markString(
    const StringRef &reference, std::vector<bool> &used,
    MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    if (reference.string_id == 0 || reference.string_id > used.size())
        return fail(E_ABI_BOUNDS, "semantic string reference is invalid", error);
    used[reference.string_id - 1] = true;
    return true;
}

bool
markSpan(
    const ListSpan &span, size_t size, size_t &next,
    MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    if (span.count == 0)
        return span.begin == 0 ||
            fail(E_ABI_ORDER, "empty span has nonzero begin", error);
    if (span.begin > size || span.count > size - span.begin)
        return fail(E_ABI_BOUNDS, "semantic list span is out of bounds", error);
    if (span.begin < next)
        return fail(E_ABI_DUPLICATE, "semantic list spans overlap", error);
    if (span.begin > next)
        return fail(E_ABI_ORDER, "semantic list spans are out of order", error);
    next += span.count;
    return true;
}

bool
allowedTarget(
    const SemanticFieldDescriptor &descriptor, const SemanticRef &reference,
    MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    if (reference.row_id == 0)
        return fail(E_ABI_BOUNDS, "semantic reference is absent", error);
    if (descriptor.allowed_target_count == 0)
        return fail(E_ABI_BOUNDS, "semantic reference target is invalid", error);
    const auto begin = descriptor.allowed_target_section_types;
    const auto end = begin + descriptor.allowed_target_count;
    if (std::find(begin, end, reference.section_type) == end)
        return fail(E_ABI_BOUNDS, "semantic reference target is invalid", error);
    return true;
}

bool
validScalar(const ScalarValue &value, MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    switch (value.kind) {
      case kScalarKindBool:
        return value.payload <= 1 ||
            fail(E_ABI_ENUM, "boolean scalar payload is invalid", error);
      case kScalarKindI64:
        int64_t signedValue;
        std::memcpy(&signedValue, &value.payload, sizeof(signedValue));
        return signedValue < 0 ||
            fail(E_ABI_ORDER, "signed scalar is noncanonical", error);
      case kScalarKindU64:
        return true;
      case kScalarKindF64: {
        double decoded = 0;
        std::memcpy(&decoded, &value.payload, sizeof(decoded));
        return std::isfinite(decoded) ||
            fail(E_ABI_BOUNDS, "floating scalar is not finite", error);
      }
      default:
        return fail(E_ABI_ENUM, "scalar kind is invalid", error);
    }
}

bool
validateTransport(
    const ProgramStorage &program, std::vector<bool> &semanticStrings,
    MeshLoadError &error)
{
    bool valid = true;
    mesh_abi::visitTransportTablesWire(
        program.transport,
        [&](const auto &, const auto &table) {
            using Table = std::decay_t<decltype(table)>;
            using Value = typename Table::value_type;
            if constexpr (std::is_same_v<Value, std::string>) {
                return true;
            } else {
                using Traits = mesh_abi::TransportRecordTraits<Value>;
                for (const auto &value : table) {
                    std::array<uint8_t, Traits::record_bytes> encoded{};
                    mesh_abi::AbiError abiError;
                    if (!Traits::encode(value, encoded, abiError)) {
                        valid = fail(abiError, error);
                        return false;
                    }
                    Value decoded{};
                    if (!Traits::decode(encoded.data(), decoded, abiError)) {
                        valid = fail(abiError, error);
                        return false;
                    }
                    if constexpr (!std::is_same_v<Value, mesh_abi::TypedOpAttr>) {
                        mesh_abi::visitTransportFieldsCanonical(
                            value, mesh_abi::CanonicalProjection::Full,
                            [&](const auto &descriptor, const auto &field) {
                                if (descriptor.string_pool !=
                                        mesh_abi::TransportStringPool::Semantic)
                                    return true;
                                if constexpr (std::is_integral_v<
                                                  std::decay_t<decltype(field)>>) {
                                    if (!markString(
                                            {static_cast<uint32_t>(field)},
                                            semanticStrings, error)) {
                                        valid = false;
                                        return false;
                                    }
                                }
                                return true;
                            });
                        if (!valid)
                            return false;
                    }
                }
                return true;
            }
        });
    return valid;
}

bool
validateOptionalTables(const ProgramStorage &program, MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    const auto &transport = program.transport;
    for (size_t index = 1; index < transport.source_map.size(); ++index) {
        if (transport.source_map[index - 1].loc_id >=
                transport.source_map[index].loc_id)
            return fail(
                E_ABI_ORDER,
                "SOURCE_MAP keys must be sorted and unique", error);
    }
    for (size_t index = 1; index < transport.profile_hints.size(); ++index) {
        const auto &previous = transport.profile_hints[index - 1];
        const auto &hint = transport.profile_hints[index];
        const std::string &previousName =
            program.semantic_strings[previous.name_sid - 1];
        const std::string &name =
            program.semantic_strings[hint.name_sid - 1];
        const bool ordered = previous.entrypoint_id < hint.entrypoint_id ||
            (previous.entrypoint_id == hint.entrypoint_id &&
             (previous.profile_id < hint.profile_id ||
              (previous.profile_id == hint.profile_id &&
               byteLess(previousName, name))));
        if (!ordered)
            return fail(
                E_ABI_ORDER,
                "PROFILE_HINTS keys must be sorted and unique", error);
    }
    for (size_t index = 1; index < transport.content_digests.size(); ++index) {
        const auto &previous = transport.content_digests[index - 1];
        const auto &digest = transport.content_digests[index];
        if (previous.object_kind > digest.object_kind ||
            (previous.object_kind == digest.object_kind &&
             previous.object_id >= digest.object_id))
            return fail(
                E_ABI_ORDER,
                "CONTENT_DIGESTS keys must be sorted and unique", error);
    }
    std::unordered_set<uint32_t> sourceIds;
    for (const auto &source : transport.source_map) {
        sourceIds.insert(source.loc_id);
        const std::string &path =
            program.semantic_strings[source.file_sid - 1];
        if (path.empty() || path.front() == '/' || path.back() == '/')
            return fail(
                E_ABI_BOUNDS,
                "source filename must be a contained relative path", error);
        size_t begin = 0;
        while (begin < path.size()) {
            const size_t end = path.find('/', begin);
            const std::string_view part(
                path.data() + begin,
                (end == std::string::npos ? path.size() : end) - begin);
            if (part.empty() || part == ".." ||
                (part == "." && path.size() != 1))
                return fail(
                    E_ABI_BOUNDS,
                    "source filename must be a contained relative path",
                    error);
            if (end == std::string::npos)
                break;
            begin = end + 1;
        }
    }
    for (const auto &command : transport.commands) {
        if (command.debug_loc_id != 0 &&
            !sourceIds.count(command.debug_loc_id))
            return fail(
                E_ABI_BOUNDS,
                "command debug location is unknown", error);
    }

    std::unordered_set<uint64_t> profiles;
    for (const auto &profile : transport.profiles) {
        profiles.insert(
            (static_cast<uint64_t>(profile.entrypoint_id) << 32) |
            profile.profile_id);
    }
    for (const auto &hint : transport.profile_hints) {
        const uint64_t key =
            (static_cast<uint64_t>(hint.entrypoint_id) << 32) |
            hint.profile_id;
        if (!profiles.count(key))
            return fail(
                E_ABI_BOUNDS,
                "profile hint references an unknown profile", error);
    }

    std::unordered_set<uint64_t> tensorIds;
    std::unordered_set<uint64_t> shardIds;
    std::unordered_set<uint64_t> allocationIds;
    std::unordered_set<uint64_t> objectIds;
    std::unordered_map<uint64_t, const mesh_abi::Tensor *> tensors;
    for (const auto &tensor : transport.tensors) {
        tensorIds.insert(tensor.tensor_id);
        tensors[tensor.tensor_id] = &tensor;
    }
    for (const auto &shard : transport.shards)
        shardIds.insert(shard.shard_id);
    for (const auto &allocation : transport.allocations)
        allocationIds.insert(allocation.allocation_id);
    for (const auto &object : program.semantic_tables.buffer_object_rows)
        objectIds.insert(object.object_id);
    for (const auto &digest : transport.content_digests) {
        const std::unordered_set<uint64_t> *targets = nullptr;
        switch (digest.object_kind) {
          case mesh_abi::kContentDigestObjectKindTENSOR:
            targets = &tensorIds;
            break;
          case mesh_abi::kContentDigestObjectKindSHARD:
            targets = &shardIds;
            break;
          case mesh_abi::kContentDigestObjectKindALLOCATION:
            targets = &allocationIds;
            break;
          case mesh_abi::kContentDigestObjectKindKERNEL_OBJECT:
            targets = &objectIds;
            break;
          default:
            return fail(
                E_ABI_ENUM,
                "content digest object kind is unknown", error);
        }
        if (!targets->count(digest.object_id))
            return fail(
                E_ABI_BOUNDS,
                "content digest references an unknown object", error);
        if (digest.object_kind ==
                mesh_abi::kContentDigestObjectKindTENSOR) {
            const mesh_abi::Tensor &tensor = *tensors[digest.object_id];
            if ((tensor.flags & mesh_abi::kTensorFlagsHAS_CONTENT_SHA256) &&
                tensor.content_sha256 != digest.digest)
                return fail(
                    E_ABI_CHECKSUM,
                    "content digest contradicts tensor content hash", error);
        }
    }
    return true;
}

template <typename Value>
bool
isZeroField(const Value &value)
{
    if constexpr (std::is_enum_v<Value>) {
        return static_cast<std::underlying_type_t<Value>>(value) == 0;
    } else if constexpr (std::is_integral_v<Value>) {
        return value == 0;
    } else if constexpr (std::is_same_v<Value, double>) {
        uint64_t bits = 0;
        std::memcpy(&bits, &value, sizeof(bits));
        return bits == 0;
    } else if constexpr (std::is_same_v<Value, SemanticRef>) {
        return value.section_type == 0 && value.row_id == 0;
    } else if constexpr (std::is_same_v<Value, ListSpan>) {
        return value.begin == 0 && value.count == 0;
    } else if constexpr (std::is_same_v<Value, StringRef>) {
        return value.string_id == 0;
    } else if constexpr (std::is_same_v<Value, ScalarValue>) {
        return value.kind == 0 && value.payload == 0;
    } else {
        return false;
    }
}

bool
validateSemanticRows(const ProgramStorage &program, MeshLoadError &error)
{
    bool valid = true;
    mesh_abi::semantic_abi::visitSemanticTables(
        program.semantic_tables, [&](const auto &traits, const auto &table) {
            using Traits = std::decay_t<decltype(traits)>;
            using Value = typename std::decay_t<decltype(table)>::value_type;
            for (const auto &value : table) {
                const auto encoded = Traits::encode(value);
                Value decoded{};
                mesh_abi::AbiError abiError;
                if (!Traits::decode(encoded.data(), decoded, abiError)) {
                    valid = fail(abiError, error);
                    return false;
                }
                mesh_abi::semantic_abi::visitSemanticFieldsWire(
                    value, [&](const auto &, bool present,
                               const auto &field) {
                        if (!present && !isZeroField(field)) {
                            valid = fail(
                                mesh_diagnostics::E_ABI_RESERVED,
                                "absent semantic field is nonzero", error);
                            return false;
                        }
                        return true;
                    });
                if (!valid)
                    return false;
            }
            return true;
        });
    return valid;
}

struct Ownership
{
    const ProgramStorage &program;
    std::vector<bool> semantic_strings;
    std::unordered_set<uint64_t> records;
    std::unordered_map<uint16_t, uint32_t> next_rows;
    size_t next_u64_value = 0;
    size_t next_i64_value = 0;
    size_t next_reference = 0;
    size_t next_byte = 0;
    size_t next_integer_value = 0;
    size_t total_records = 0;
};

using ValidationTask = std::function<bool()>;

bool validateRecord(
    Ownership &ownership, const SemanticRef &reference,
    std::vector<ValidationTask> &pending, MeshLoadError &error);

bool
validateField(
    Ownership &ownership, const SemanticFieldDescriptor &descriptor,
    bool present, const SemanticRef &value,
    std::vector<ValidationTask> &pending, MeshLoadError &error)
{
    if (!present)
        return true;
    if (!allowedTarget(descriptor, value, error))
        return false;
    pending.emplace_back([&ownership, &pending, &error, value]() {
        return validateRecord(ownership, value, pending, error);
    });
    return true;
}

bool
validateField(
    Ownership &ownership, const SemanticFieldDescriptor &,
    bool present, const StringRef &value,
    std::vector<ValidationTask> &, MeshLoadError &error)
{
    if (!present)
        return true;
    return markString(value, ownership.semantic_strings, error);
}

bool
validateField(
    Ownership &ownership, const SemanticFieldDescriptor &descriptor,
    bool present, const ListSpan &value,
    std::vector<ValidationTask> &pending, MeshLoadError &error)
{
    if (!present)
        return true;
    switch (descriptor.kind) {
      case SemanticFieldKind::U64List:
        return markSpan(
            value, ownership.program.semantic_u64_values.size(),
            ownership.next_u64_value, error);
      case SemanticFieldKind::IntegerList:
        return markSpan(
            value, ownership.program.semantic_integer_values.size(),
            ownership.next_integer_value, error);
      case SemanticFieldKind::Bytes:
        return markSpan(
            value, ownership.program.semantic_bytes.size(),
            ownership.next_byte, error);
      case SemanticFieldKind::RefList:
        if (!markSpan(
                value, ownership.program.semantic_references.size(),
                ownership.next_reference, error))
            return false;
        for (uint64_t index =
                 static_cast<uint64_t>(value.begin) + value.count;
             index > value.begin; --index) {
            const SemanticRef reference =
                ownership.program.semantic_references[index - 1];
            pending.emplace_back(
                [&ownership, &pending, &error, descriptor, reference]() {
                    if (!allowedTarget(descriptor, reference, error))
                        return false;
                    return validateRecord(
                        ownership, reference, pending, error);
                });
        }
        return true;
      default:
        return fail(
            mesh_diagnostics::E_ABI_CORRUPT,
            "semantic span has an invalid field kind", error);
    }
}

bool
validateField(
    Ownership &, const SemanticFieldDescriptor &, bool present,
    const ScalarValue &value, std::vector<ValidationTask> &,
    MeshLoadError &error)
{
    return !present || validScalar(value, error);
}

template <typename Value>
bool
validateField(
    Ownership &, const SemanticFieldDescriptor &, bool present,
    const Value &value, std::vector<ValidationTask> &, MeshLoadError &error)
{
    if (!present)
        return true;
    if constexpr (std::is_enum_v<Value>) {
        if (SemanticEnumTraits<Value>::pythonValue(value).kind ==
            SemanticEnumPythonKind::Invalid)
            return fail(
                mesh_diagnostics::E_ABI_ENUM,
                "semantic enum value is invalid", error);
    } else if constexpr (std::is_same_v<Value, double>) {
        if (!std::isfinite(value))
            return fail(
                mesh_diagnostics::E_ABI_BOUNDS,
                "semantic float is not finite", error);
    }
    return true;
}

bool
validateRecord(
    Ownership &ownership, const SemanticRef &reference,
    std::vector<ValidationTask> &pending, MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    if (reference.row_id == 0)
        return fail(E_ABI_BOUNDS, "semantic record reference is absent", error);
    const uint64_t key = (static_cast<uint64_t>(reference.section_type) << 32) |
                         reference.row_id;
    if (!ownership.records.insert(key).second)
        return fail(E_ABI_DUPLICATE, "semantic record has multiple owners", error);
    bool matched = false;
    bool valid = true;
    mesh_abi::semantic_abi::dispatchSemanticTable(
        reference.section_type, ownership.program.semantic_tables,
        [&](const auto &, const auto &table) {
            matched = true;
            if (reference.row_id > table.size()) {
                valid = fail(E_ABI_BOUNDS, "semantic row is out of bounds", error);
                return false;
            }
            uint32_t &next = ownership.next_rows[reference.section_type];
            if (next == 0)
                next = 1;
            if (reference.row_id != next) {
                valid = fail(E_ABI_ORDER, "semantic rows are not in occurrence order", error);
                return false;
            }
            ++next;
            const auto &record = table[reference.row_id - 1];
            std::vector<ValidationTask> fields;
            mesh_abi::semantic_abi::visitSemanticFieldsWire(
                record, [&](const auto &descriptor, bool present,
                            const auto &field) {
                    fields.emplace_back(
                        [&ownership, &pending, &error, descriptor,
                         present, field]() {
                            return validateField(
                                ownership, descriptor, present, field,
                                pending, error);
                        });
                    return true;
                });
            pending.insert(pending.end(), fields.rbegin(), fields.rend());
            return valid;
        });
    return matched ? valid :
        fail(E_ABI_BOUNDS, "semantic section type is invalid", error);
}

bool
validateSemantic(Ownership &ownership, MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    std::vector<ValidationTask> pending;
    const SemanticRef root = ownership.program.metadata.semantics;
    pending.emplace_back([&ownership, &pending, &error, root]() {
        return validateRecord(ownership, root, pending, error);
    });
    while (!pending.empty()) {
        ValidationTask task = std::move(pending.back());
        pending.pop_back();
        if (!task())
            return false;
    }
    if (ownership.records.size() != ownership.total_records)
        return fail(E_ABI_BOUNDS, "semantic tables contain unreachable rows", error);
    if (!allUsed(ownership.semantic_strings) ||
        ownership.next_u64_value !=
            ownership.program.semantic_u64_values.size() ||
        ownership.next_i64_value !=
            ownership.program.semantic_i64_values.size() ||
        ownership.next_reference !=
            ownership.program.semantic_references.size() ||
        ownership.next_byte != ownership.program.semantic_bytes.size() ||
        ownership.next_integer_value !=
            ownership.program.semantic_integer_values.size())
        return fail(E_ABI_BOUNDS, "semantic support data is unreachable", error);
    return true;
}

}

bool
validateProgramStorage(const ProgramStorage &program, MeshLoadError &error)
{
    using namespace mesh_diagnostics;
    if (!validateProgramMetadata(program, error))
        return false;
    for (const auto &value : program.transport.strings) {
        if (!mesh_canonical::validUtf8(value))
            return fail(E_ABI_CORRUPT, "transport string is not valid UTF-8", error);
    }
    for (const auto &value : program.semantic_strings) {
        if (!mesh_canonical::validUtf8(value))
            return fail(E_ABI_CORRUPT, "semantic string is not valid UTF-8", error);
    }
    if (!strictlySorted(program.semantic_strings))
        return fail(
            E_ABI_ORDER,
            "semantic string table must be unique and sorted", error);
    std::vector<bool> semanticStrings(program.semantic_strings.size());
    if (!validateTransport(program, semanticStrings, error))
        return false;
    if (!validateOptionalTables(program, error))
        return false;
    if (!validateSemanticRows(program, error))
        return false;
    for (const auto &value : program.semantic_integer_values) {
        if (value.kind != kScalarKindI64 && value.kind != kScalarKindU64)
            return fail(E_ABI_ENUM, "semantic integer kind is invalid", error);
        if (value.kind == kScalarKindI64) {
            int64_t signedValue;
            std::memcpy(&signedValue, &value.payload, sizeof(signedValue));
            if (signedValue >= 0)
                return fail(E_ABI_ORDER, "semantic signed integer is noncanonical", error);
        }
    }
    Ownership ownership{
        program,
        std::move(semanticStrings),
        {},
        {},
        0,
        0,
        0,
        0,
        0,
        0,
    };
    bool countsValid = true;
    mesh_abi::semantic_abi::visitSemanticTables(
        program.semantic_tables, [&](const auto &, const auto &table) {
            if (table.size() >
                std::numeric_limits<size_t>::max() - ownership.total_records) {
                error = {E_ABI_OVERFLOW, "semantic row count overflows"};
                countsValid = false;
                return false;
            }
            ownership.total_records += table.size();
            return true;
        });
    if (!countsValid)
        return false;
    return validateSemantic(ownership, error);
}

}
}
}
