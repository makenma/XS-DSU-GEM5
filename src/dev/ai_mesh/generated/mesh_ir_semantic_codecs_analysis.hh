#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_CODECS_ANALYSIS_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_CODECS_ANALYSIS_HH

inline bool decodeExecutionWorkPhase(const uint8_t *data, ExecutionWorkPhase &out, AbiError &error)
{
    out = ExecutionWorkPhase{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ExecutionWorkPhase presence mask has undeclared bits"}; return false; }
    if (!decodeListSpan(data + 8, out.work, error)) return false;
    return true;
}

inline std::array<uint8_t, kExecutionWorkPhaseBytes> encodeExecutionWorkPhase(const ExecutionWorkPhase &value)
{
    std::array<uint8_t, kExecutionWorkPhaseBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeListSpan(data.data() + 8, value.work);
    return data;
}

inline bool decodeWorkEstimate(const uint8_t *data, WorkEstimate &out, AbiError &error)
{
    out = WorkEstimate{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "WorkEstimate presence mask has undeclared bits"}; return false; }
    if (mesh_abi::rdU32(data + 8 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "WorkEstimate.engine reserved bytes are nonzero"}; return false; }
    if (!validEngine(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "WorkEstimate.engine is invalid"}; return false; }
    out.engine = static_cast<Engine>(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset));
    if (mesh_abi::rdU32(data + 16 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "WorkEstimate.unit reserved bytes are nonzero"}; return false; }
    if (!validWorkUnit(mesh_abi::rdU32(data + 16 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "WorkEstimate.unit is invalid"}; return false; }
    out.unit = static_cast<WorkUnit>(mesh_abi::rdU32(data + 16 + kEnumValueValueOffset));
    if (mesh_abi::rdU32(data + 24 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "WorkEstimate.dtype reserved bytes are nonzero"}; return false; }
    if (!validDType(mesh_abi::rdU32(data + 24 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "WorkEstimate.dtype is invalid"}; return false; }
    out.dtype = static_cast<DType>(mesh_abi::rdU32(data + 24 + kEnumValueValueOffset));
    out.operations = mesh_abi::rdU64(data + 32);
    return true;
}

inline std::array<uint8_t, kWorkEstimateBytes> encodeWorkEstimate(const WorkEstimate &value)
{
    std::array<uint8_t, kWorkEstimateBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU32(data.data() + 8 + kEnumValueValueOffset, static_cast<uint32_t>(value.engine));
    mesh_abi::wrU32(data.data() + 16 + kEnumValueValueOffset, static_cast<uint32_t>(value.unit));
    mesh_abi::wrU32(data.data() + 24 + kEnumValueValueOffset, static_cast<uint32_t>(value.dtype));
    mesh_abi::wrU64(data.data() + 32, value.operations);
    return data;
}

#endif
