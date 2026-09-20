#include "dev/ai_mesh/mesh_binary_canonical.hh"

#include <algorithm>
#include <array>
#include <cstring>
#include <functional>
#include <ostream>
#include <string_view>
#include <type_traits>
#include <vector>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/mesh_binary_validation.hh"
#include "dev/ai_mesh/mesh_canonical.hh"
#include "dev/ai_mesh/mesh_hash.hh"

namespace gem5
{
namespace ai_mesh
{
namespace mesh_binary_detail
{

namespace
{

using namespace mesh_abi::semantic_abi;

template <typename Value>
struct IsArray : std::false_type
{};

template <typename Value, size_t Size>
struct IsArray<std::array<Value, Size>> : std::true_type
{};

class CanonicalWriter
{
  public:
    CanonicalWriter(
        const ProgramStorage &program,
        mesh_abi::CanonicalProjection projection, std::ostream &out,
        MeshLoadError &error)
      : program(program), projection(projection), out(out), error(error)
    {}

    bool write()
    {
        if (!out.put('{'))
            return streamFailure();
        bool first = true;
        for (const auto &descriptor : mesh_abi::kProgramCanonicalFields) {
            if (projection == mesh_abi::CanonicalProjection::Semantic &&
                !descriptor.semantic)
                continue;
            if (!property(descriptor.name, first))
                return false;
            switch (descriptor.kind) {
              case mesh_abi::ProgramFieldKind::Abi:
                if (!writeAbi())
                    return false;
                break;
              case mesh_abi::ProgramFieldKind::Bytes:
                if (!mesh_canonical::writeBytes(
                        out, program.header.arch_digest.data(),
                        program.header.arch_digest.size()))
                    return streamFailure();
                break;
              case mesh_abi::ProgramFieldKind::TransportSections:
                if (!writeSections())
                    return false;
                break;
              case mesh_abi::ProgramFieldKind::SemanticRef:
                if (!writeSemantic(program.metadata.semantics, false))
                    return false;
                break;
              case mesh_abi::ProgramFieldKind::Digest:
                if (!mesh_canonical::writeBytes(
                        out, program.metadata.semantic_sha256.data(),
                        program.metadata.semantic_sha256.size()))
                    return streamFailure();
                break;
            }
        }
        return out.put('}') ? true : streamFailure();
    }

    bool writeSemanticValue(const SemanticRef &value)
    {
        return writeSemantic(value, false);
    }

  private:
    using Action = std::function<bool()>;

    bool streamFailure()
    {
        error = {
            mesh_diagnostics::E_ABI_CORRUPT,
            "canonical output stream failed"};
        return false;
    }

    bool invalid(const char *message)
    {
        error = {mesh_diagnostics::E_ABI_CORRUPT, message};
        return false;
    }

    bool property(std::string_view name, bool &first)
    {
        if (!first && !out.put(','))
            return streamFailure();
        first = false;
        if (!mesh_canonical::writeString(out, name) || !out.put(':'))
            return streamFailure();
        return true;
    }

    bool writeAbi()
    {
        if (!out.put('{'))
            return streamFailure();
        bool first = true;
        const bool visited = mesh_abi::visitProgramAbiFieldsCanonical(
            program.header.abi_major, program.header.abi_minor,
            program.metadata.min_reader_minor,
            program.header.required_features,
            [&](const auto &descriptor, auto value) {
                return property(descriptor.name, first) &&
                    mesh_canonical::writeUnsigned(out, value);
            });
        if (!visited)
            return streamFailure();
        return out.put('}') ? true : streamFailure();
    }

    bool writeSections()
    {
        if (!out.put('{'))
            return streamFailure();
        bool first = true;
        bool valid = true;
        mesh_abi::visitTransportTablesCanonical(
            program.transport, projection,
            [&](const auto &descriptor, const auto &table) {
                if (!property(descriptor.name, first) ||
                    !writeTransportTable(descriptor, table)) {
                    valid = false;
                    return false;
                }
                return true;
            });
        if (!valid)
            return false;
        return out.put('}') ? true : streamFailure();
    }

    template <typename Value>
    bool writeTransportTable(
        const mesh_abi::TransportSectionDescriptor &,
        const std::vector<Value> &table)
    {
        if (!out.put('['))
            return streamFailure();
        for (size_t index = 0; index < table.size(); ++index) {
            if (index != 0 && !out.put(','))
                return streamFailure();
            if constexpr (std::is_same_v<Value, std::string>) {
                if (!mesh_canonical::writeString(out, table[index]))
                    return streamFailure();
            } else if constexpr (
                std::is_same_v<Value, mesh_abi::TypedOpAttr>) {
                if (!writeAttr(table[index]))
                    return false;
            } else if (!writeTransportRecord(table[index])) {
                return false;
            }
        }
        return out.put(']') ? true : streamFailure();
    }

