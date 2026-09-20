#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_CODECS_GRAPH_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_CODECS_GRAPH_HH

inline bool decodeElementwiseAttrs(const uint8_t *data, ElementwiseAttrs &out, AbiError &error)
{
    out = ElementwiseAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(1)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ElementwiseAttrs presence mask has undeclared bits"}; return false; }
    if ((out.presence_mask & (uint64_t(1) << 0)) == 0) {
        if (!zeroBytes(data + 8, 16)) { error = {mesh_diagnostics::E_ABI_RESERVED, "ElementwiseAttrs.scalar absent bytes are nonzero"}; return false; }
    } else {
        out.scalar.kind = mesh_abi::rdU16(data + 8 + kScalarValueKindOffset);
        out.scalar.payload = mesh_abi::rdU64(data + 8 + kScalarValuePayloadOffset);
        if (!zeroBytes(data + 8 + kScalarValueReservedOffset, kScalarValuePayloadOffset - kScalarValueReservedOffset)) { error = {mesh_diagnostics::E_ABI_RESERVED, "ElementwiseAttrs.scalar scalar reserved bytes are nonzero"}; return false; }
        if (!validScalarKind(out.scalar.kind)) { error = {mesh_diagnostics::E_ABI_ENUM, "ElementwiseAttrs.scalar scalar kind is invalid"}; return false; }
        if (out.scalar.kind == kScalarKindBool && out.scalar.payload > 1) { error = {mesh_diagnostics::E_ABI_ENUM, "ElementwiseAttrs.scalar boolean scalar is invalid"}; return false; }
        if (out.scalar.kind == kScalarKindI64 && rdI64(data + 8 + kScalarValuePayloadOffset) >= 0) { error = {mesh_diagnostics::E_ABI_ORDER, "ElementwiseAttrs.scalar signed scalar is noncanonical"}; return false; }
        if (out.scalar.kind == kScalarKindF64 && !std::isfinite(rdF64(data + 8 + kScalarValuePayloadOffset))) { error = {mesh_diagnostics::E_ABI_BOUNDS, "ElementwiseAttrs.scalar scalar is not finite"}; return false; }
    }
    if (!decodeStringRef(data + 24, out.scalar_side, error)) return false;
    out.alpha = rdF64(data + 32);
    if (!std::isfinite(out.alpha)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "ElementwiseAttrs.alpha is not finite"}; return false; }
    if (!decodeStringRef(data + 40, out.approximation, error)) return false;
    return true;
}

inline std::array<uint8_t, kElementwiseAttrsBytes> encodeElementwiseAttrs(const ElementwiseAttrs &value)
{
    std::array<uint8_t, kElementwiseAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    if ((value.presence_mask & (uint64_t(1) << 0)) != 0) {
        mesh_abi::wrU16(data.data() + 8 + kScalarValueKindOffset, value.scalar.kind);
        mesh_abi::wrU64(data.data() + 8 + kScalarValuePayloadOffset, value.scalar.payload);
    }
    encodeStringRef(data.data() + 24, value.scalar_side);
    wrF64(data.data() + 32, value.alpha);
    encodeStringRef(data.data() + 40, value.approximation);
    return data;
}

inline bool decodeEmbeddingAttrs(const uint8_t *data, EmbeddingAttrs &out, AbiError &error)
{
    out = EmbeddingAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(8)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "EmbeddingAttrs presence mask has undeclared bits"}; return false; }
    out.padding_idx = rdI64(data + 8);
    if (mesh_abi::rdU64(data + 16) > 1) { error = {mesh_diagnostics::E_ABI_ENUM, "EmbeddingAttrs.scale_grad_by_freq is not boolean"}; return false; }
    out.scale_grad_by_freq = mesh_abi::rdU64(data + 16) != 0;
    if (mesh_abi::rdU64(data + 24) > 1) { error = {mesh_diagnostics::E_ABI_ENUM, "EmbeddingAttrs.sparse is not boolean"}; return false; }
    out.sparse = mesh_abi::rdU64(data + 24) != 0;
    if ((out.presence_mask & (uint64_t(1) << 3)) == 0) {
        if (!zeroBytes(data + 32, 8)) { error = {mesh_diagnostics::E_ABI_RESERVED, "EmbeddingAttrs.max_norm absent bytes are nonzero"}; return false; }
    } else {
        out.max_norm = rdF64(data + 32);
        if (!std::isfinite(out.max_norm)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "EmbeddingAttrs.max_norm is not finite"}; return false; }
    }
    out.norm_type = rdF64(data + 40);
    if (!std::isfinite(out.norm_type)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "EmbeddingAttrs.norm_type is not finite"}; return false; }
    return true;
}

