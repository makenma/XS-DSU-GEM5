#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_ENUM_TRAITS_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_ENUM_TRAITS_HH

template <> struct SemanticEnumTraits<WorkUnit>
{
    static constexpr std::string_view name = "WorkUnit";
    static bool fromWire(uint32_t wire, WorkUnit &value)
    {
        if (!validWorkUnit(wire)) return false;
        value = static_cast<WorkUnit>(wire);
        return true;
    }
    static constexpr uint32_t toWire(WorkUnit value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(WorkUnit value)
    {
        switch (value) {
        case WorkUnit::MAC: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4d""\x41""\x43", 3}};
        case WorkUnit::ADD: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x41""\x44""\x44", 3}};
        case WorkUnit::SUB: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x53""\x55""\x42", 3}};
        case WorkUnit::MUL: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4d""\x55""\x4c", 3}};
        case WorkUnit::DIV: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x44""\x49""\x56", 3}};
        case WorkUnit::MAX: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4d""\x41""\x58", 3}};
        case WorkUnit::EXP: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x45""\x58""\x50", 3}};
        case WorkUnit::ERF: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x45""\x52""\x46", 3}};
        case WorkUnit::TANH: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x54""\x41""\x4e""\x48", 4}};
        case WorkUnit::NEGATE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4e""\x45""\x47""\x41""\x54""\x45", 6}};
        case WorkUnit::RSQRT: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x53""\x51""\x52""\x54", 5}};
        case WorkUnit::COPY: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x43""\x4f""\x50""\x59", 4}};
        case WorkUnit::GATHER: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x47""\x41""\x54""\x48""\x45""\x52", 6}};
        case WorkUnit::PREDICATE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x50""\x52""\x45""\x44""\x49""\x43""\x41""\x54""\x45", 9}};
        case WorkUnit::LOGICAL_AND: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4c""\x4f""\x47""\x49""\x43""\x41""\x4c""\x5f""\x41""\x4e""\x44", 11}};
        case WorkUnit::SELECT: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x53""\x45""\x4c""\x45""\x43""\x54", 6}};
        case WorkUnit::CAST: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x43""\x41""\x53""\x54", 4}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<Access>
{
    static constexpr std::string_view name = "Access";
    static bool fromWire(uint32_t wire, Access &value)
    {
        if (!validAccess(wire)) return false;
        value = static_cast<Access>(wire);
        return true;
    }
    static constexpr uint32_t toWire(Access value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(Access value)
    {
        switch (value) {
        case Access::READ_ONLY: return {SemanticEnumPythonKind::Integer, 1ll, {}};
        case Access::READ_WRITE: return {SemanticEnumPythonKind::Integer, 2ll, {}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<DType>
{
    static constexpr std::string_view name = "DType";
    static bool fromWire(uint32_t wire, DType &value)
    {
        if (!validDType(wire)) return false;
        value = static_cast<DType>(wire);
        return true;
    }
    static constexpr uint32_t toWire(DType value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(DType value)
    {
        switch (value) {
        case DType::FP32: return {SemanticEnumPythonKind::Integer, 1ll, {}};
        case DType::FP16: return {SemanticEnumPythonKind::Integer, 2ll, {}};
        case DType::BF16: return {SemanticEnumPythonKind::Integer, 3ll, {}};
        case DType::INT8: return {SemanticEnumPythonKind::Integer, 4ll, {}};
        case DType::INT32: return {SemanticEnumPythonKind::Integer, 5ll, {}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<DmaKind>
{
    static constexpr std::string_view name = "DmaKind";
    static bool fromWire(uint32_t wire, DmaKind &value)
    {
        if (!validDmaKind(wire)) return false;
        value = static_cast<DmaKind>(wire);
        return true;
    }
    static constexpr uint32_t toWire(DmaKind value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(DmaKind value)
    {
        switch (value) {
        case DmaKind::LOAD: return {SemanticEnumPythonKind::Integer, 1ll, {}};
        case DmaKind::STORE: return {SemanticEnumPythonKind::Integer, 2ll, {}};
        case DmaKind::P2P_PUSH: return {SemanticEnumPythonKind::Integer, 3ll, {}};
        case DmaKind::PREFETCH: return {SemanticEnumPythonKind::Integer, 4ll, {}};
        case DmaKind::LOCAL_FILL: return {SemanticEnumPythonKind::Integer, 5ll, {}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<Engine>
{
    static constexpr std::string_view name = "Engine";
    static bool fromWire(uint32_t wire, Engine &value)
    {
        if (!validEngine(wire)) return false;
        value = static_cast<Engine>(wire);
        return true;
    }
    static constexpr uint32_t toWire(Engine value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(Engine value)
    {
        switch (value) {
        case Engine::CONTROL: return {SemanticEnumPythonKind::Integer, 1ll, {}};
        case Engine::DMA_READ: return {SemanticEnumPythonKind::Integer, 2ll, {}};
        case Engine::DMA_WRITE: return {SemanticEnumPythonKind::Integer, 3ll, {}};
        case Engine::TENSOR: return {SemanticEnumPythonKind::Integer, 4ll, {}};
        case Engine::VECTOR: return {SemanticEnumPythonKind::Integer, 5ll, {}};
        case Engine::REDUCE: return {SemanticEnumPythonKind::Integer, 6ll, {}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<Layout>
{
    static constexpr std::string_view name = "Layout";
    static bool fromWire(uint32_t wire, Layout &value)
    {
        if (!validLayout(wire)) return false;
        value = static_cast<Layout>(wire);
        return true;
    }
    static constexpr uint32_t toWire(Layout value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(Layout value)
    {
        switch (value) {
        case Layout::CONTIGUOUS_ROW_MAJOR: return {SemanticEnumPythonKind::Integer, 1ll, {}};
        case Layout::TRANSPOSED_2D_VIEW: return {SemanticEnumPythonKind::Integer, 2ll, {}};
        case Layout::BLOCKED_MNK: return {SemanticEnumPythonKind::Integer, 3ll, {}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<MemorySpace>
{
    static constexpr std::string_view name = "MemorySpace";
    static bool fromWire(uint32_t wire, MemorySpace &value)
    {
        if (!validMemorySpace(wire)) return false;
        value = static_cast<MemorySpace>(wire);
        return true;
    }
    static constexpr uint32_t toWire(MemorySpace value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(MemorySpace value)
    {
        switch (value) {
        case MemorySpace::HBM: return {SemanticEnumPythonKind::Integer, 1ll, {}};
        case MemorySpace::HOST_SHARED: return {SemanticEnumPythonKind::Integer, 2ll, {}};
        case MemorySpace::CORE_SRAM: return {SemanticEnumPythonKind::Integer, 3ll, {}};
        case MemorySpace::PEER_SRAM: return {SemanticEnumPythonKind::Integer, 4ll, {}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<StorageClass>
{
    static constexpr std::string_view name = "StorageClass";
    static bool fromWire(uint32_t wire, StorageClass &value)
    {
        if (!validStorageClass(wire)) return false;
        value = static_cast<StorageClass>(wire);
        return true;
    }
    static constexpr uint32_t toWire(StorageClass value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(StorageClass value)
    {
        switch (value) {
        case StorageClass::EXTERNAL: return {SemanticEnumPythonKind::Integer, 1ll, {}};
        case StorageClass::HBM: return {SemanticEnumPythonKind::Integer, 2ll, {}};
        case StorageClass::HOST_SHARED: return {SemanticEnumPythonKind::Integer, 3ll, {}};
        case StorageClass::CORE_SRAM: return {SemanticEnumPythonKind::Integer, 4ll, {}};
        case StorageClass::PRE_RESIDENT: return {SemanticEnumPythonKind::Integer, 5ll, {}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<TensorRole>
{
    static constexpr std::string_view name = "TensorRole";
    static bool fromWire(uint32_t wire, TensorRole &value)
    {
        if (!validTensorRole(wire)) return false;
        value = static_cast<TensorRole>(wire);
        return true;
    }
    static constexpr uint32_t toWire(TensorRole value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(TensorRole value)
    {
        switch (value) {
        case TensorRole::INPUT: return {SemanticEnumPythonKind::Integer, 1ll, {}};
        case TensorRole::OUTPUT: return {SemanticEnumPythonKind::Integer, 2ll, {}};
        case TensorRole::WEIGHT: return {SemanticEnumPythonKind::Integer, 3ll, {}};
        case TensorRole::CONSTANT: return {SemanticEnumPythonKind::Integer, 4ll, {}};
        case TensorRole::ACTIVATION: return {SemanticEnumPythonKind::Integer, 5ll, {}};
        case TensorRole::KV_CACHE: return {SemanticEnumPythonKind::Integer, 6ll, {}};
        case TensorRole::STATE: return {SemanticEnumPythonKind::Integer, 7ll, {}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<OpCode>
{
    static constexpr std::string_view name = "OpCode";
    static bool fromWire(uint32_t wire, OpCode &value)
    {
        if (!validOpCode(wire)) return false;
        value = static_cast<OpCode>(wire);
        return true;
    }
    static constexpr uint32_t toWire(OpCode value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(OpCode value)
    {
        switch (value) {
        case OpCode::MATMUL: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4d""\x41""\x54""\x4d""\x55""\x4c", 6}};
        case OpCode::BMM: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x42""\x4d""\x4d", 3}};
        case OpCode::LINEAR_BIAS: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4c""\x49""\x4e""\x45""\x41""\x52""\x5f""\x42""\x49""\x41""\x53", 11}};
        case OpCode::RESHAPE_VIEW: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x45""\x53""\x48""\x41""\x50""\x45""\x5f""\x56""\x49""\x45""\x57", 12}};
        case OpCode::TRANSPOSE_VIEW: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x54""\x52""\x41""\x4e""\x53""\x50""\x4f""\x53""\x45""\x5f""\x56""\x49""\x45""\x57", 14}};
        case OpCode::PERMUTE_VIEW: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x50""\x45""\x52""\x4d""\x55""\x54""\x45""\x5f""\x56""\x49""\x45""\x57", 12}};
        case OpCode::SLICE_VIEW: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x53""\x4c""\x49""\x43""\x45""\x5f""\x56""\x49""\x45""\x57", 10}};
        case OpCode::EXPAND_VIEW: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x45""\x58""\x50""\x41""\x4e""\x44""\x5f""\x56""\x49""\x45""\x57", 11}};
        case OpCode::CONTIGUOUS_COPY: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x43""\x4f""\x4e""\x54""\x49""\x47""\x55""\x4f""\x55""\x53""\x5f""\x43""\x4f""\x50""\x59", 15}};
        case OpCode::CONCAT: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x43""\x4f""\x4e""\x43""\x41""\x54", 6}};
        case OpCode::GATHER_ROWS: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x47""\x41""\x54""\x48""\x45""\x52""\x5f""\x52""\x4f""\x57""\x53", 11}};
        case OpCode::ADD: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x41""\x44""\x44", 3}};
        case OpCode::SUB: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x53""\x55""\x42", 3}};
        case OpCode::MUL: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4d""\x55""\x4c", 3}};
        case OpCode::DIV: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x44""\x49""\x56", 3}};
        case OpCode::RELU: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x45""\x4c""\x55", 4}};
        case OpCode::GELU: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x47""\x45""\x4c""\x55", 4}};
        case OpCode::SILU: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x53""\x49""\x4c""\x55", 4}};
        case OpCode::EXP: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x45""\x58""\x50", 3}};
        case OpCode::RSQRT: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x53""\x51""\x52""\x54", 5}};
        case OpCode::REDUCE_SUM: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x45""\x44""\x55""\x43""\x45""\x5f""\x53""\x55""\x4d", 10}};
        case OpCode::REDUCE_MAX: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x45""\x44""\x55""\x43""\x45""\x5f""\x4d""\x41""\x58", 10}};
        case OpCode::REDUCE_MEAN: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x45""\x44""\x55""\x43""\x45""\x5f""\x4d""\x45""\x41""\x4e", 11}};
        case OpCode::LAYERNORM: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4c""\x41""\x59""\x45""\x52""\x4e""\x4f""\x52""\x4d", 9}};
        case OpCode::RMSNORM: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x4d""\x53""\x4e""\x4f""\x52""\x4d", 7}};
        case OpCode::SOFTMAX: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x53""\x4f""\x46""\x54""\x4d""\x41""\x58", 7}};
        case OpCode::EMBEDDING_LOOKUP: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x45""\x4d""\x42""\x45""\x44""\x44""\x49""\x4e""\x47""\x5f""\x4c""\x4f""\x4f""\x4b""\x55""\x50", 16}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<CollectiveAlgorithm>
{
    static constexpr std::string_view name = "CollectiveAlgorithm";
    static bool fromWire(uint32_t wire, CollectiveAlgorithm &value)
    {
        if (!validCollectiveAlgorithm(wire)) return false;
        value = static_cast<CollectiveAlgorithm>(wire);
        return true;
    }
    static constexpr uint32_t toWire(CollectiveAlgorithm value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(CollectiveAlgorithm value)
    {
        switch (value) {
        case CollectiveAlgorithm::AUTO: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x41""\x55""\x54""\x4f", 4}};
        case CollectiveAlgorithm::RING: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x49""\x4e""\x47", 4}};
        case CollectiveAlgorithm::TREE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x54""\x52""\x45""\x45", 4}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<CollectiveKind>
{
    static constexpr std::string_view name = "CollectiveKind";
    static bool fromWire(uint32_t wire, CollectiveKind &value)
    {
        if (!validCollectiveKind(wire)) return false;
        value = static_cast<CollectiveKind>(wire);
        return true;
    }
    static constexpr uint32_t toWire(CollectiveKind value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(CollectiveKind value)
    {
        switch (value) {
        case CollectiveKind::ALL_REDUCE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x41""\x4c""\x4c""\x5f""\x52""\x45""\x44""\x55""\x43""\x45", 10}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<DistributionKind>
{
    static constexpr std::string_view name = "DistributionKind";
    static bool fromWire(uint32_t wire, DistributionKind &value)
    {
        if (!validDistributionKind(wire)) return false;
        value = static_cast<DistributionKind>(wire);
        return true;
    }
    static constexpr uint32_t toWire(DistributionKind value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(DistributionKind value)
    {
        switch (value) {
        case DistributionKind::PARTITIONED: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x50""\x41""\x52""\x54""\x49""\x54""\x49""\x4f""\x4e""\x45""\x44", 11}};
        case DistributionKind::REPLICATED: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x45""\x50""\x4c""\x49""\x43""\x41""\x54""\x45""\x44", 10}};
        case DistributionKind::PARTIAL_SUM: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x50""\x41""\x52""\x54""\x49""\x41""\x4c""\x5f""\x53""\x55""\x4d", 11}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<KernelOpcode>
{
    static constexpr std::string_view name = "KernelOpcode";
    static bool fromWire(uint32_t wire, KernelOpcode &value)
    {
        if (!validKernelOpcode(wire)) return false;
        value = static_cast<KernelOpcode>(wire);
        return true;
    }
    static constexpr uint32_t toWire(KernelOpcode value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(KernelOpcode value)
    {
        switch (value) {
        case KernelOpcode::ALLOC: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x41""\x4c""\x4c""\x4f""\x43", 5}};
        case KernelOpcode::VIEW: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x56""\x49""\x45""\x57", 4}};
        case KernelOpcode::DMA: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x44""\x4d""\x41", 3}};
        case KernelOpcode::GEMM: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x47""\x45""\x4d""\x4d", 4}};
        case KernelOpcode::BMM: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x42""\x4d""\x4d", 3}};
        case KernelOpcode::MATRIX_EPILOGUE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4d""\x41""\x54""\x52""\x49""\x58""\x5f""\x45""\x50""\x49""\x4c""\x4f""\x47""\x55""\x45", 15}};
        case KernelOpcode::VECTOR: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x56""\x45""\x43""\x54""\x4f""\x52", 6}};
        case KernelOpcode::DATA_MOVEMENT: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x44""\x41""\x54""\x41""\x5f""\x4d""\x4f""\x56""\x45""\x4d""\x45""\x4e""\x54", 13}};
        case KernelOpcode::REDUCE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x45""\x44""\x55""\x43""\x45", 6}};
        case KernelOpcode::SOFTMAX: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x53""\x4f""\x46""\x54""\x4d""\x41""\x58", 7}};
        case KernelOpcode::NORM: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4e""\x4f""\x52""\x4d", 4}};
        case KernelOpcode::COLLECTIVE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x43""\x4f""\x4c""\x4c""\x45""\x43""\x54""\x49""\x56""\x45", 10}};
        case KernelOpcode::LOCAL_REDUCE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4c""\x4f""\x43""\x41""\x4c""\x5f""\x52""\x45""\x44""\x55""\x43""\x45", 12}};
        case KernelOpcode::LOCAL_COPY: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4c""\x4f""\x43""\x41""\x4c""\x5f""\x43""\x4f""\x50""\x59", 10}};
        case KernelOpcode::RECV_WAIT: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x45""\x43""\x56""\x5f""\x57""\x41""\x49""\x54", 9}};
        case KernelOpcode::BARRIER: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x42""\x41""\x52""\x52""\x49""\x45""\x52", 7}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<MatrixEpilogueAlgorithm>
{
    static constexpr std::string_view name = "MatrixEpilogueAlgorithm";
    static bool fromWire(uint32_t wire, MatrixEpilogueAlgorithm &value)
    {
        if (!validMatrixEpilogueAlgorithm(wire)) return false;
        value = static_cast<MatrixEpilogueAlgorithm>(wire);
        return true;
    }
    static constexpr uint32_t toWire(MatrixEpilogueAlgorithm value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(MatrixEpilogueAlgorithm value)
    {
        switch (value) {
        case MatrixEpilogueAlgorithm::VECTOR_ACCUMULATION: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x56""\x45""\x43""\x54""\x4f""\x52""\x5f""\x41""\x43""\x43""\x55""\x4d""\x55""\x4c""\x41""\x54""\x49""\x4f""\x4e", 19}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<MatrixPhase>
{
    static constexpr std::string_view name = "MatrixPhase";
    static bool fromWire(uint32_t wire, MatrixPhase &value)
    {
        if (!validMatrixPhase(wire)) return false;
        value = static_cast<MatrixPhase>(wire);
        return true;
    }
    static constexpr uint32_t toWire(MatrixPhase value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(MatrixPhase value)
    {
        switch (value) {
        case MatrixPhase::DIRECT: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x44""\x49""\x52""\x45""\x43""\x54", 6}};
        case MatrixPhase::ACCUMULATE_ONLY: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x41""\x43""\x43""\x55""\x4d""\x55""\x4c""\x41""\x54""\x45""\x5f""\x4f""\x4e""\x4c""\x59", 15}};
        case MatrixPhase::ACCUMULATE_FIRST: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x41""\x43""\x43""\x55""\x4d""\x55""\x4c""\x41""\x54""\x45""\x5f""\x46""\x49""\x52""\x53""\x54", 16}};
        case MatrixPhase::ACCUMULATE_CONTINUE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x41""\x43""\x43""\x55""\x4d""\x55""\x4c""\x41""\x54""\x45""\x5f""\x43""\x4f""\x4e""\x54""\x49""\x4e""\x55""\x45", 19}};
        case MatrixPhase::ACCUMULATE_FINAL: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x41""\x43""\x43""\x55""\x4d""\x55""\x4c""\x41""\x54""\x45""\x5f""\x46""\x49""\x4e""\x41""\x4c", 16}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<MovementAlgorithm>
{
    static constexpr std::string_view name = "MovementAlgorithm";
    static bool fromWire(uint32_t wire, MovementAlgorithm &value)
    {
        if (!validMovementAlgorithm(wire)) return false;
        value = static_cast<MovementAlgorithm>(wire);
        return true;
    }
    static constexpr uint32_t toWire(MovementAlgorithm value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(MovementAlgorithm value)
    {
        switch (value) {
        case MovementAlgorithm::STRIDED_COPY: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x53""\x54""\x52""\x49""\x44""\x45""\x44""\x5f""\x43""\x4f""\x50""\x59", 12}};
        case MovementAlgorithm::CONCAT: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x43""\x4f""\x4e""\x43""\x41""\x54", 6}};
        case MovementAlgorithm::GATHER_ROWS: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x47""\x41""\x54""\x48""\x45""\x52""\x5f""\x52""\x4f""\x57""\x53", 11}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<NormAlgorithm>
{
    static constexpr std::string_view name = "NormAlgorithm";
    static bool fromWire(uint32_t wire, NormAlgorithm &value)
    {
        if (!validNormAlgorithm(wire)) return false;
        value = static_cast<NormAlgorithm>(wire);
        return true;
    }
    static constexpr uint32_t toWire(NormAlgorithm value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(NormAlgorithm value)
    {
        switch (value) {
        case NormAlgorithm::LAYER_NORM: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4c""\x41""\x59""\x45""\x52""\x5f""\x4e""\x4f""\x52""\x4d", 10}};
        case NormAlgorithm::RMS_NORM: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x4d""\x53""\x5f""\x4e""\x4f""\x52""\x4d", 8}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<OperandAccessMode>
{
    static constexpr std::string_view name = "OperandAccessMode";
    static bool fromWire(uint32_t wire, OperandAccessMode &value)
    {
        if (!validOperandAccessMode(wire)) return false;
        value = static_cast<OperandAccessMode>(wire);
        return true;
    }
    static constexpr uint32_t toWire(OperandAccessMode value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(OperandAccessMode value)
    {
        switch (value) {
        case OperandAccessMode::READ: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x45""\x41""\x44", 4}};
        case OperandAccessMode::WRITE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x57""\x52""\x49""\x54""\x45", 5}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<ReduceKind>
{
    static constexpr std::string_view name = "ReduceKind";
    static bool fromWire(uint32_t wire, ReduceKind &value)
    {
        if (!validReduceKind(wire)) return false;
        value = static_cast<ReduceKind>(wire);
        return true;
    }
    static constexpr uint32_t toWire(ReduceKind value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(ReduceKind value)
    {
        switch (value) {
        case ReduceKind::SUM: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x53""\x55""\x4d", 3}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<ReductionAlgorithm>
{
    static constexpr std::string_view name = "ReductionAlgorithm";
    static bool fromWire(uint32_t wire, ReductionAlgorithm &value)
    {
        if (!validReductionAlgorithm(wire)) return false;
        value = static_cast<ReductionAlgorithm>(wire);
        return true;
    }
    static constexpr uint32_t toWire(ReductionAlgorithm value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(ReductionAlgorithm value)
    {
        switch (value) {
        case ReductionAlgorithm::LEFT_TO_RIGHT: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4c""\x45""\x46""\x54""\x5f""\x54""\x4f""\x5f""\x52""\x49""\x47""\x48""\x54", 13}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<SoftmaxAlgorithm>
{
    static constexpr std::string_view name = "SoftmaxAlgorithm";
    static bool fromWire(uint32_t wire, SoftmaxAlgorithm &value)
    {
        if (!validSoftmaxAlgorithm(wire)) return false;
        value = static_cast<SoftmaxAlgorithm>(wire);
        return true;
    }
    static constexpr uint32_t toWire(SoftmaxAlgorithm value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(SoftmaxAlgorithm value)
    {
        switch (value) {
        case SoftmaxAlgorithm::STABLE_MAX_SUM: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x53""\x54""\x41""\x42""\x4c""\x45""\x5f""\x4d""\x41""\x58""\x5f""\x53""\x55""\x4d", 14}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<StateOrigin>
{
    static constexpr std::string_view name = "StateOrigin";
    static bool fromWire(uint32_t wire, StateOrigin &value)
    {
        if (!validStateOrigin(wire)) return false;
        value = static_cast<StateOrigin>(wire);
        return true;
    }
    static constexpr uint32_t toWire(StateOrigin value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(StateOrigin value)
    {
        switch (value) {
        case StateOrigin::EMPTY: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x45""\x4d""\x50""\x54""\x59", 5}};
        case StateOrigin::EXTERNAL: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x45""\x58""\x54""\x45""\x52""\x4e""\x41""\x4c", 8}};
        case StateOrigin::PRE_RESIDENT: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x50""\x52""\x45""\x5f""\x52""\x45""\x53""\x49""\x44""\x45""\x4e""\x54", 12}};
        case StateOrigin::PRODUCED: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x50""\x52""\x4f""\x44""\x55""\x43""\x45""\x44", 8}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<SynthesizedTensorPurpose>
{
    static constexpr std::string_view name = "SynthesizedTensorPurpose";
    static bool fromWire(uint32_t wire, SynthesizedTensorPurpose &value)
    {
        if (!validSynthesizedTensorPurpose(wire)) return false;
        value = static_cast<SynthesizedTensorPurpose>(wire);
        return true;
    }
    static constexpr uint32_t toWire(SynthesizedTensorPurpose value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(SynthesizedTensorPurpose value)
    {
        switch (value) {
        case SynthesizedTensorPurpose::PADDING: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x50""\x41""\x44""\x44""\x49""\x4e""\x47", 7}};
        case SynthesizedTensorPurpose::COPY: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x43""\x4f""\x50""\x59", 4}};
        case SynthesizedTensorPurpose::ACCUMULATION: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x41""\x43""\x43""\x55""\x4d""\x55""\x4c""\x41""\x54""\x49""\x4f""\x4e", 12}};
        case SynthesizedTensorPurpose::PARTIAL_SUM: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x50""\x41""\x52""\x54""\x49""\x41""\x4c""\x5f""\x53""\x55""\x4d", 11}};
        case SynthesizedTensorPurpose::REDUCTION: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x45""\x44""\x55""\x43""\x54""\x49""\x4f""\x4e", 9}};
        case SynthesizedTensorPurpose::DATA_MOVEMENT: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x44""\x41""\x54""\x41""\x5f""\x4d""\x4f""\x56""\x45""\x4d""\x45""\x4e""\x54", 13}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<VectorAlgorithm>
{
    static constexpr std::string_view name = "VectorAlgorithm";
    static bool fromWire(uint32_t wire, VectorAlgorithm &value)
    {
        if (!validVectorAlgorithm(wire)) return false;
        value = static_cast<VectorAlgorithm>(wire);
        return true;
    }
    static constexpr uint32_t toWire(VectorAlgorithm value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(VectorAlgorithm value)
    {
        switch (value) {
        case VectorAlgorithm::ELEMENTWISE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x45""\x4c""\x45""\x4d""\x45""\x4e""\x54""\x57""\x49""\x53""\x45", 11}};
        case VectorAlgorithm::EMBEDDING_GATHER: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x45""\x4d""\x42""\x45""\x44""\x44""\x49""\x4e""\x47""\x5f""\x47""\x41""\x54""\x48""\x45""\x52", 16}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<EndpointSide>
{
    static constexpr std::string_view name = "EndpointSide";
    static bool fromWire(uint32_t wire, EndpointSide &value)
    {
        if (!validEndpointSide(wire)) return false;
        value = static_cast<EndpointSide>(wire);
        return true;
    }
    static constexpr uint32_t toWire(EndpointSide value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(EndpointSide value)
    {
        switch (value) {
        case EndpointSide::SRC: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x53""\x52""\x43", 3}};
        case EndpointSide::DST: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x44""\x53""\x54", 3}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<FenceScope>
{
    static constexpr std::string_view name = "FenceScope";
    static bool fromWire(uint32_t wire, FenceScope &value)
    {
        if (!validFenceScope(wire)) return false;
        value = static_cast<FenceScope>(wire);
        return true;
    }
    static constexpr uint32_t toWire(FenceScope value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(FenceScope value)
    {
        switch (value) {
        case FenceScope::DMA_READ: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x44""\x4d""\x41""\x5f""\x52""\x45""\x41""\x44", 8}};
        case FenceScope::DMA_WRITE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x44""\x4d""\x41""\x5f""\x57""\x52""\x49""\x54""\x45", 9}};
        case FenceScope::P2P: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x50""\x32""\x50", 3}};
        case FenceScope::HOST_SHARED_WRITE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x48""\x4f""\x53""\x54""\x5f""\x53""\x48""\x41""\x52""\x45""\x44""\x5f""\x57""\x52""\x49""\x54""\x45", 17}};
        case FenceScope::ALL_INSTANCE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x41""\x4c""\x4c""\x5f""\x49""\x4e""\x53""\x54""\x41""\x4e""\x43""\x45", 12}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<ScheduledDependencyKind>
{
    static constexpr std::string_view name = "ScheduledDependencyKind";
    static bool fromWire(uint32_t wire, ScheduledDependencyKind &value)
    {
        if (!validScheduledDependencyKind(wire)) return false;
        value = static_cast<ScheduledDependencyKind>(wire);
        return true;
    }
    static constexpr uint32_t toWire(ScheduledDependencyKind value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(ScheduledDependencyKind value)
    {
        switch (value) {
        case ScheduledDependencyKind::KERNEL_CONTROL: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4b""\x45""\x52""\x4e""\x45""\x4c""\x5f""\x43""\x4f""\x4e""\x54""\x52""\x4f""\x4c", 14}};
        case ScheduledDependencyKind::KERNEL_STATE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4b""\x45""\x52""\x4e""\x45""\x4c""\x5f""\x53""\x54""\x41""\x54""\x45", 12}};
        case ScheduledDependencyKind::RAW: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x41""\x57", 3}};
        case ScheduledDependencyKind::WAR: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x57""\x41""\x52", 3}};
        case ScheduledDependencyKind::WAW: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x57""\x41""\x57", 3}};
        case ScheduledDependencyKind::DMA_PIN: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x44""\x4d""\x41""\x5f""\x50""\x49""\x4e", 7}};
        case ScheduledDependencyKind::DMA_COMPLETION: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x44""\x4d""\x41""\x5f""\x43""\x4f""\x4d""\x50""\x4c""\x45""\x54""\x49""\x4f""\x4e", 14}};
        case ScheduledDependencyKind::SRAM_REUSE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x53""\x52""\x41""\x4d""\x5f""\x52""\x45""\x55""\x53""\x45", 10}};
        case ScheduledDependencyKind::STREAM_ORDER: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x53""\x54""\x52""\x45""\x41""\x4d""\x5f""\x4f""\x52""\x44""\x45""\x52", 12}};
        case ScheduledDependencyKind::LIFECYCLE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4c""\x49""\x46""\x45""\x43""\x59""\x43""\x4c""\x45", 9}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<AxiChannel>
{
    static constexpr std::string_view name = "AxiChannel";
    static bool fromWire(uint32_t wire, AxiChannel &value)
    {
        if (!validAxiChannel(wire)) return false;
        value = static_cast<AxiChannel>(wire);
        return true;
    }
    static constexpr uint32_t toWire(AxiChannel value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(AxiChannel value)
    {
        switch (value) {
        case AxiChannel::AW: return {SemanticEnumPythonKind::Integer, 0ll, {}};
        case AxiChannel::W: return {SemanticEnumPythonKind::Integer, 1ll, {}};
        case AxiChannel::B: return {SemanticEnumPythonKind::Integer, 2ll, {}};
        case AxiChannel::AR: return {SemanticEnumPythonKind::Integer, 3ll, {}};
        case AxiChannel::R: return {SemanticEnumPythonKind::Integer, 4ll, {}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<TrafficAggregateLevel>
{
    static constexpr std::string_view name = "TrafficAggregateLevel";
    static bool fromWire(uint32_t wire, TrafficAggregateLevel &value)
    {
        if (!validTrafficAggregateLevel(wire)) return false;
        value = static_cast<TrafficAggregateLevel>(wire);
        return true;
    }
    static constexpr uint32_t toWire(TrafficAggregateLevel value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(TrafficAggregateLevel value)
    {
        switch (value) {
        case TrafficAggregateLevel::PROGRAM: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x50""\x52""\x4f""\x47""\x52""\x41""\x4d", 7}};
        case TrafficAggregateLevel::ENTRYPOINT: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x45""\x4e""\x54""\x52""\x59""\x50""\x4f""\x49""\x4e""\x54", 10}};
        case TrafficAggregateLevel::PROFILE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x50""\x52""\x4f""\x46""\x49""\x4c""\x45", 7}};
        case TrafficAggregateLevel::DETAIL: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x44""\x45""\x54""\x41""\x49""\x4c", 6}};
        default: return {};
        }
    }
};

template <> struct SemanticEnumTraits<TrafficDirection>
{
    static constexpr std::string_view name = "TrafficDirection";
    static bool fromWire(uint32_t wire, TrafficDirection &value)
    {
        if (!validTrafficDirection(wire)) return false;
        value = static_cast<TrafficDirection>(wire);
        return true;
    }
    static constexpr uint32_t toWire(TrafficDirection value) { return static_cast<uint32_t>(value); }
    static constexpr SemanticEnumPythonValue pythonValue(TrafficDirection value)
    {
        switch (value) {
        case TrafficDirection::READ: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x52""\x45""\x41""\x44", 4}};
        case TrafficDirection::WRITE: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x57""\x52""\x49""\x54""\x45", 5}};
        case TrafficDirection::P2P: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x50""\x32""\x50", 3}};
        case TrafficDirection::LOCAL: return {SemanticEnumPythonKind::String, 0, std::string_view{"\x4c""\x4f""\x43""\x41""\x4c", 5}};
        default: return {};
        }
    }
};

#endif
