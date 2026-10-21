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
    E_ABI_FEATURE = 20,
    E_ABI_CHECKSUM = 21,
    E_ABI_SECTION_RANGE = 22,
    E_ARCH_DIGEST = 23,
    E_RUNTIME_DEADLOCK = 24,
    E_AXI_RESPONSE = 25,
    E_TRAFFIC_MISMATCH = 26,
    E_ABI_ENUM = 27,
    E_ABI_RESERVED = 28,
    E_ABI_DUPLICATE = 29,
    E_ABI_ORDER = 30,
    E_ABI_BOUNDS = 31,
    E_ABI_OVERFLOW = 32,
    E_ABI_CORRUPT = 33,
    E_ENGINE_MISMATCH = 34,
    E_EVENT_NO_PRODUCER = 35,
    E_STREAM_CONTRACT = 36,
    E_LIFECYCLE = 37,
    E_RELOCATION = 38,
    E_CAPABILITY_MISMATCH = 39,
    E_ARCH_PARTITION = 40,
    E_CAPACITY_PLAN = 41,
    E_MOE_UID = 42,
    E_MOE_RNG = 43,
    E_MOE_PROVIDER = 44,
    E_MOE_ROUTE_REPLAY = 45,
    E_MOE_TOPK_DUP = 46,
    E_MOE_PROFILE = 47,
    E_MOE_KEY_COLLISION = 48,
    E_MOE_CACHE = 49,
    E_MOE_TRAFFIC = 50,
    E_MOE_CAPACITY = 51,
    E_MOE_ROUTE_DIGEST = 52,
    E_MOE_SELECTION_V = 53,
    E_MOE_MATERIALIZATION_V = 54,
    E_MOE_MATERIALIZE_CAPACITY = 55,
    E_MOE_DISPATCH_BYTES = 56,
    E_MOE_COMBINE_BYTES = 57,
    E_REQUEST_PROFILE = 58,
    E_REQUEST_PROFILE_KEY = 59,
    E_BINDING_ROLE = 60,
    E_HOST_IO_SIZE_MISMATCH = 61,
    E_OUTPUT_CHUNK_MISMATCH = 62,
    E_KV_TOKEN_MISMATCH = 63,
    E_KV_STATE = 64,
    E_KV_STALE_GENERATION = 65,
    E_KV_CONTRACT_MISMATCH = 66,
    E_KV_FLAG_COMBINATION = 67,
    E_AGENT_PROTOCOL_FATAL = 68,
    E_CHECKPOINT_BOUNDARY = 69,
    E_CHECKPOINT_CONFIG = 70,
    E_CHECKPOINT_STATE = 71,
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

inline constexpr char kCatalogSha256[] = "87760892d7620cd847f7b2855f53d11d9082c7036056ebd93726cee7918cba33";

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
inline constexpr char E_ABI_FEATURE[] = "E_ABI_FEATURE";
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
inline constexpr char E_ARCH_PARTITION[] = "E_ARCH_PARTITION";
inline constexpr char E_CAPACITY_PLAN[] = "E_CAPACITY_PLAN";
inline constexpr char E_MOE_UID[] = "E_MOE_UID";
inline constexpr char E_MOE_RNG[] = "E_MOE_RNG";
inline constexpr char E_MOE_PROVIDER[] = "E_MOE_PROVIDER";
inline constexpr char E_MOE_ROUTE_REPLAY[] = "E_MOE_ROUTE_REPLAY";
inline constexpr char E_MOE_TOPK_DUP[] = "E_MOE_TOPK_DUP";
inline constexpr char E_MOE_PROFILE[] = "E_MOE_PROFILE";
inline constexpr char E_MOE_KEY_COLLISION[] = "E_MOE_KEY_COLLISION";
inline constexpr char E_MOE_CACHE[] = "E_MOE_CACHE";
inline constexpr char E_MOE_TRAFFIC[] = "E_MOE_TRAFFIC";
inline constexpr char E_MOE_CAPACITY[] = "E_MOE_CAPACITY";
inline constexpr char E_MOE_ROUTE_DIGEST[] = "E_MOE_ROUTE_DIGEST";
inline constexpr char E_MOE_SELECTION_V[] = "E_MOE_SELECTION_V";
inline constexpr char E_MOE_MATERIALIZATION_V[] = "E_MOE_MATERIALIZATION_V";
inline constexpr char E_MOE_MATERIALIZE_CAPACITY[] = "E_MOE_MATERIALIZE_CAPACITY";
inline constexpr char E_MOE_DISPATCH_BYTES[] = "E_MOE_DISPATCH_BYTES";
inline constexpr char E_MOE_COMBINE_BYTES[] = "E_MOE_COMBINE_BYTES";
inline constexpr char E_REQUEST_PROFILE[] = "E_REQUEST_PROFILE";
inline constexpr char E_REQUEST_PROFILE_KEY[] = "E_REQUEST_PROFILE_KEY";
inline constexpr char E_BINDING_ROLE[] = "E_BINDING_ROLE";
inline constexpr char E_HOST_IO_SIZE_MISMATCH[] = "E_HOST_IO_SIZE_MISMATCH";
inline constexpr char E_OUTPUT_CHUNK_MISMATCH[] = "E_OUTPUT_CHUNK_MISMATCH";
inline constexpr char E_KV_TOKEN_MISMATCH[] = "E_KV_TOKEN_MISMATCH";
inline constexpr char E_KV_STATE[] = "E_KV_STATE";
inline constexpr char E_KV_STALE_GENERATION[] = "E_KV_STALE_GENERATION";
inline constexpr char E_KV_CONTRACT_MISMATCH[] = "E_KV_CONTRACT_MISMATCH";
inline constexpr char E_KV_FLAG_COMBINATION[] = "E_KV_FLAG_COMBINATION";
inline constexpr char E_AGENT_PROTOCOL_FATAL[] = "E_AGENT_PROTOCOL_FATAL";
inline constexpr char E_CHECKPOINT_BOUNDARY[] = "E_CHECKPOINT_BOUNDARY";
inline constexpr char E_CHECKPOINT_CONFIG[] = "E_CHECKPOINT_CONFIG";
inline constexpr char E_CHECKPOINT_STATE[] = "E_CHECKPOINT_STATE";