    bool writeAttr(const mesh_abi::TypedOpAttr &value)
    {
        if (!out.put('{'))
            return streamFailure();
        bool first = true;
        bool kindWritten = false;
        auto writeKind = [&]() {
            kindWritten = true;
            return property("kind", first) &&
                mesh_canonical::writeUnsigned(out, value.kind);
        };
        const bool visited = mesh_abi::visitAttrPayloadCanonical(
            value.kind, value.payload, projection,
            [&](const auto &descriptor, const auto &field) {
                if (!kindWritten && std::string_view("kind") < descriptor.name &&
                    !writeKind())
                    return false;
                return property(descriptor.name, first) &&
                    writeTransportValue(descriptor, field);
            });
        if (!visited)
            return invalid("attribute kind and payload disagree");
        if (!kindWritten && !writeKind())
            return false;
        return out.put('}') ? true : streamFailure();
    }

    template <typename Value>
    bool writeTransportRecord(const Value &value)
    {
        if (!out.put('{'))
            return streamFailure();
        bool first = true;
        bool valid = true;
        mesh_abi::visitTransportFieldsCanonical(
            value, projection,
            [&](const auto &descriptor, const auto &field) {
                if (!property(descriptor.name, first) ||
                    !writeTransportValue(descriptor, field)) {
                    valid = false;
                    return false;
                }
                return true;
            });
        if (!valid)
            return false;
        return out.put('}') ? true : streamFailure();
    }

    template <typename Value>
    bool writeTransportValue(
        const mesh_abi::TransportFieldDescriptor &descriptor,
        const Value &value)
    {
        if (descriptor.string_pool != mesh_abi::TransportStringPool::None) {
            if constexpr (std::is_integral_v<Value>) {
                const auto &strings = descriptor.string_pool ==
                        mesh_abi::TransportStringPool::Transport ?
                    program.transport.strings : program.semantic_strings;
                if (value == 0 || static_cast<uint64_t>(value) > strings.size())
                    return invalid("transport string reference is invalid");
                return mesh_canonical::writeString(out, strings[value - 1]) ||
                    streamFailure();
            }
            return invalid("transport string reference has wrong type");
        }
        if constexpr (std::is_integral_v<Value>) {
            return mesh_canonical::writeUnsigned(out, value) || streamFailure();
        } else if constexpr (IsArray<Value>::value) {
            using Element = typename Value::value_type;
            if constexpr (std::is_same_v<Element, uint8_t>) {
                return mesh_canonical::writeBytes(
                           out, value.data(), value.size()) ||
                    streamFailure();
            } else {
                if (!out.put('['))
                    return streamFailure();
                for (size_t index = 0; index < value.size(); ++index) {
                    if ((index != 0 && !out.put(',')) ||
                        !mesh_canonical::writeUnsigned(out, value[index]))
                        return streamFailure();
                }
                return out.put(']') ? true : streamFailure();
            }
        } else {
            return writeTransportRecord(value);
        }
    }

    bool writeSemantic(const SemanticRef &reference, bool discriminator)
    {
        actions.clear();
        scheduleReference(reference, discriminator);
        while (!actions.empty()) {
            Action action = std::move(actions.back());
            actions.pop_back();
            if (!action())
                return false;
        }
        return true;
    }

    void scheduleReference(const SemanticRef &reference, bool discriminator)
    {
        actions.emplace_back([this, reference, discriminator]() {
            return openRecord(reference, discriminator);
        });
    }

    bool openRecord(const SemanticRef &reference, bool discriminator)
    {
        bool matched = false;
        bool valid = true;
        mesh_abi::semantic_abi::dispatchSemanticTable(
            reference.section_type, program.semantic_tables,
            [&](const auto &traits, const auto &table) {
                matched = true;
                if (reference.row_id == 0 || reference.row_id > table.size()) {
                    valid = invalid("semantic row is out of bounds");
                    return false;
                }
                valid = scheduleRecord(
                    table[reference.row_id - 1], traits.json_tag,
                    discriminator);
                return valid;
            });
        return matched ? valid : invalid("semantic section type is invalid");
    }