inline std::array<uint8_t, kEmbeddingAttrsBytes> encodeEmbeddingAttrs(const EmbeddingAttrs &value)
{
    std::array<uint8_t, kEmbeddingAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    wrI64(data.data() + 8, value.padding_idx);
    mesh_abi::wrU64(data.data() + 16, value.scale_grad_by_freq ? 1 : 0);
    mesh_abi::wrU64(data.data() + 24, value.sparse ? 1 : 0);
    if ((value.presence_mask & (uint64_t(1) << 3)) != 0) {
        wrF64(data.data() + 32, value.max_norm);
    }
    wrF64(data.data() + 40, value.norm_type);
    return data;
}

inline bool decodeMatmulAttrs(const uint8_t *data, MatmulAttrs &out, AbiError &error)
{
    out = MatmulAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "MatmulAttrs presence mask has undeclared bits"}; return false; }
    if (!decodeListSpan(data + 8, out.batch_axes, error)) return false;
    out.lhs_contract_axis = rdI64(data + 16);
    out.rhs_contract_axis = rdI64(data + 24);
    if (mesh_abi::rdU64(data + 32) > 1) { error = {mesh_diagnostics::E_ABI_ENUM, "MatmulAttrs.lhs_transpose is not boolean"}; return false; }
    out.lhs_transpose = mesh_abi::rdU64(data + 32) != 0;
    if (mesh_abi::rdU64(data + 40) > 1) { error = {mesh_diagnostics::E_ABI_ENUM, "MatmulAttrs.rhs_transpose is not boolean"}; return false; }
    out.rhs_transpose = mesh_abi::rdU64(data + 40) != 0;
    out.alpha = rdF64(data + 48);
    if (!std::isfinite(out.alpha)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "MatmulAttrs.alpha is not finite"}; return false; }
    out.beta = rdF64(data + 56);
    if (!std::isfinite(out.beta)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "MatmulAttrs.beta is not finite"}; return false; }
    if (mesh_abi::rdU32(data + 64 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "MatmulAttrs.accum_dtype reserved bytes are nonzero"}; return false; }
    if (!validDType(mesh_abi::rdU32(data + 64 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "MatmulAttrs.accum_dtype is invalid"}; return false; }
    out.accum_dtype = static_cast<DType>(mesh_abi::rdU32(data + 64 + kEnumValueValueOffset));
    return true;
}

inline std::array<uint8_t, kMatmulAttrsBytes> encodeMatmulAttrs(const MatmulAttrs &value)
{
    std::array<uint8_t, kMatmulAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeListSpan(data.data() + 8, value.batch_axes);
    wrI64(data.data() + 16, value.lhs_contract_axis);
    wrI64(data.data() + 24, value.rhs_contract_axis);
    mesh_abi::wrU64(data.data() + 32, value.lhs_transpose ? 1 : 0);
    mesh_abi::wrU64(data.data() + 40, value.rhs_transpose ? 1 : 0);
    wrF64(data.data() + 48, value.alpha);
    wrF64(data.data() + 56, value.beta);
    mesh_abi::wrU32(data.data() + 64 + kEnumValueValueOffset, static_cast<uint32_t>(value.accum_dtype));
    return data;
}

inline bool decodeMovementAttrs(const uint8_t *data, MovementAttrs &out, AbiError &error)
{
    out = MovementAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "MovementAttrs presence mask has undeclared bits"}; return false; }
    out.axis = mesh_abi::rdU64(data + 8);
    if (mesh_abi::rdU64(data + 16) > 1) { error = {mesh_diagnostics::E_ABI_ENUM, "MovementAttrs.bounds_check is not boolean"}; return false; }
    out.bounds_check = mesh_abi::rdU64(data + 16) != 0;
    return true;
}

inline std::array<uint8_t, kMovementAttrsBytes> encodeMovementAttrs(const MovementAttrs &value)
{
    std::array<uint8_t, kMovementAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.axis);
    mesh_abi::wrU64(data.data() + 16, value.bounds_check ? 1 : 0);
    return data;
}

inline bool decodeNormAttrs(const uint8_t *data, NormAttrs &out, AbiError &error)
{
    out = NormAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "NormAttrs presence mask has undeclared bits"}; return false; }
    if (!decodeListSpan(data + 8, out.axes, error)) return false;
    out.epsilon = rdF64(data + 16);
    if (!std::isfinite(out.epsilon)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "NormAttrs.epsilon is not finite"}; return false; }
    if (mesh_abi::rdU64(data + 24) > 1) { error = {mesh_diagnostics::E_ABI_ENUM, "NormAttrs.has_weight is not boolean"}; return false; }
    out.has_weight = mesh_abi::rdU64(data + 24) != 0;
    if (mesh_abi::rdU64(data + 32) > 1) { error = {mesh_diagnostics::E_ABI_ENUM, "NormAttrs.has_bias is not boolean"}; return false; }
    out.has_bias = mesh_abi::rdU64(data + 32) != 0;
    return true;
}

inline std::array<uint8_t, kNormAttrsBytes> encodeNormAttrs(const NormAttrs &value)
{
    std::array<uint8_t, kNormAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeListSpan(data.data() + 8, value.axes);
    wrF64(data.data() + 16, value.epsilon);
    mesh_abi::wrU64(data.data() + 24, value.has_weight ? 1 : 0);
    mesh_abi::wrU64(data.data() + 32, value.has_bias ? 1 : 0);
    return data;
}

