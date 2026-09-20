#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_FIELDS_ANALYSIS_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_FIELDS_ANALYSIS_HH

inline constexpr std::array<uint16_t, 1> kExecutionWorkPhaseWorkTargets = {257};
inline constexpr SemanticFieldDescriptor kExecutionWorkPhaseWorkField = {"work", SemanticFieldKind::RefList, 0ull, kExecutionWorkPhaseWorkTargets.data(), 1, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ExecutionWorkPhase &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kExecutionWorkPhaseWorkField, true, value.work)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ExecutionWorkPhase &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kExecutionWorkPhaseWorkField, true, value.work)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kWorkEstimateEngineField = {"engine", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kWorkEstimateUnitField = {"unit", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kWorkEstimateDtypeField = {"dtype", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kWorkEstimateOperationsField = {"operations", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const WorkEstimate &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kWorkEstimateEngineField, true, value.engine)) return false;
    if (!visitor(kWorkEstimateUnitField, true, value.unit)) return false;
    if (!visitor(kWorkEstimateDtypeField, true, value.dtype)) return false;
    if (!visitor(kWorkEstimateOperationsField, true, value.operations)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const WorkEstimate &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kWorkEstimateDtypeField, true, value.dtype)) return false;
    if (!visitor(kWorkEstimateEngineField, true, value.engine)) return false;
    if (!visitor(kWorkEstimateOperationsField, true, value.operations)) return false;
    if (!visitor(kWorkEstimateUnitField, true, value.unit)) return false;
    return true;
}

#endif