    template <typename Value>
    bool scheduleRecord(
        const Value &value, std::string_view tag, bool discriminator)
    {
        if (!out.put('{'))
            return streamFailure();
        if (discriminator &&
            (!mesh_canonical::writeString(out, "$type") || !out.put(':') ||
             !mesh_canonical::writeString(out, tag)))
            return streamFailure();
        std::vector<Action> fields;
        size_t fieldIndex = 0;
        mesh_abi::semantic_abi::visitSemanticFieldsCanonical(
            value, [&](const auto &descriptor, bool present,
                       const auto &field) {
                if (!present)
                    return true;
                const bool comma = discriminator || fieldIndex != 0;
                fields.emplace_back(
                    [this, descriptor, field, comma]() {
                        if (comma && !out.put(','))
                            return streamFailure();
                        if (!mesh_canonical::writeString(out, descriptor.name) ||
                            !out.put(':'))
                            return streamFailure();
                        return scheduleSemanticValue(descriptor, field);
                    });
                ++fieldIndex;
                return true;
            });
        actions.emplace_back([this]() {
            return out.put('}') ? true : streamFailure();
        });
        actions.insert(actions.end(), fields.rbegin(), fields.rend());
        return true;
    }

    bool scheduleSemanticValue(
        const SemanticFieldDescriptor &descriptor,
        const SemanticRef &value)
    {
        scheduleReference(value, descriptor.json_union_discriminator);
        return true;
    }

    bool scheduleSemanticValue(
        const SemanticFieldDescriptor &, const StringRef &value)
    {
        if (value.string_id == 0 ||
            value.string_id > program.semantic_strings.size())
            return invalid("semantic string reference is invalid");
        return mesh_canonical::writeString(
                   out, program.semantic_strings[value.string_id - 1]) ||
            streamFailure();
    }

    bool scheduleSemanticValue(
        const SemanticFieldDescriptor &descriptor, const ListSpan &value)
    {
        switch (descriptor.kind) {
          case SemanticFieldKind::U64List:
            return writeUnsignedSpan(value, program.semantic_u64_values);
          case SemanticFieldKind::IntegerList:
            return writeIntegerSpan(value);
          case SemanticFieldKind::Bytes:
            return writeByteSpan(value);
          case SemanticFieldKind::RefList:
            return scheduleReferenceSpan(
                value, descriptor.json_union_discriminator);
          default:
            return invalid("semantic span has an invalid field kind");
        }
    }

    bool scheduleSemanticValue(
        const SemanticFieldDescriptor &, const ScalarValue &value)
    {
        switch (value.kind) {
          case mesh_abi::semantic_abi::kScalarKindBool:
            return mesh_canonical::writeBoolean(out, value.payload != 0) ||
                streamFailure();
          case mesh_abi::semantic_abi::kScalarKindI64: {
            int64_t decoded = 0;
            std::memcpy(&decoded, &value.payload, sizeof(decoded));
            return mesh_canonical::writeSigned(out, decoded) || streamFailure();
          }
          case mesh_abi::semantic_abi::kScalarKindU64:
            return mesh_canonical::writeUnsigned(out, value.payload) ||
                streamFailure();
          case mesh_abi::semantic_abi::kScalarKindF64: {
            double decoded = 0;
            std::memcpy(&decoded, &value.payload, sizeof(decoded));
            return mesh_canonical::writeFloat(out, decoded) || streamFailure();
          }
          default:
            return invalid("semantic scalar kind is invalid");
        }
    }

    template <typename Value>
    bool scheduleSemanticValue(
        const SemanticFieldDescriptor &, const Value &value)
    {
        if constexpr (std::is_enum_v<Value>) {
            const auto python = SemanticEnumTraits<Value>::pythonValue(value);
            if (python.kind == SemanticEnumPythonKind::Integer)
                return mesh_canonical::writeSigned(
                           out, python.integer_value) ||
                    streamFailure();
            if (python.kind == SemanticEnumPythonKind::String)
                return mesh_canonical::writeString(
                           out, python.string_value) ||
                    streamFailure();
            return invalid("semantic enum value is invalid");
        } else if constexpr (std::is_same_v<Value, bool>) {
            return mesh_canonical::writeBoolean(out, value) || streamFailure();
        } else if constexpr (std::is_same_v<Value, double>) {
            return mesh_canonical::writeFloat(out, value) || streamFailure();
        } else if constexpr (std::is_signed_v<Value>) {
            return mesh_canonical::writeSigned(out, value) || streamFailure();
        } else if constexpr (std::is_unsigned_v<Value>) {
            return mesh_canonical::writeUnsigned(out, value) || streamFailure();
        }
        return invalid("semantic field type is unsupported");
    }

