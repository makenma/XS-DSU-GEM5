#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_FIELDS_COMMON_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_FIELDS_COMMON_HH

inline constexpr std::array<uint16_t, 6> kAddLhsTargets = {260, 263, 258, 262, 261, 259};
inline constexpr SemanticFieldDescriptor kAddLhsField = {"lhs", SemanticFieldKind::Ref, 0ull, kAddLhsTargets.data(), 6, true};
inline constexpr std::array<uint16_t, 6> kAddRhsTargets = {260, 263, 258, 262, 261, 259};
inline constexpr SemanticFieldDescriptor kAddRhsField = {"rhs", SemanticFieldKind::Ref, 0ull, kAddRhsTargets.data(), 6, true};

template <typename Visitor> bool visitSemanticFieldsWire(const Add &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kAddLhsField, true, value.lhs)) return false;
    if (!visitor(kAddRhsField, true, value.rhs)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const Add &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kAddLhsField, true, value.lhs)) return false;
    if (!visitor(kAddRhsField, true, value.rhs)) return false;
    return true;
}

inline constexpr std::array<uint16_t, 6> kCeilDivByConstValueTargets = {260, 263, 258, 262, 261, 259};
inline constexpr SemanticFieldDescriptor kCeilDivByConstValueField = {"value", SemanticFieldKind::Ref, 0ull, kCeilDivByConstValueTargets.data(), 6, true};
inline constexpr SemanticFieldDescriptor kCeilDivByConstDivisorField = {"divisor", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const CeilDivByConst &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kCeilDivByConstValueField, true, value.value)) return false;
    if (!visitor(kCeilDivByConstDivisorField, true, value.divisor)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const CeilDivByConst &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kCeilDivByConstDivisorField, true, value.divisor)) return false;
    if (!visitor(kCeilDivByConstValueField, true, value.value)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kConstValueField = {"value", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const Const &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kConstValueField, true, value.value)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const Const &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kConstValueField, true, value.value)) return false;
    return true;
}

inline constexpr std::array<uint16_t, 6> kFloorDivByConstValueTargets = {260, 263, 258, 262, 261, 259};
inline constexpr SemanticFieldDescriptor kFloorDivByConstValueField = {"value", SemanticFieldKind::Ref, 0ull, kFloorDivByConstValueTargets.data(), 6, true};
inline constexpr SemanticFieldDescriptor kFloorDivByConstDivisorField = {"divisor", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const FloorDivByConst &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kFloorDivByConstValueField, true, value.value)) return false;
    if (!visitor(kFloorDivByConstDivisorField, true, value.divisor)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const FloorDivByConst &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kFloorDivByConstDivisorField, true, value.divisor)) return false;
    if (!visitor(kFloorDivByConstValueField, true, value.value)) return false;
    return true;
}

inline constexpr std::array<uint16_t, 6> kMulByConstValueTargets = {260, 263, 258, 262, 261, 259};
inline constexpr SemanticFieldDescriptor kMulByConstValueField = {"value", SemanticFieldKind::Ref, 0ull, kMulByConstValueTargets.data(), 6, true};
inline constexpr SemanticFieldDescriptor kMulByConstFactorField = {"factor", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const MulByConst &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kMulByConstValueField, true, value.value)) return false;
    if (!visitor(kMulByConstFactorField, true, value.factor)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const MulByConst &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kMulByConstFactorField, true, value.factor)) return false;
    if (!visitor(kMulByConstValueField, true, value.value)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kSymbolSymbolIdField = {"symbol_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kSymbolNameField = {"name", SemanticFieldKind::String, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kSymbolMinimumField = {"minimum", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kSymbolMaximumField = {"maximum", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kSymbolMultipleOfField = {"multiple_of", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const Symbol &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kSymbolSymbolIdField, true, value.symbol_id)) return false;
    if (!visitor(kSymbolNameField, true, value.name)) return false;
    if (!visitor(kSymbolMinimumField, true, value.minimum)) return false;
    if (!visitor(kSymbolMaximumField, true, value.maximum)) return false;
    if (!visitor(kSymbolMultipleOfField, true, value.multiple_of)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const Symbol &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kSymbolMaximumField, true, value.maximum)) return false;
    if (!visitor(kSymbolMinimumField, true, value.minimum)) return false;
    if (!visitor(kSymbolMultipleOfField, true, value.multiple_of)) return false;
    if (!visitor(kSymbolNameField, true, value.name)) return false;
    if (!visitor(kSymbolSymbolIdField, true, value.symbol_id)) return false;
    return true;
}

#endif
