#ifndef DEV_AI_MESH_MESH_JSON_HH
#define DEV_AI_MESH_MESH_JSON_HH

#include <optional>
#include <cstdint>
#include <string>
#include <boost/json.hpp>

namespace gem5::ai_mesh
{
std::optional<boost::json::value> parseMeshJson(const std::string &text, std::string &reason);
std::string canonicalMeshJson(const boost::json::value &value);
uint64_t meshJsonCounter(double value);
}
#endif
