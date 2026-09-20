from enum import Enum, IntEnum, StrEnum

class WorkUnit(str, Enum):
    MAC = 'MAC'
    ADD = 'ADD'
    SUB = 'SUB'
    MUL = 'MUL'
    DIV = 'DIV'
    MAX = 'MAX'
    EXP = 'EXP'
    ERF = 'ERF'
    TANH = 'TANH'
    NEGATE = 'NEGATE'
    RSQRT = 'RSQRT'
    COPY = 'COPY'
    GATHER = 'GATHER'
    PREDICATE = 'PREDICATE'
    LOGICAL_AND = 'LOGICAL_AND'
    SELECT = 'SELECT'
    CAST = 'CAST'

WorkUnit.__module__ = 'mesh_ir.analysis.cost'

class Access(IntEnum):
    READ_ONLY = 1
    READ_WRITE = 2

Access.__module__ = 'mesh_ir.ir.common'

class DType(IntEnum):
    FP32 = 1
    FP16 = 2
    BF16 = 3
    INT8 = 4
    INT32 = 5

    @property
    def byte_width(self):
        return {
            DType.FP32: 4,
            DType.FP16: 2,
            DType.BF16: 2,
            DType.INT8: 1,
            DType.INT32: 4,
        }[self]

    @property
    def accumulation(self):
        return {
            DType.FP32: DType.FP32,
            DType.FP16: DType.FP32,
            DType.BF16: DType.FP32,
            DType.INT8: DType.INT32,
            DType.INT32: DType.INT32,
        }[self]

    @property
    def machine_epsilon(self):
        return {
            DType.FP32: 1.1920928955078125e-07,
            DType.FP16: 0.0009765625,
            DType.BF16: 0.0078125,
        }[self]

DType.__module__ = 'mesh_ir.ir.common'

class DmaKind(IntEnum):
    LOAD = 1
    STORE = 2
    P2P_PUSH = 3
    PREFETCH = 4
    LOCAL_FILL = 5

DmaKind.__module__ = 'mesh_ir.ir.common'

class Engine(IntEnum):
    CONTROL = 1
    DMA_READ = 2
    DMA_WRITE = 3
    TENSOR = 4
    VECTOR = 5
    REDUCE = 6

Engine.__module__ = 'mesh_ir.ir.common'

class Layout(IntEnum):
    CONTIGUOUS_ROW_MAJOR = 1
    TRANSPOSED_2D_VIEW = 2
    BLOCKED_MNK = 3

Layout.__module__ = 'mesh_ir.ir.common'

class MemorySpace(IntEnum):
    HBM = 1
    HOST_SHARED = 2
    CORE_SRAM = 3
    PEER_SRAM = 4

MemorySpace.__module__ = 'mesh_ir.ir.common'

class StorageClass(IntEnum):
    EXTERNAL = 1
    HBM = 2
    HOST_SHARED = 3
    CORE_SRAM = 4
    PRE_RESIDENT = 5

StorageClass.__module__ = 'mesh_ir.ir.common'

class TensorRole(IntEnum):
    INPUT = 1
    OUTPUT = 2
    WEIGHT = 3
    CONSTANT = 4
    ACTIVATION = 5
    KV_CACHE = 6
    STATE = 7

TensorRole.__module__ = 'mesh_ir.ir.common'

class OpCode(str, Enum):
    MATMUL = 'MATMUL'
    BMM = 'BMM'
    LINEAR_BIAS = 'LINEAR_BIAS'
    RESHAPE_VIEW = 'RESHAPE_VIEW'
    TRANSPOSE_VIEW = 'TRANSPOSE_VIEW'
    PERMUTE_VIEW = 'PERMUTE_VIEW'
    SLICE_VIEW = 'SLICE_VIEW'
    EXPAND_VIEW = 'EXPAND_VIEW'
    CONTIGUOUS_COPY = 'CONTIGUOUS_COPY'
    CONCAT = 'CONCAT'
    GATHER_ROWS = 'GATHER_ROWS'
    ADD = 'ADD'
    SUB = 'SUB'
    MUL = 'MUL'
    DIV = 'DIV'
    RELU = 'RELU'
    GELU = 'GELU'
    SILU = 'SILU'
    EXP = 'EXP'
    RSQRT = 'RSQRT'
    REDUCE_SUM = 'REDUCE_SUM'
    REDUCE_MAX = 'REDUCE_MAX'
    REDUCE_MEAN = 'REDUCE_MEAN'
    LAYERNORM = 'LAYERNORM'
    RMSNORM = 'RMSNORM'
    SOFTMAX = 'SOFTMAX'
    EMBEDDING_LOOKUP = 'EMBEDDING_LOOKUP'

