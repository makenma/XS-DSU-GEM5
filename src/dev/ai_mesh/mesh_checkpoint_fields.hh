#ifndef DEV_AI_MESH_MESH_CHECKPOINT_FIELDS_HH
#define DEV_AI_MESH_MESH_CHECKPOINT_FIELDS_HH

#include "base/checkpoint_fields.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5::ai_mesh::mesh_abi
{
template<class Archive>
void serialize(Archive &archive, ErrorSourceKey &value, unsigned)
{ checkpoint::aggregate(archive, value); }
template<class Archive>
void serialize(Archive &archive, WeightCacheBaseKey &value, unsigned)
{ checkpoint::aggregate(archive, value); }
template<class Archive>
void serialize(Archive &archive, WeightFillKey &value, unsigned)
{ checkpoint::aggregate(archive, value); }
}

namespace gem5::ai_mesh::agent_abi
{
template<class Archive>
void serialize(Archive &archive, BindingRecord &value, unsigned)
{ checkpoint::aggregate(archive, value); }
}
#endif