inline bool decodeReduceAttrs(const uint8_t *data, ReduceAttrs &out, AbiError &error)
{
    out = ReduceAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ReduceAttrs presence mask has undeclared bits"}; return false; }
    if (!decodeListSpan(data + 8, out.axes, error)) return false;
    if (mesh_abi::rdU64(data + 16) > 1) { error = {mesh_diagnostics::E_ABI_ENUM, "ReduceAttrs.keepdim is not boolean"}; return false; }
    out.keepdim = mesh_abi::rdU64(data + 16) != 0;
    if (mesh_abi::rdU32(data + 24 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ReduceAttrs.output_dtype reserved bytes are nonzero"}; return false; }
    if (!validDType(mesh_abi::rdU32(data + 24 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "ReduceAttrs.output_dtype is invalid"}; return false; }
    out.output_dtype = static_cast<DType>(mesh_abi::rdU32(data + 24 + kEnumValueValueOffset));
    if (mesh_abi::rdU32(data + 32 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ReduceAttrs.accum_dtype reserved bytes are nonzero"}; return false; }
    if (!validDType(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "ReduceAttrs.accum_dtype is invalid"}; return false; }
    out.accum_dtype = static_cast<DType>(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset));
    if (!decodeStringRef(data + 40, out.order, error)) return false;
    return true;
}

inline std::array<uint8_t, kReduceAttrsBytes> encodeReduceAttrs(const ReduceAttrs &value)
{
    std::array<uint8_t, kReduceAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeListSpan(data.data() + 8, value.axes);
    mesh_abi::wrU64(data.data() + 16, value.keepdim ? 1 : 0);
    mesh_abi::wrU32(data.data() + 24 + kEnumValueValueOffset, static_cast<uint32_t>(value.output_dtype));
    mesh_abi::wrU32(data.data() + 32 + kEnumValueValueOffset, static_cast<uint32_t>(value.accum_dtype));
    encodeStringRef(data.data() + 40, value.order);
    return data;
}

inline bool decodeSoftmaxAttrs(const uint8_t *data, SoftmaxAttrs &out, AbiError &error)
{
    out = SoftmaxAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "SoftmaxAttrs presence mask has undeclared bits"}; return false; }
    out.axis = mesh_abi::rdU64(data + 8);
    if (mesh_abi::rdU32(data + 16 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "SoftmaxAttrs.output_dtype reserved bytes are nonzero"}; return false; }
    if (!validDType(mesh_abi::rdU32(data + 16 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "SoftmaxAttrs.output_dtype is invalid"}; return false; }
    out.output_dtype = static_cast<DType>(mesh_abi::rdU32(data + 16 + kEnumValueValueOffset));
    if (mesh_abi::rdU64(data + 24) > 1) { error = {mesh_diagnostics::E_ABI_ENUM, "SoftmaxAttrs.zero_fully_masked_rows is not boolean"}; return false; }
    out.zero_fully_masked_rows = mesh_abi::rdU64(data + 24) != 0;
    return true;
}

inline std::array<uint8_t, kSoftmaxAttrsBytes> encodeSoftmaxAttrs(const SoftmaxAttrs &value)
{
    std::array<uint8_t, kSoftmaxAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.axis);
    mesh_abi::wrU32(data.data() + 16 + kEnumValueValueOffset, static_cast<uint32_t>(value.output_dtype));
    mesh_abi::wrU64(data.data() + 24, value.zero_fully_masked_rows ? 1 : 0);
    return data;
}

inline bool decodeViewAttrs(const uint8_t *data, ViewAttrs &out, AbiError &error)
{
    out = ViewAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ViewAttrs presence mask has undeclared bits"}; return false; }
    if (!decodeListSpan(data + 8, out.shape, error)) return false;
    if (!decodeListSpan(data + 16, out.permutation, error)) return false;
    if (!decodeListSpan(data + 24, out.axes, error)) return false;
    if (!decodeListSpan(data + 32, out.starts, error)) return false;
    if (!decodeListSpan(data + 40, out.ends, error)) return false;
    if (!decodeListSpan(data + 48, out.steps, error)) return false;
    if (!decodeListSpan(data + 56, out.expanded_axes, error)) return false;
    return true;
}

inline std::array<uint8_t, kViewAttrsBytes> encodeViewAttrs(const ViewAttrs &value)
{
    std::array<uint8_t, kViewAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeListSpan(data.data() + 8, value.shape);
    encodeListSpan(data.data() + 16, value.permutation);
    encodeListSpan(data.data() + 24, value.axes);
    encodeListSpan(data.data() + 32, value.starts);
    encodeListSpan(data.data() + 40, value.ends);
    encodeListSpan(data.data() + 48, value.steps);
    encodeListSpan(data.data() + 56, value.expanded_axes);
    return data;
}

#endif