OpCode.__module__ = 'mesh_ir.ir.graph_ir'

class CollectiveAlgorithm(str, Enum):
    AUTO = 'AUTO'
    RING = 'RING'
    TREE = 'TREE'

CollectiveAlgorithm.__module__ = 'mesh_ir.ir.kernel_ir'

class CollectiveKind(str, Enum):
    ALL_REDUCE = 'ALL_REDUCE'

CollectiveKind.__module__ = 'mesh_ir.ir.kernel_ir'

class DistributionKind(str, Enum):
    PARTITIONED = 'PARTITIONED'
    REPLICATED = 'REPLICATED'
    PARTIAL_SUM = 'PARTIAL_SUM'

DistributionKind.__module__ = 'mesh_ir.ir.kernel_ir'

class KernelOpcode(str, Enum):
    ALLOC = 'ALLOC'
    VIEW = 'VIEW'
    DMA = 'DMA'
    GEMM = 'GEMM'
    BMM = 'BMM'
    MATRIX_EPILOGUE = 'MATRIX_EPILOGUE'
    VECTOR = 'VECTOR'
    DATA_MOVEMENT = 'DATA_MOVEMENT'
    REDUCE = 'REDUCE'
    SOFTMAX = 'SOFTMAX'
    NORM = 'NORM'
    COLLECTIVE = 'COLLECTIVE'
    LOCAL_REDUCE = 'LOCAL_REDUCE'
    LOCAL_COPY = 'LOCAL_COPY'
    RECV_WAIT = 'RECV_WAIT'
    BARRIER = 'BARRIER'

KernelOpcode.__module__ = 'mesh_ir.ir.kernel_ir'

class MatrixEpilogueAlgorithm(str, Enum):
    VECTOR_ACCUMULATION = 'VECTOR_ACCUMULATION'

MatrixEpilogueAlgorithm.__module__ = 'mesh_ir.ir.kernel_ir'

class MatrixPhase(str, Enum):
    DIRECT = 'DIRECT'
    ACCUMULATE_ONLY = 'ACCUMULATE_ONLY'
    ACCUMULATE_FIRST = 'ACCUMULATE_FIRST'
    ACCUMULATE_CONTINUE = 'ACCUMULATE_CONTINUE'
    ACCUMULATE_FINAL = 'ACCUMULATE_FINAL'

MatrixPhase.__module__ = 'mesh_ir.ir.kernel_ir'

class MovementAlgorithm(str, Enum):
    STRIDED_COPY = 'STRIDED_COPY'
    CONCAT = 'CONCAT'
    GATHER_ROWS = 'GATHER_ROWS'

MovementAlgorithm.__module__ = 'mesh_ir.ir.kernel_ir'

class NormAlgorithm(str, Enum):
    LAYER_NORM = 'LAYER_NORM'
    RMS_NORM = 'RMS_NORM'

NormAlgorithm.__module__ = 'mesh_ir.ir.kernel_ir'

class OperandAccessMode(str, Enum):
    READ = 'READ'
    WRITE = 'WRITE'

OperandAccessMode.__module__ = 'mesh_ir.ir.kernel_ir'

class ReduceKind(str, Enum):
    SUM = 'SUM'

ReduceKind.__module__ = 'mesh_ir.ir.kernel_ir'

class ReductionAlgorithm(str, Enum):
    LEFT_TO_RIGHT = 'LEFT_TO_RIGHT'

