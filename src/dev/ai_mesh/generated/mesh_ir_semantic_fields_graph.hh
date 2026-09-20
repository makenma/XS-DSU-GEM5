#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_FIELDS_GRAPH_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_FIELDS_GRAPH_HH

inline constexpr SemanticFieldDescriptor kElementwiseAttrsScalarField = {"scalar", SemanticFieldKind::Scalar, 1ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kElementwiseAttrsScalarSideField = {"scalar_side", SemanticFieldKind::String, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kElementwiseAttrsAlphaField = {"alpha", SemanticFieldKind::F64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kElementwiseAttrsApproximationField = {"approximation", SemanticFieldKind::String, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ElementwiseAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kElementwiseAttrsScalarField, (value.presence_mask & 1ull) != 0, value.scalar)) return false;
    if (!visitor(kElementwiseAttrsScalarSideField, true, value.scalar_side)) return false;
    if (!visitor(kElementwiseAttrsAlphaField, true, value.alpha)) return false;
    if (!visitor(kElementwiseAttrsApproximationField, true, value.approximation)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ElementwiseAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kElementwiseAttrsAlphaField, true, value.alpha)) return false;
    if (!visitor(kElementwiseAttrsApproximationField, true, value.approximation)) return false;
    if (!visitor(kElementwiseAttrsScalarField, (value.presence_mask & 1ull) != 0, value.scalar)) return false;
    if (!visitor(kElementwiseAttrsScalarSideField, true, value.scalar_side)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kEmbeddingAttrsPaddingIdxField = {"padding_idx", SemanticFieldKind::I64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kEmbeddingAttrsScaleGradByFreqField = {"scale_grad_by_freq", SemanticFieldKind::Bool, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kEmbeddingAttrsSparseField = {"sparse", SemanticFieldKind::Bool, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kEmbeddingAttrsMaxNormField = {"max_norm", SemanticFieldKind::F64, 8ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kEmbeddingAttrsNormTypeField = {"norm_type", SemanticFieldKind::F64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const EmbeddingAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kEmbeddingAttrsPaddingIdxField, true, value.padding_idx)) return false;
    if (!visitor(kEmbeddingAttrsScaleGradByFreqField, true, value.scale_grad_by_freq)) return false;
    if (!visitor(kEmbeddingAttrsSparseField, true, value.sparse)) return false;
    if (!visitor(kEmbeddingAttrsMaxNormField, (value.presence_mask & 8ull) != 0, value.max_norm)) return false;
    if (!visitor(kEmbeddingAttrsNormTypeField, true, value.norm_type)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const EmbeddingAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kEmbeddingAttrsMaxNormField, (value.presence_mask & 8ull) != 0, value.max_norm)) return false;
    if (!visitor(kEmbeddingAttrsNormTypeField, true, value.norm_type)) return false;
    if (!visitor(kEmbeddingAttrsPaddingIdxField, true, value.padding_idx)) return false;
    if (!visitor(kEmbeddingAttrsScaleGradByFreqField, true, value.scale_grad_by_freq)) return false;
    if (!visitor(kEmbeddingAttrsSparseField, true, value.sparse)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kMatmulAttrsBatchAxesField = {"batch_axes", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kMatmulAttrsLhsContractAxisField = {"lhs_contract_axis", SemanticFieldKind::I64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kMatmulAttrsRhsContractAxisField = {"rhs_contract_axis", SemanticFieldKind::I64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kMatmulAttrsLhsTransposeField = {"lhs_transpose", SemanticFieldKind::Bool, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kMatmulAttrsRhsTransposeField = {"rhs_transpose", SemanticFieldKind::Bool, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kMatmulAttrsAlphaField = {"alpha", SemanticFieldKind::F64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kMatmulAttrsBetaField = {"beta", SemanticFieldKind::F64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kMatmulAttrsAccumDtypeField = {"accum_dtype", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const MatmulAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kMatmulAttrsBatchAxesField, true, value.batch_axes)) return false;
    if (!visitor(kMatmulAttrsLhsContractAxisField, true, value.lhs_contract_axis)) return false;
    if (!visitor(kMatmulAttrsRhsContractAxisField, true, value.rhs_contract_axis)) return false;
    if (!visitor(kMatmulAttrsLhsTransposeField, true, value.lhs_transpose)) return false;
    if (!visitor(kMatmulAttrsRhsTransposeField, true, value.rhs_transpose)) return false;
    if (!visitor(kMatmulAttrsAlphaField, true, value.alpha)) return false;
    if (!visitor(kMatmulAttrsBetaField, true, value.beta)) return false;
    if (!visitor(kMatmulAttrsAccumDtypeField, true, value.accum_dtype)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const MatmulAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kMatmulAttrsAccumDtypeField, true, value.accum_dtype)) return false;
    if (!visitor(kMatmulAttrsAlphaField, true, value.alpha)) return false;
    if (!visitor(kMatmulAttrsBatchAxesField, true, value.batch_axes)) return false;
    if (!visitor(kMatmulAttrsBetaField, true, value.beta)) return false;
    if (!visitor(kMatmulAttrsLhsContractAxisField, true, value.lhs_contract_axis)) return false;
    if (!visitor(kMatmulAttrsLhsTransposeField, true, value.lhs_transpose)) return false;
    if (!visitor(kMatmulAttrsRhsContractAxisField, true, value.rhs_contract_axis)) return false;
    if (!visitor(kMatmulAttrsRhsTransposeField, true, value.rhs_transpose)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kMovementAttrsAxisField = {"axis", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kMovementAttrsBoundsCheckField = {"bounds_check", SemanticFieldKind::Bool, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const MovementAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kMovementAttrsAxisField, true, value.axis)) return false;
    if (!visitor(kMovementAttrsBoundsCheckField, true, value.bounds_check)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const MovementAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kMovementAttrsAxisField, true, value.axis)) return false;
    if (!visitor(kMovementAttrsBoundsCheckField, true, value.bounds_check)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kNormAttrsAxesField = {"axes", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kNormAttrsEpsilonField = {"epsilon", SemanticFieldKind::F64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kNormAttrsHasWeightField = {"has_weight", SemanticFieldKind::Bool, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kNormAttrsHasBiasField = {"has_bias", SemanticFieldKind::Bool, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const NormAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kNormAttrsAxesField, true, value.axes)) return false;
    if (!visitor(kNormAttrsEpsilonField, true, value.epsilon)) return false;
    if (!visitor(kNormAttrsHasWeightField, true, value.has_weight)) return false;
    if (!visitor(kNormAttrsHasBiasField, true, value.has_bias)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const NormAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kNormAttrsAxesField, true, value.axes)) return false;
    if (!visitor(kNormAttrsEpsilonField, true, value.epsilon)) return false;
    if (!visitor(kNormAttrsHasBiasField, true, value.has_bias)) return false;
    if (!visitor(kNormAttrsHasWeightField, true, value.has_weight)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kReduceAttrsAxesField = {"axes", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kReduceAttrsKeepdimField = {"keepdim", SemanticFieldKind::Bool, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kReduceAttrsOutputDtypeField = {"output_dtype", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kReduceAttrsAccumDtypeField = {"accum_dtype", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kReduceAttrsOrderField = {"order", SemanticFieldKind::String, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ReduceAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kReduceAttrsAxesField, true, value.axes)) return false;
    if (!visitor(kReduceAttrsKeepdimField, true, value.keepdim)) return false;
    if (!visitor(kReduceAttrsOutputDtypeField, true, value.output_dtype)) return false;
    if (!visitor(kReduceAttrsAccumDtypeField, true, value.accum_dtype)) return false;
    if (!visitor(kReduceAttrsOrderField, true, value.order)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ReduceAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kReduceAttrsAccumDtypeField, true, value.accum_dtype)) return false;
    if (!visitor(kReduceAttrsAxesField, true, value.axes)) return false;
    if (!visitor(kReduceAttrsKeepdimField, true, value.keepdim)) return false;
    if (!visitor(kReduceAttrsOrderField, true, value.order)) return false;
    if (!visitor(kReduceAttrsOutputDtypeField, true, value.output_dtype)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kSoftmaxAttrsAxisField = {"axis", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kSoftmaxAttrsOutputDtypeField = {"output_dtype", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kSoftmaxAttrsZeroFullyMaskedRowsField = {"zero_fully_masked_rows", SemanticFieldKind::Bool, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const SoftmaxAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kSoftmaxAttrsAxisField, true, value.axis)) return false;
    if (!visitor(kSoftmaxAttrsOutputDtypeField, true, value.output_dtype)) return false;
    if (!visitor(kSoftmaxAttrsZeroFullyMaskedRowsField, true, value.zero_fully_masked_rows)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const SoftmaxAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kSoftmaxAttrsAxisField, true, value.axis)) return false;
    if (!visitor(kSoftmaxAttrsOutputDtypeField, true, value.output_dtype)) return false;
    if (!visitor(kSoftmaxAttrsZeroFullyMaskedRowsField, true, value.zero_fully_masked_rows)) return false;
    return true;
}

inline constexpr std::array<uint16_t, 6> kViewAttrsShapeTargets = {260, 263, 258, 262, 261, 259};
inline constexpr SemanticFieldDescriptor kViewAttrsShapeField = {"shape", SemanticFieldKind::RefList, 0ull, kViewAttrsShapeTargets.data(), 6, true};
inline constexpr SemanticFieldDescriptor kViewAttrsPermutationField = {"permutation", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kViewAttrsAxesField = {"axes", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kViewAttrsStartsField = {"starts", SemanticFieldKind::IntegerList, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kViewAttrsEndsField = {"ends", SemanticFieldKind::IntegerList, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kViewAttrsStepsField = {"steps", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kViewAttrsExpandedAxesField = {"expanded_axes", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ViewAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kViewAttrsShapeField, true, value.shape)) return false;
    if (!visitor(kViewAttrsPermutationField, true, value.permutation)) return false;
    if (!visitor(kViewAttrsAxesField, true, value.axes)) return false;
    if (!visitor(kViewAttrsStartsField, true, value.starts)) return false;
    if (!visitor(kViewAttrsEndsField, true, value.ends)) return false;
    if (!visitor(kViewAttrsStepsField, true, value.steps)) return false;
    if (!visitor(kViewAttrsExpandedAxesField, true, value.expanded_axes)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ViewAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kViewAttrsAxesField, true, value.axes)) return false;
    if (!visitor(kViewAttrsEndsField, true, value.ends)) return false;
    if (!visitor(kViewAttrsExpandedAxesField, true, value.expanded_axes)) return false;
    if (!visitor(kViewAttrsPermutationField, true, value.permutation)) return false;
    if (!visitor(kViewAttrsShapeField, true, value.shape)) return false;
    if (!visitor(kViewAttrsStartsField, true, value.starts)) return false;
    if (!visitor(kViewAttrsStepsField, true, value.steps)) return false;
    return true;
}

#endif
