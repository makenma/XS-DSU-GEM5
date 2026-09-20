#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_DIAGNOSTICS_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_DIAGNOSTICS_HH

#include <array>
#include <cstdint>
#include <string_view>

namespace gem5
{
namespace ai_mesh
{
namespace mesh_diagnostics
{

enum class DiagnosticCode : uint16_t
{
    E_EXPORT_UNSUPPORTED_OP = 0,
    E_EXPORT_GRAPH_BREAK = 1,
    E_EXPORT_STATE_MUTATION = 2,
    E_EXPORT_NON_TENSOR_IO = 3,
    E_EXPORT_DTYPE = 4,
    E_EXPORT_LAYOUT = 5,
    E_EXPORT_VERSION = 6,
    E_CONFIG = 7,
    E_SHAPE_UNBOUND = 8,
    E_SHAPE_PROFILE_MISMATCH = 9,
    E_PLACEMENT_INFEASIBLE = 10,
    E_SRAM_OOM = 11,
    E_DEPENDENCY_CYCLE = 12,
    E_EVENT_MULTIPLE_PRODUCERS = 13,
    E_TENSOR_NOT_RESIDENT = 14,
    E_DMA_RANGE = 15,
    E_DMA_4K_SPLIT = 16,
    E_P2P_UNMATCHED = 17,
    E_ABI_MAGIC = 18,
    E_ABI_VERSION = 19,
    E_ABI_CHECKSUM = 20,
    E_ABI_SECTION_RANGE = 21,
    E_ARCH_DIGEST = 22,
    E_RUNTIME_DEADLOCK = 23,
    E_AXI_RESPONSE = 24,
    E_TRAFFIC_MISMATCH = 25,
    E_ABI_ENUM = 26,
    E_ABI_RESERVED = 27,
    E_ABI_DUPLICATE = 28,
    E_ABI_ORDER = 29,
    E_ABI_BOUNDS = 30,
    E_ABI_OVERFLOW = 31,
    E_ABI_CORRUPT = 32,
    E_ENGINE_MISMATCH = 33,
    E_EVENT_NO_PRODUCER = 34,
    E_STREAM_CONTRACT = 35,
    E_LIFECYCLE = 36,
    E_RELOCATION = 37,
    E_CAPABILITY_MISMATCH = 38,
};

enum class Severity : uint8_t
{
    Error,
    Warning,
};

struct Definition
{
    DiagnosticCode id;
    std::string_view code;
    Severity severity;
    std::string_view message;
};

inline constexpr char kCatalogSha256[] = "2a5d0b81e9d7f41a3e61877c4d3c569ebd9ff8bb2f8f9e09c63489789edff6b4";

inline constexpr char E_EXPORT_UNSUPPORTED_OP[] = "E_EXPORT_UNSUPPORTED_OP";
inline constexpr char E_EXPORT_GRAPH_BREAK[] = "E_EXPORT_GRAPH_BREAK";
inline constexpr char E_EXPORT_STATE_MUTATION[] = "E_EXPORT_STATE_MUTATION";
inline constexpr char E_EXPORT_NON_TENSOR_IO[] = "E_EXPORT_NON_TENSOR_IO";
inline constexpr char E_EXPORT_DTYPE[] = "E_EXPORT_DTYPE";
inline constexpr char E_EXPORT_LAYOUT[] = "E_EXPORT_LAYOUT";
inline constexpr char E_EXPORT_VERSION[] = "E_EXPORT_VERSION";
inline constexpr char E_CONFIG[] = "E_CONFIG";
inline constexpr char E_SHAPE_UNBOUND[] = "E_SHAPE_UNBOUND";
inline constexpr char E_SHAPE_PROFILE_MISMATCH[] = "E_SHAPE_PROFILE_MISMATCH";
inline constexpr char E_PLACEMENT_INFEASIBLE[] = "E_PLACEMENT_INFEASIBLE";
inline constexpr char E_SRAM_OOM[] = "E_SRAM_OOM";
inline constexpr char E_DEPENDENCY_CYCLE[] = "E_DEPENDENCY_CYCLE";
inline constexpr char E_EVENT_MULTIPLE_PRODUCERS[] = "E_EVENT_MULTIPLE_PRODUCERS";
inline constexpr char E_TENSOR_NOT_RESIDENT[] = "E_TENSOR_NOT_RESIDENT";
inline constexpr char E_DMA_RANGE[] = "E_DMA_RANGE";
inline constexpr char E_DMA_4K_SPLIT[] = "E_DMA_4K_SPLIT";
inline constexpr char E_P2P_UNMATCHED[] = "E_P2P_UNMATCHED";
inline constexpr char E_ABI_MAGIC[] = "E_ABI_MAGIC";
inline constexpr char E_ABI_VERSION[] = "E_ABI_VERSION";
inline constexpr char E_ABI_CHECKSUM[] = "E_ABI_CHECKSUM";
inline constexpr char E_ABI_SECTION_RANGE[] = "E_ABI_SECTION_RANGE";
inline constexpr char E_ARCH_DIGEST[] = "E_ARCH_DIGEST";
inline constexpr char E_RUNTIME_DEADLOCK[] = "E_RUNTIME_DEADLOCK";
inline constexpr char E_AXI_RESPONSE[] = "E_AXI_RESPONSE";
inline constexpr char E_TRAFFIC_MISMATCH[] = "E_TRAFFIC_MISMATCH";
inline constexpr char E_ABI_ENUM[] = "E_ABI_ENUM";
inline constexpr char E_ABI_RESERVED[] = "E_ABI_RESERVED";
inline constexpr char E_ABI_DUPLICATE[] = "E_ABI_DUPLICATE";
inline constexpr char E_ABI_ORDER[] = "E_ABI_ORDER";
inline constexpr char E_ABI_BOUNDS[] = "E_ABI_BOUNDS";
inline constexpr char E_ABI_OVERFLOW[] = "E_ABI_OVERFLOW";
inline constexpr char E_ABI_CORRUPT[] = "E_ABI_CORRUPT";
inline constexpr char E_ENGINE_MISMATCH[] = "E_ENGINE_MISMATCH";
inline constexpr char E_EVENT_NO_PRODUCER[] = "E_EVENT_NO_PRODUCER";
inline constexpr char E_STREAM_CONTRACT[] = "E_STREAM_CONTRACT";
inline constexpr char E_LIFECYCLE[] = "E_LIFECYCLE";
inline constexpr char E_RELOCATION[] = "E_RELOCATION";
inline constexpr char E_CAPABILITY_MISMATCH[] = "E_CAPABILITY_MISMATCH";

inline constexpr std::array<Definition, 39> kDefinitions{{
    {DiagnosticCode::E_EXPORT_UNSUPPORTED_OP, E_EXPORT_UNSUPPORTED_OP, Severity::Error, "unsupported exported operator"},
    {DiagnosticCode::E_EXPORT_GRAPH_BREAK, E_EXPORT_GRAPH_BREAK, Severity::Error, "export could not produce a single graph"},
    {DiagnosticCode::E_EXPORT_STATE_MUTATION, E_EXPORT_STATE_MUTATION, Severity::Error, "exported graph mutates state"},
    {DiagnosticCode::E_EXPORT_NON_TENSOR_IO, E_EXPORT_NON_TENSOR_IO, Severity::Error, "exported graph has non-tensor user I/O"},
    {DiagnosticCode::E_EXPORT_DTYPE, E_EXPORT_DTYPE, Severity::Error, "exported graph uses an unsupported dtype"},
    {DiagnosticCode::E_EXPORT_LAYOUT, E_EXPORT_LAYOUT, Severity::Error, "exported graph uses an unsafe layout"},
    {DiagnosticCode::E_EXPORT_VERSION, E_EXPORT_VERSION, Severity::Error, "exported program version or dialect is unsupported"},
    {DiagnosticCode::E_CONFIG, E_CONFIG, Severity::Error, "configuration is invalid"},
    {DiagnosticCode::E_SHAPE_UNBOUND, E_SHAPE_UNBOUND, Severity::Error, "a shape symbol is unbound"},
    {DiagnosticCode::E_SHAPE_PROFILE_MISMATCH, E_SHAPE_PROFILE_MISMATCH, Severity::Error, "a shape profile violates exported constraints"},
    {DiagnosticCode::E_PLACEMENT_INFEASIBLE, E_PLACEMENT_INFEASIBLE, Severity::Error, "placement policy is infeasible"},
    {DiagnosticCode::E_SRAM_OOM, E_SRAM_OOM, Severity::Error, "SRAM capacity is insufficient"},
    {DiagnosticCode::E_DEPENDENCY_CYCLE, E_DEPENDENCY_CYCLE, Severity::Error, "dependency graph contains a cycle"},
    {DiagnosticCode::E_EVENT_MULTIPLE_PRODUCERS, E_EVENT_MULTIPLE_PRODUCERS, Severity::Error, "event has multiple producers"},
    {DiagnosticCode::E_TENSOR_NOT_RESIDENT, E_TENSOR_NOT_RESIDENT, Severity::Error, "tensor is not resident"},
    {DiagnosticCode::E_DMA_RANGE, E_DMA_RANGE, Severity::Error, "DMA range is invalid"},
    {DiagnosticCode::E_DMA_4K_SPLIT, E_DMA_4K_SPLIT, Severity::Error, "DMA burst crosses a 4 KiB boundary"},
    {DiagnosticCode::E_P2P_UNMATCHED, E_P2P_UNMATCHED, Severity::Error, "P2P transfer is unmatched"},
    {DiagnosticCode::E_ABI_MAGIC, E_ABI_MAGIC, Severity::Error, "binary magic is invalid"},
    {DiagnosticCode::E_ABI_VERSION, E_ABI_VERSION, Severity::Error, "ABI version is unsupported"},
    {DiagnosticCode::E_ABI_CHECKSUM, E_ABI_CHECKSUM, Severity::Error, "binary checksum is invalid"},
    {DiagnosticCode::E_ABI_SECTION_RANGE, E_ABI_SECTION_RANGE, Severity::Error, "binary section range is invalid"},
    {DiagnosticCode::E_ARCH_DIGEST, E_ARCH_DIGEST, Severity::Error, "architecture digest does not match"},
    {DiagnosticCode::E_RUNTIME_DEADLOCK, E_RUNTIME_DEADLOCK, Severity::Error, "runtime made no progress"},
    {DiagnosticCode::E_AXI_RESPONSE, E_AXI_RESPONSE, Severity::Error, "AXI response failed"},
    {DiagnosticCode::E_TRAFFIC_MISMATCH, E_TRAFFIC_MISMATCH, Severity::Error, "traffic does not match the oracle"},
    {DiagnosticCode::E_ABI_ENUM, E_ABI_ENUM, Severity::Error, "binary enum is invalid"},
    {DiagnosticCode::E_ABI_RESERVED, E_ABI_RESERVED, Severity::Error, "reserved binary data is nonzero"},
    {DiagnosticCode::E_ABI_DUPLICATE, E_ABI_DUPLICATE, Severity::Error, "binary identifier is duplicated"},
    {DiagnosticCode::E_ABI_ORDER, E_ABI_ORDER, Severity::Error, "binary ordering is invalid"},
    {DiagnosticCode::E_ABI_BOUNDS, E_ABI_BOUNDS, Severity::Error, "binary reference is out of bounds"},
    {DiagnosticCode::E_ABI_OVERFLOW, E_ABI_OVERFLOW, Severity::Error, "binary arithmetic overflowed"},
    {DiagnosticCode::E_ABI_CORRUPT, E_ABI_CORRUPT, Severity::Error, "binary data is corrupt"},
    {DiagnosticCode::E_ENGINE_MISMATCH, E_ENGINE_MISMATCH, Severity::Error, "command engine does not match opcode"},
    {DiagnosticCode::E_EVENT_NO_PRODUCER, E_EVENT_NO_PRODUCER, Severity::Error, "event has no producer"},
    {DiagnosticCode::E_STREAM_CONTRACT, E_STREAM_CONTRACT, Severity::Error, "stream contract is invalid"},
    {DiagnosticCode::E_LIFECYCLE, E_LIFECYCLE, Severity::Error, "lifecycle contract is invalid"},
    {DiagnosticCode::E_RELOCATION, E_RELOCATION, Severity::Error, "relocation is invalid"},
    {DiagnosticCode::E_CAPABILITY_MISMATCH, E_CAPABILITY_MISMATCH, Severity::Error, "architecture capability does not match"},
}};

constexpr const Definition *
findDefinition(DiagnosticCode code)
{
    for (const auto &definition : kDefinitions) {
        if (definition.id == code) {
            return &definition;
        }
    }
    return nullptr;
}

constexpr const Definition *
findDefinition(std::string_view code)
{
    for (const auto &definition : kDefinitions) {
        if (definition.code == code) {
            return &definition;
        }
    }
    return nullptr;
}

}
}
}

#endif