ReductionAlgorithm.__module__ = 'mesh_ir.ir.kernel_ir'

class SoftmaxAlgorithm(str, Enum):
    STABLE_MAX_SUM = 'STABLE_MAX_SUM'

SoftmaxAlgorithm.__module__ = 'mesh_ir.ir.kernel_ir'

class StateOrigin(str, Enum):
    EMPTY = 'EMPTY'
    EXTERNAL = 'EXTERNAL'
    PRE_RESIDENT = 'PRE_RESIDENT'
    PRODUCED = 'PRODUCED'

StateOrigin.__module__ = 'mesh_ir.ir.kernel_ir'

class SynthesizedTensorPurpose(str, Enum):
    PADDING = 'PADDING'
    COPY = 'COPY'
    ACCUMULATION = 'ACCUMULATION'
    PARTIAL_SUM = 'PARTIAL_SUM'
    REDUCTION = 'REDUCTION'
    DATA_MOVEMENT = 'DATA_MOVEMENT'

SynthesizedTensorPurpose.__module__ = 'mesh_ir.ir.kernel_ir'

class VectorAlgorithm(str, Enum):
    ELEMENTWISE = 'ELEMENTWISE'
    EMBEDDING_GATHER = 'EMBEDDING_GATHER'

VectorAlgorithm.__module__ = 'mesh_ir.ir.kernel_ir'

class EndpointSide(str, Enum):
    SRC = 'SRC'
    DST = 'DST'

EndpointSide.__module__ = 'mesh_ir.scheduled.model'

class FenceScope(str, Enum):
    DMA_READ = 'DMA_READ'
    DMA_WRITE = 'DMA_WRITE'
    P2P = 'P2P'
    HOST_SHARED_WRITE = 'HOST_SHARED_WRITE'
    ALL_INSTANCE = 'ALL_INSTANCE'

FenceScope.__module__ = 'mesh_ir.scheduled.model'

class ScheduledDependencyKind(str, Enum):
    KERNEL_CONTROL = 'KERNEL_CONTROL'
    KERNEL_STATE = 'KERNEL_STATE'
    RAW = 'RAW'
    WAR = 'WAR'
    WAW = 'WAW'
    DMA_PIN = 'DMA_PIN'
    DMA_COMPLETION = 'DMA_COMPLETION'
    SRAM_REUSE = 'SRAM_REUSE'
    STREAM_ORDER = 'STREAM_ORDER'
    LIFECYCLE = 'LIFECYCLE'

ScheduledDependencyKind.__module__ = 'mesh_ir.scheduled.model'

class AxiChannel(IntEnum):
    AW = 0
    W = 1
    B = 2
    AR = 3
    R = 4

AxiChannel.__module__ = 'mesh_ir.traffic'

class TrafficAggregateLevel(StrEnum):
    PROGRAM = 'PROGRAM'
    ENTRYPOINT = 'ENTRYPOINT'
    PROFILE = 'PROFILE'
    DETAIL = 'DETAIL'

TrafficAggregateLevel.__module__ = 'mesh_ir.traffic'

class TrafficDirection(StrEnum):
    READ = 'READ'
    WRITE = 'WRITE'
    P2P = 'P2P'
    LOCAL = 'LOCAL'

TrafficDirection.__module__ = 'mesh_ir.traffic'

__all__ = ['WorkUnit', 'Access', 'DType', 'DmaKind', 'Engine', 'Layout', 'MemorySpace', 'StorageClass', 'TensorRole', 'OpCode', 'CollectiveAlgorithm', 'CollectiveKind', 'DistributionKind', 'KernelOpcode', 'MatrixEpilogueAlgorithm', 'MatrixPhase', 'MovementAlgorithm', 'NormAlgorithm', 'OperandAccessMode', 'ReduceKind', 'ReductionAlgorithm', 'SoftmaxAlgorithm', 'StateOrigin', 'SynthesizedTensorPurpose', 'VectorAlgorithm', 'EndpointSide', 'FenceScope', 'ScheduledDependencyKind', 'AxiChannel', 'TrafficAggregateLevel', 'TrafficDirection']