inline constexpr std::array<Definition, 72> kDefinitions{{
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
    {DiagnosticCode::E_ABI_FEATURE, E_ABI_FEATURE, Severity::Error, "ABI section feature contract is invalid"},
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
    {DiagnosticCode::E_ARCH_PARTITION, E_ARCH_PARTITION, Severity::Error, "SRAM partition contract is invalid"},
    {DiagnosticCode::E_CAPACITY_PLAN, E_CAPACITY_PLAN, Severity::Error, "capacity plan"},
    {DiagnosticCode::E_MOE_UID, E_MOE_UID, Severity::Error, "moe uid"},
    {DiagnosticCode::E_MOE_RNG, E_MOE_RNG, Severity::Error, "moe rng"},
    {DiagnosticCode::E_MOE_PROVIDER, E_MOE_PROVIDER, Severity::Error, "moe provider"},
    {DiagnosticCode::E_MOE_ROUTE_REPLAY, E_MOE_ROUTE_REPLAY, Severity::Error, "moe route replay"},
    {DiagnosticCode::E_MOE_TOPK_DUP, E_MOE_TOPK_DUP, Severity::Error, "moe topk dup"},
    {DiagnosticCode::E_MOE_PROFILE, E_MOE_PROFILE, Severity::Error, "moe profile"},
    {DiagnosticCode::E_MOE_KEY_COLLISION, E_MOE_KEY_COLLISION, Severity::Error, "moe key collision"},
    {DiagnosticCode::E_MOE_CACHE, E_MOE_CACHE, Severity::Error, "moe cache"},
    {DiagnosticCode::E_MOE_TRAFFIC, E_MOE_TRAFFIC, Severity::Error, "moe traffic"},
    {DiagnosticCode::E_MOE_CAPACITY, E_MOE_CAPACITY, Severity::Error, "moe capacity"},
    {DiagnosticCode::E_MOE_ROUTE_DIGEST, E_MOE_ROUTE_DIGEST, Severity::Error, "moe route digest"},
    {DiagnosticCode::E_MOE_SELECTION_V, E_MOE_SELECTION_V, Severity::Error, "moe selection v"},
    {DiagnosticCode::E_MOE_MATERIALIZATION_V, E_MOE_MATERIALIZATION_V, Severity::Error, "moe materialization v"},
    {DiagnosticCode::E_MOE_MATERIALIZE_CAPACITY, E_MOE_MATERIALIZE_CAPACITY, Severity::Error, "moe materialize capacity"},
    {DiagnosticCode::E_MOE_DISPATCH_BYTES, E_MOE_DISPATCH_BYTES, Severity::Error, "moe dispatch bytes"},
    {DiagnosticCode::E_MOE_COMBINE_BYTES, E_MOE_COMBINE_BYTES, Severity::Error, "moe combine bytes"},
    {DiagnosticCode::E_REQUEST_PROFILE, E_REQUEST_PROFILE, Severity::Error, "request profile"},
    {DiagnosticCode::E_REQUEST_PROFILE_KEY, E_REQUEST_PROFILE_KEY, Severity::Error, "request profile key"},
    {DiagnosticCode::E_BINDING_ROLE, E_BINDING_ROLE, Severity::Error, "binding role"},
    {DiagnosticCode::E_HOST_IO_SIZE_MISMATCH, E_HOST_IO_SIZE_MISMATCH, Severity::Error, "host io size mismatch"},
    {DiagnosticCode::E_OUTPUT_CHUNK_MISMATCH, E_OUTPUT_CHUNK_MISMATCH, Severity::Error, "output chunk mismatch"},
    {DiagnosticCode::E_KV_TOKEN_MISMATCH, E_KV_TOKEN_MISMATCH, Severity::Error, "kv token mismatch"},
    {DiagnosticCode::E_KV_STATE, E_KV_STATE, Severity::Error, "kv state"},
    {DiagnosticCode::E_KV_STALE_GENERATION, E_KV_STALE_GENERATION, Severity::Error, "kv stale generation"},
    {DiagnosticCode::E_KV_CONTRACT_MISMATCH, E_KV_CONTRACT_MISMATCH, Severity::Error, "kv contract mismatch"},
    {DiagnosticCode::E_KV_FLAG_COMBINATION, E_KV_FLAG_COMBINATION, Severity::Error, "kv flag combination"},
    {DiagnosticCode::E_AGENT_PROTOCOL_FATAL, E_AGENT_PROTOCOL_FATAL, Severity::Error, "agent protocol fatal"},
    {DiagnosticCode::E_CHECKPOINT_BOUNDARY, E_CHECKPOINT_BOUNDARY, Severity::Error, "checkpoint requires a drained runtime"},
    {DiagnosticCode::E_CHECKPOINT_CONFIG, E_CHECKPOINT_CONFIG, Severity::Error, "checkpoint runtime configuration differs"},
    {DiagnosticCode::E_CHECKPOINT_STATE, E_CHECKPOINT_STATE, Severity::Error, "checkpoint state is inconsistent"},
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
