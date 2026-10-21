#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_CODECS_COMMON_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_CODECS_COMMON_HH

inline bool decodeAdd(const uint8_t *data, Add &out, AbiError &error)
{
    out = Add{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "Add presence mask has undeclared bits"}; return false; }
    if (!decodeSemanticRef(data + 8, out.lhs, error)) return false;
    if (out.lhs.row_id == 0 || (out.lhs.section_type != 260 && out.lhs.section_type != 263 && out.lhs.section_type != 258 && out.lhs.section_type != 262 && out.lhs.section_type != 261 && out.lhs.section_type != 259)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "Add.lhs target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 16, out.rhs, error)) return false;
    if (out.rhs.row_id == 0 || (out.rhs.section_type != 260 && out.rhs.section_type != 263 && out.rhs.section_type != 258 && out.rhs.section_type != 262 && out.rhs.section_type != 261 && out.rhs.section_type != 259)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "Add.rhs target is invalid"}; return false; }
    return true;
}

inline std::array<uint8_t, kAddBytes> encodeAdd(const Add &value)
{
    std::array<uint8_t, kAddBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeSemanticRef(data.data() + 8, value.lhs);
    encodeSemanticRef(data.data() + 16, value.rhs);
    return data;
}

inline bool decodeCeilDivByConst(const uint8_t *data, CeilDivByConst &out, AbiError &error)
{
    out = CeilDivByConst{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "CeilDivByConst presence mask has undeclared bits"}; return false; }
    if (!decodeSemanticRef(data + 8, out.value, error)) return false;
    if (out.value.row_id == 0 || (out.value.section_type != 260 && out.value.section_type != 263 && out.value.section_type != 258 && out.value.section_type != 262 && out.value.section_type != 261 && out.value.section_type != 259)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "CeilDivByConst.value target is invalid"}; return false; }
    out.divisor = mesh_abi::rdU64(data + 16);
    return true;
}

inline std::array<uint8_t, kCeilDivByConstBytes> encodeCeilDivByConst(const CeilDivByConst &value)
{
    std::array<uint8_t, kCeilDivByConstBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeSemanticRef(data.data() + 8, value.value);
    mesh_abi::wrU64(data.data() + 16, value.divisor);
    return data;
}

inline bool decodeConst(const uint8_t *data, Const &out, AbiError &error)
{
    out = Const{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "Const presence mask has undeclared bits"}; return false; }
    out.value = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kConstBytes> encodeConst(const Const &value)
{
    std::array<uint8_t, kConstBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.value);
    return data;
}

inline bool decodeFloorDivByConst(const uint8_t *data, FloorDivByConst &out, AbiError &error)
{
    out = FloorDivByConst{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "FloorDivByConst presence mask has undeclared bits"}; return false; }
    if (!decodeSemanticRef(data + 8, out.value, error)) return false;
    if (out.value.row_id == 0 || (out.value.section_type != 260 && out.value.section_type != 263 && out.value.section_type != 258 && out.value.section_type != 262 && out.value.section_type != 261 && out.value.section_type != 259)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "FloorDivByConst.value target is invalid"}; return false; }
    out.divisor = mesh_abi::rdU64(data + 16);
    return true;
}

inline std::array<uint8_t, kFloorDivByConstBytes> encodeFloorDivByConst(const FloorDivByConst &value)
{
    std::array<uint8_t, kFloorDivByConstBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeSemanticRef(data.data() + 8, value.value);
    mesh_abi::wrU64(data.data() + 16, value.divisor);
    return data;
}

inline bool decodeMulByConst(const uint8_t *data, MulByConst &out, AbiError &error)
{
    out = MulByConst{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "MulByConst presence mask has undeclared bits"}; return false; }
    if (!decodeSemanticRef(data + 8, out.value, error)) return false;
    if (out.value.row_id == 0 || (out.value.section_type != 260 && out.value.section_type != 263 && out.value.section_type != 258 && out.value.section_type != 262 && out.value.section_type != 261 && out.value.section_type != 259)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "MulByConst.value target is invalid"}; return false; }
    out.factor = mesh_abi::rdU64(data + 16);
    return true;
}

inline std::array<uint8_t, kMulByConstBytes> encodeMulByConst(const MulByConst &value)
{
    std::array<uint8_t, kMulByConstBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeSemanticRef(data.data() + 8, value.value);
    mesh_abi::wrU64(data.data() + 16, value.factor);
    return data;
}

inline bool decodeSymbol(const uint8_t *data, Symbol &out, AbiError &error)
{
    out = Symbol{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "Symbol presence mask has undeclared bits"}; return false; }
    out.symbol_id = mesh_abi::rdU64(data + 8);
    if (!decodeStringRef(data + 16, out.name, error)) return false;
    out.minimum = mesh_abi::rdU64(data + 24);
    out.maximum = mesh_abi::rdU64(data + 32);
    out.multiple_of = mesh_abi::rdU64(data + 40);
    return true;
}

inline std::array<uint8_t, kSymbolBytes> encodeSymbol(const Symbol &value)
{
    std::array<uint8_t, kSymbolBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.symbol_id);
    encodeStringRef(data.data() + 16, value.name);
    mesh_abi::wrU64(data.data() + 24, value.minimum);
    mesh_abi::wrU64(data.data() + 32, value.maximum);
    mesh_abi::wrU64(data.data() + 40, value.multiple_of);
    return data;
}

#endif