    template <typename Value>
    bool writeUnsignedSpan(
        const ListSpan &span, const std::vector<Value> &values)
    {
        if (span.begin > values.size() || span.count > values.size() - span.begin)
            return invalid("semantic list span is out of bounds");
        if (!out.put('['))
            return streamFailure();
        for (uint64_t index = span.begin;
             index < static_cast<uint64_t>(span.begin) + span.count; ++index) {
            if ((index != span.begin && !out.put(',')) ||
                !mesh_canonical::writeUnsigned(out, values[index]))
                return streamFailure();
        }
        return out.put(']') ? true : streamFailure();
    }

    bool writeIntegerSpan(const ListSpan &span)
    {
        const auto &values = program.semantic_integer_values;
        if (span.begin > values.size() || span.count > values.size() - span.begin)
            return invalid("semantic integer span is out of bounds");
        if (!out.put('['))
            return streamFailure();
        for (uint64_t index = span.begin;
             index < static_cast<uint64_t>(span.begin) + span.count; ++index) {
            if (index != span.begin && !out.put(','))
                return streamFailure();
            const auto &value = values[index];
            if (value.kind == mesh_abi::semantic_abi::kScalarKindI64) {
                int64_t decoded = 0;
                std::memcpy(&decoded, &value.payload, sizeof(decoded));
                if (!mesh_canonical::writeSigned(out, decoded))
                    return streamFailure();
            } else if (value.kind ==
                       mesh_abi::semantic_abi::kScalarKindU64) {
                if (!mesh_canonical::writeUnsigned(out, value.payload))
                    return streamFailure();
            } else {
                return invalid("semantic integer kind is invalid");
            }
        }
        return out.put(']') ? true : streamFailure();
    }

    bool writeByteSpan(const ListSpan &span)
    {
        if (span.begin > program.semantic_bytes.size() ||
            span.count > program.semantic_bytes.size() - span.begin)
            return invalid("semantic byte span is out of bounds");
        const uint8_t *data = span.count == 0 ? nullptr :
            program.semantic_bytes.data() + span.begin;
        return mesh_canonical::writeBytes(
                   out, data, span.count) ||
            streamFailure();
    }

    bool scheduleReferenceSpan(const ListSpan &span, bool discriminator)
    {
        if (span.begin > program.semantic_references.size() ||
            span.count > program.semantic_references.size() - span.begin)
            return invalid("semantic reference span is out of bounds");
        if (!out.put('['))
            return streamFailure();
        actions.emplace_back([this]() {
            return out.put(']') ? true : streamFailure();
        });
        for (uint64_t index =
                 static_cast<uint64_t>(span.begin) + span.count;
             index > span.begin; --index) {
            const auto reference = program.semantic_references[index - 1];
            const bool comma = index - 1 != span.begin;
            actions.emplace_back([this, reference, discriminator, comma]() {
                if (comma && !out.put(','))
                    return streamFailure();
                scheduleReference(reference, discriminator);
                return true;
            });
        }
        return true;
    }

    const ProgramStorage &program;
    mesh_abi::CanonicalProjection projection;
    std::ostream &out;
    MeshLoadError &error;
    std::vector<Action> actions;
};

}

bool
writeProgramCanonical(
    const ProgramStorage &program, mesh_abi::CanonicalProjection projection,
    std::ostream &out, MeshLoadError &error)
{
    if (!validateProgramStorage(program, error))
        return false;
    CanonicalWriter writer(program, projection, out, error);
    return writer.write();
}

bool
writeSemanticValueCanonical(
    const ProgramStorage &program, const SemanticRef &value,
    std::ostream &out, MeshLoadError &error)
{
    CanonicalWriter writer(
        program, mesh_abi::CanonicalProjection::Semantic, out, error);
    return writer.writeSemanticValue(value);
}

bool
validateProgramSemanticChecksum(
    const ProgramStorage &program, MeshLoadError &error)
{
    mesh_hash::Sha256StreamBuffer buffer;
    std::ostream stream(&buffer);
    if (!writeProgramCanonical(
            program, mesh_abi::CanonicalProjection::Semantic,
            stream, error))
        return false;
    if (buffer.digest() != program.metadata.semantic_sha256) {
        error = {
            mesh_diagnostics::E_ABI_CHECKSUM,
            "semantic checksum is invalid"};
        return false;
    }
    return true;
}

}
}
}
