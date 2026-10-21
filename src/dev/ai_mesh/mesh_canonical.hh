#ifndef GEM5_DEV_AI_MESH_MESH_CANONICAL_HH
#define GEM5_DEV_AI_MESH_MESH_CANONICAL_HH

#include <cstddef>
#include <cstdint>
#include <iosfwd>
#include <string_view>

namespace gem5
{
namespace ai_mesh
{
namespace mesh_canonical
{

bool validUtf8(std::string_view value);
bool writeString(std::ostream &out, std::string_view value);
bool writeUnsigned(std::ostream &out, uint64_t value);
bool writeSigned(std::ostream &out, int64_t value);
bool writeFloat(std::ostream &out, double value);
bool writeBoolean(std::ostream &out, bool value);
bool writeBytes(std::ostream &out, const uint8_t *data, size_t size);

}
}
}

#endif
