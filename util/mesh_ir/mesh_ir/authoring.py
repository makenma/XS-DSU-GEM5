from dataclasses import dataclass, replace
from math import prod

from mesh_ir.analysis.cost import ElementWorkDomain, MatrixWorkDomain, estimate_work_phases, project_kernel_cost
from mesh_ir.analysis.sram import plan_memory_sram
from mesh_ir.canonical import checked_mul_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, DType, DmaKind, INVALID_CORE_ID, Layout, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.graph_ir import ElementwiseAttrs, MatmulAttrs, OpCode
from mesh_ir.ir.kernel_ir import AllocAttrs, BufferObject, BufferView, ControlToken, DistributionKind, DmaAttrs, ElementRegion, GemmKernelAttrs, KernelComputation, KernelMemoryRecords, KernelOp, KernelOpcode, KernelTensor, KernelTile, MatrixPhase, OperandAccess, OperandAccessMode, Placement, StateOrigin, StateTransition, TensorShard, TensorState, ViewDeclarationAttrs, RecvWaitAttrs, VectorAlgorithm, VectorKernelAttrs
from mesh_ir.scheduled.model import ExternalSlotBacking, ObjectBacking
from mesh_ir.traffic import Binding, BindingSlot


@dataclass(frozen=True)
class AuthoredBuffer:
    tensor_id: int
    object_id: int
    view_id: int


class AuthoredMemory:
    def __init__(self, arch, core_id: int):
        if type(core_id) is not int or core_id not in arch.core_ids:
            raise MeshIrError('E_ABI_BOUNDS', 'authored memory core is not in the architecture')
        self.arch = arch
        self.core_id = core_id
        self._tensors = []
        self._computations = []
        self._placements = []
        self._shards = []
        self._objects = []
        self._views = []
        self._states = []
        self._tokens = []
        self._effects = []
        self._offsets = {}
        self._backings = []
        self._slots = []
        self._handles = {}
        self._current = {}
        self._writers = {}
        self._names = set()
        self._operation_names = set()
        self._next_transfer = 1

    @property
    def allocations(self):
        plan = plan_memory_sram(self.records(), self.arch)
        return tuple(replace(item, offset_bytes=self._offsets[item.object_id]) for item in plan.allocations)

    @property
    def external_backings(self):
        return tuple(self._backings)

    @property
    def binding_slots(self):
        return tuple(self._slots)

    @property
    def effects(self):
        return tuple(self._effects)

    def records(self) -> KernelMemoryRecords:
        declarations = []
        for obj in self._objects:
            declarations.append(KernelOp(len(declarations) + 1, 0, f'alloc:{obj.object_id}',
                KernelOpcode.ALLOC, obj.owner_core, 0, (), (), AllocAttrs(obj.object_id), (), None))
        for view in self._views:
            declarations.append(KernelOp(len(declarations) + 1, 0, f'view:{view.view_id}',
                KernelOpcode.VIEW, self._objects[view.object_id - 1].owner_core, 0, (), (),
                ViewDeclarationAttrs(view.view_id), (), None))
        return KernelMemoryRecords(tuple(self._tensors), tuple(self._computations), tuple(self._placements),
            tuple(self._shards), (), tuple(self._objects), tuple(self._views), tuple(self._states),
            tuple(self._tokens), tuple(declarations) + tuple(self._effects))

    def buffer(self, name: str, role: TensorRole, dtype: DType, shape: tuple[int, ...],
               space: MemorySpace, offset: int, *, content_sha256: str | None = None,
               capacity_bytes: int | None = None, core_id: int | None = None,
               storage_class: StorageClass | None = None,
               initial_state: StateOrigin | None = None,
               access: Access | None = None) -> AuthoredBuffer:
        if self._effects:
            raise MeshIrError('E_ABI_ORDER', 'authored buffers must precede operations')
        if type(name) is not str or not name or name in self._names:
            raise MeshIrError('E_ABI_DUPLICATE', 'authored buffer name must be unique')
        if type(shape) is not tuple or not shape or any(type(item) is not int or item <= 0 for item in shape):
            raise MeshIrError('E_EXPORT_LAYOUT', 'authored buffer needs a positive concrete shape')
        if type(dtype) is not DType or type(role) is not TensorRole or space not in (MemorySpace.HBM, MemorySpace.HOST_SHARED, MemorySpace.CORE_SRAM):
            raise MeshIrError('E_ABI_ENUM', 'authored buffer has an unsupported type or memory space')
        if type(offset) is not int or offset < 0:
            raise MeshIrError('E_ABI_BOUNDS', 'authored buffer offset must be nonnegative')
        size = checked_mul_u64(prod(shape), dtype.byte_width, 'authored buffer bytes')
        strides = tuple(prod(shape[index + 1:]) for index in range(len(shape)))
        external = space is not MemorySpace.CORE_SRAM
        storage_class = storage_class if storage_class is not None else (
            StorageClass.EXTERNAL if external else StorageClass.CORE_SRAM)
        if type(storage_class) is not StorageClass:
            raise MeshIrError('E_ABI_ENUM', 'authored tensor has an unsupported storage class')
        if access is None:
            access = Access.READ_ONLY if role in (TensorRole.INPUT, TensorRole.WEIGHT, TensorRole.CONSTANT) else Access.READ_WRITE
        if type(access) is not Access:
            raise MeshIrError('E_ABI_ENUM', 'authored tensor has an unsupported access mode')
        tensor_id = len(self._tensors) + 1
        tensor = KernelTensor(tensor_id, 0, None, tensor_id, 0, name, role, dtype, shape, strides,
            storage_class, access, size, size, content_sha256)
        handle = self._storage(name, tensor, space, offset, capacity_bytes, core_id, initial_state)
        self._tensors.append(tensor)
        return handle

    def view(self, source: AuthoredBuffer, name: str, offset_elements: int, shape: tuple[int, ...], *, core_id: int | None = None) -> AuthoredBuffer:
        obj, tensor = self._buffer(source)
        core_id = self.core_id if core_id is None else core_id
        if self._effects:
            raise MeshIrError('E_ABI_ORDER', 'authored views must precede operations')
        if type(name) is not str or not name or name in self._names:
            raise MeshIrError('E_ABI_DUPLICATE', 'authored view name must be unique')
        if type(shape) is not tuple or not shape or any(type(item) is not int or item <= 0 for item in shape):
            raise MeshIrError('E_EXPORT_LAYOUT', 'authored view needs a positive concrete shape')
        if type(offset_elements) is not int or offset_elements < 0 or type(core_id) is not int or core_id not in self.arch.core_ids:
            raise MeshIrError('E_ABI_BOUNDS', 'authored view offset or core is invalid')
        if obj.memory_space is MemorySpace.CORE_SRAM and obj.owner_core != core_id:
            raise MeshIrError('E_PLACEMENT_INFEASIBLE', 'authored view cannot change a local owner')
        parent = self._views[source.view_id - 1]
        size = checked_mul_u64(prod(shape), tensor.dtype.byte_width, 'authored view bytes')
        if offset_elements + prod(shape) > prod(parent.valid_shape):
            raise MeshIrError('E_EXPORT_LAYOUT', 'authored view leaves its parent')
        strides = tuple(prod(shape[index + 1:]) for index in range(len(shape)))
        tensor_id, shard_id, view_id = len(self._tensors) + 1, len(self._shards) + 1, len(self._views) + 1
        alias = KernelTensor(tensor_id, 0, None, tensor.alias_root_tensor_id,
            tensor.storage_offset_elements + offset_elements, name, tensor.role, tensor.dtype, shape, strides,
            tensor.storage_class, tensor.access, size, (tensor.storage_offset_elements + offset_elements) * tensor.dtype.byte_width + size, None)
        self._tensors.append(alias)
        placement_id = len(self._placements) + 1
        self._placements.append(Placement(placement_id, (core_id,)))
        self._shards.append(TensorShard(shard_id, tensor_id, placement_id, core_id,
            DistributionKind.PARTITIONED, (0,) * len(shape), shape, shape, 0))
        self._views.append(BufferView(view_id, obj.object_id, shard_id, (0,) * len(shape), shape, shape,
            parent.object_offset_elements + offset_elements, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0))
        handle = AuthoredBuffer(tensor_id, obj.object_id, view_id)
        self._handles[view_id] = handle
        self._names.add(name)
        return handle

    def stage(self, source: AuthoredBuffer, name: str, offset: int, *, core_id: int | None = None,
              space: MemorySpace = MemorySpace.CORE_SRAM, capacity_bytes: int | None = None) -> AuthoredBuffer:
        _, tensor = self._buffer(source)
        if self._effects:
            raise MeshIrError('E_ABI_ORDER', 'authored buffers must precede operations')
        if type(name) is not str or not name or name in self._names:
            raise MeshIrError('E_ABI_DUPLICATE', 'authored buffer name must be unique')
        if type(offset) is not int or offset < 0:
            raise MeshIrError('E_ABI_BOUNDS', 'authored buffer offset must be nonnegative')
        if type(space) is not MemorySpace or space not in (MemorySpace.CORE_SRAM, MemorySpace.HBM, MemorySpace.HOST_SHARED):
            raise MeshIrError('E_ABI_ENUM', 'authored storage has an unsupported memory space')
        return self._storage(name, tensor, space, offset, capacity_bytes, core_id)

    def _storage(self, name, tensor, space, offset, capacity_bytes=None, core_id=None, initial_state=None):
        core_id = self.core_id if core_id is None else core_id
        if type(core_id) is not int or core_id not in self.arch.core_ids:
            raise MeshIrError('E_ABI_BOUNDS', 'authored storage core is not in the architecture')
        tensor_id = tensor.tensor_id
        shape, strides, size = tensor.shape, tensor.strides, tensor.logical_extent_bytes
        external = space is not MemorySpace.CORE_SRAM
        if initial_state is not None and (type(initial_state) is not StateOrigin or
                initial_state not in (StateOrigin.EMPTY, StateOrigin.EXTERNAL) or
                (initial_state is StateOrigin.EXTERNAL and (not external or tensor.storage_class is not StorageClass.EXTERNAL))):
            raise MeshIrError('E_TENSOR_NOT_RESIDENT', 'authored initial state disagrees with its backing')
        origin = initial_state if initial_state is not None else (
            StateOrigin.EXTERNAL if external and tensor.role is not TensorRole.OUTPUT else StateOrigin.EMPTY)
        capacity = size if capacity_bytes is None else capacity_bytes
        if type(capacity) is not int or capacity < size:
            raise MeshIrError('E_RELOCATION', 'authored capacity does not cover the object')
        alignment = self.arch.axi_data_bytes if external else self.arch.sram_base_alignment_bytes
        region_kind = 'HBM' if space is MemorySpace.HBM else 'HOST_SHARED'
        regions = [index for index, item in enumerate(self.arch.regions) if item.kind == region_kind]
        if external and not regions:
            raise MeshIrError('E_RELOCATION', 'authored external buffer has no architecture region')
        shard = next((item for item in self._shards if item.tensor_id == tensor_id and item.owner_core == core_id), None)
        if shard is None:
            placement_id = len(self._placements) + 1
            self._placements.append(Placement(placement_id, (core_id,)))
            shard = TensorShard(len(self._shards) + 1, tensor_id, placement_id, core_id,
                DistributionKind.PARTITIONED, (0,) * len(shape), shape, shape, 0)
            self._shards.append(shard)
        object_id, view_id = len(self._objects) + 1, len(self._views) + 1
        self._objects.append(BufferObject(object_id, tensor.alias_root_tensor_id, INVALID_CORE_ID if external else core_id,
            space, shape, strides, size, alignment, tensor.role in (TensorRole.WEIGHT, TensorRole.KV_CACHE), 0))
        self._views.append(BufferView(view_id, object_id, shard.shard_id, (0,) * len(shape), shape, shape, 0,
            strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0))
        state_id = len(self._states) + 1
        self._states.append(TensorState(state_id, object_id, 0, origin))
        self._current[object_id] = state_id
        if external:
            slot_id = len(self._slots) + 1
            reference = Binding(slot_id, regions[0], INVALID_CORE_ID, offset, capacity, alignment, tensor.access)
            self._slots.append(BindingSlot(slot_id, name, space, regions[0], INVALID_CORE_ID, size, alignment, tensor.access, reference))
            self._backings.append(ObjectBacking(object_id, ExternalSlotBacking(slot_id)))
        else:
            self._offsets[object_id] = offset
        handle = AuthoredBuffer(tensor_id, object_id, view_id)
        self._handles[view_id] = handle
        self._names.add(name)
        return handle

    def _buffer(self, handle):
        if type(handle) is not AuthoredBuffer or self._handles.get(handle.view_id) is not handle:
            raise MeshIrError('E_ABI_BOUNDS', 'authored access uses a foreign buffer handle')
        return self._objects[handle.object_id - 1], self._tensors[handle.tensor_id - 1]

    def _effect(self, name, opcode, reads, destination, regions, attrs, computation_id=0, after=()):
        if type(name) is not str or not name or name in self._operation_names:
            raise MeshIrError('E_ABI_DUPLICATE', 'authored operation name must be unique')
        obj, tensor = self._buffer(destination)
        before = self._current[obj.object_id]
        if type(after) is not tuple or any(not any(item is previous for previous in self._effects) for item in after):
            raise MeshIrError('E_ABI_BOUNDS', 'authored dependency uses a foreign operation')
        dependencies = {item.done_token for item in after}
        read_accesses = []
        for handle, read_region in reads:
            source, _ = self._buffer(handle)
            state_id = self._current[source.object_id]
            if state_id in self._writers:
                dependencies.add(self._writers[state_id])
            read_accesses.append(OperandAccess(state_id, handle.view_id, read_region, OperandAccessMode.READ))
        if before in self._writers:
            dependencies.add(self._writers[before])
        after = len(self._states) + 1
        token_id = len(self._tokens) + 1
        operation = KernelOp(len(self._objects) + len(self._views) + len(self._effects) + 1, computation_id, f"{len(self._effects):08d}:{name}", opcode, attrs.issuing_core if type(attrs) is DmaAttrs else obj.owner_core,
            self._views[destination.view_id - 1].shard_id if computation_id else 0, tuple(read_accesses),
            tuple(StateTransition(before, after, destination.view_id, region) for region in regions), attrs, tuple(sorted(dependencies)), token_id)
        self._states.append(TensorState(after, destination.object_id, self._states[before - 1].version + 1, StateOrigin.PRODUCED))
        self._tokens.append(ControlToken(token_id))
        self._current[obj.object_id] = after
        self._writers[after] = token_id
        self._effects.append(operation)
        self._operation_names.add(name)
        return operation

    def copy(self, name, source, destination, *, source_region=None, destination_region=None, after=(), max_burst_beats=None):
        src, _ = self._buffer(source)
        dst, _ = self._buffer(destination)
        source_shape = self._views[source.view_id - 1].valid_shape
        source_region = source_region or ElementRegion((0,) * len(source_shape), source_shape, (1,) * len(source_shape))
        destination_shape = self._views[destination.view_id - 1].valid_shape
        destination_region = destination_region or ElementRegion((0,) * len(destination_shape), destination_shape, (1,) * len(destination_shape))
        return self.copy_regions(name, source, destination, ((source_region, destination_region),), after=after, max_burst_beats=max_burst_beats)

    def copy_regions(self, name, source, destination, regions: tuple[tuple[ElementRegion, ElementRegion], ...], *, after=(), max_burst_beats=None):
        src, _ = self._buffer(source)
        dst, _ = self._buffer(destination)
        if type(regions) is not tuple or not regions or any(type(pair) is not tuple or len(pair) != 2 or any(type(region) is not ElementRegion for region in pair) for pair in regions):
            raise MeshIrError('E_DMA_RANGE', 'authored copy needs nonempty paired regions')
        if src.memory_space in (MemorySpace.HBM, MemorySpace.HOST_SHARED) and dst.memory_space is MemorySpace.CORE_SRAM:
            kind, src_core, dst_core = DmaKind.LOAD, INVALID_CORE_ID, dst.owner_core
        elif src.memory_space is MemorySpace.CORE_SRAM and dst.memory_space in (MemorySpace.HBM, MemorySpace.HOST_SHARED):
            kind, src_core, dst_core = DmaKind.STORE, src.owner_core, INVALID_CORE_ID
        elif src.memory_space is MemorySpace.CORE_SRAM and dst.memory_space is MemorySpace.CORE_SRAM and src.owner_core != dst.owner_core:
            kind, src_core, dst_core = DmaKind.P2P_PUSH, src.owner_core, dst.owner_core
        else:
            raise MeshIrError('E_DMA_RANGE', 'authored copy must connect external storage or distinct local cores')
        transfer = self._next_transfer if kind is DmaKind.P2P_PUSH else 0
        owner = dst_core if kind is DmaKind.LOAD else src_core
        operation = self._effect(name, KernelOpcode.DMA, tuple((source, pair[0]) for pair in regions), destination,
            tuple(pair[1] for pair in regions), DmaAttrs(kind, owner, src_core, dst_core, transfer, b'', max_burst_beats=max_burst_beats), after=after)
        if transfer:
            self._next_transfer += 1
            token = len(self._tokens) + 1
            size = sum(prod(pair[0].shape) for pair in regions) * self._tensors[source.tensor_id - 1].dtype.byte_width
            receive = KernelOp(len(self._objects) + len(self._views) + len(self._effects) + 1, 0,
                f"{len(self._effects):08d}:receive:{name}", KernelOpcode.RECV_WAIT, dst_core, 0, (), (),
                RecvWaitAttrs(transfer, src_core, dst_core, size), (operation.done_token,), token)
            self._tokens.append(ControlToken(token))
            self._effects.append(receive)
            self._writers[self._current[dst.object_id]] = token
        return operation

    def fill(self, name, destination, pattern: bytes, *, region=None, after=()):
        obj, _ = self._buffer(destination)
        if obj.memory_space is not MemorySpace.CORE_SRAM or type(pattern) is not bytes or len(pattern) != 8:
            raise MeshIrError('E_DMA_RANGE', 'authored fill requires local SRAM and an exact eight-byte pattern')
        region = region or ElementRegion((0,) * len(obj.shape), obj.shape, (1,) * len(obj.shape))
        return self._effect(name, KernelOpcode.DMA, (), destination, (region,),
            DmaAttrs(DmaKind.LOCAL_FILL, obj.owner_core, obj.owner_core, obj.owner_core, 0, pattern), after=after)

    def matmul(self, name, left, right, destination):
        lhs, lhs_tensor = self._buffer(left)
        rhs, rhs_tensor = self._buffer(right)
        dst, dst_tensor = self._buffer(destination)
        if len(lhs.shape) != 2 or len(rhs.shape) != 2 or lhs.shape[1] != rhs.shape[0] or dst.shape != (lhs.shape[0], rhs.shape[1]):
            raise MeshIrError('E_EXPORT_LAYOUT', 'authored matrix shapes do not conform')
        m, k, n = lhs.shape[0], lhs.shape[1], rhs.shape[1]
        semantic = MatmulAttrs(accum_dtype=DType.FP32)
        phases = estimate_work_phases(OpCode.MATMUL, semantic, (lhs_tensor.dtype, rhs_tensor.dtype), dst_tensor.dtype, MatrixWorkDomain(1, m, n, k))
        cost = project_kernel_cost(lhs.footprint_bytes + rhs.footprint_bytes, dst.footprint_bytes, phases)
        attrs = GemmKernelAttrs(OpCode.MATMUL, semantic, KernelTile(0, 0, 0, 0, 1, m, n, k, 1, m, n, k), cost, MatrixPhase.DIRECT, 0)
        computation_id = len(self._computations) + 1
        lhs_region = ElementRegion((0, 0), lhs.shape, (1, 1))
        rhs_region = ElementRegion((0, 0), rhs.shape, (1, 1))
        operation = self._effect(name, KernelOpcode.GEMM, ((left, lhs_region), (right, rhs_region)), destination,
            (ElementRegion((0, 0), dst.shape, (1, 1)),), attrs, computation_id)
        self._computations.append(KernelComputation(computation_id, OpCode.MATMUL, (left.tensor_id, right.tensor_id), destination.tensor_id, semantic))
        self._tensors[destination.tensor_id - 1] = replace(dst_tensor, producer_computation_id=computation_id)
        return operation

    def add(self, name, left, right, destination):
        lhs, lhs_tensor = self._buffer(left)
        rhs, rhs_tensor = self._buffer(right)
        dst, dst_tensor = self._buffer(destination)
        if lhs.shape != rhs.shape or lhs.shape != dst.shape or len({lhs.owner_core, rhs.owner_core, dst.owner_core}) != 1:
            raise MeshIrError('E_EXPORT_LAYOUT', 'authored addition requires equal local shapes')
        semantic = ElementwiseAttrs()
        phases = estimate_work_phases(OpCode.ADD, semantic, (lhs_tensor.dtype, rhs_tensor.dtype), dst_tensor.dtype, ElementWorkDomain(prod(lhs.shape)))
        cost = project_kernel_cost(lhs.footprint_bytes + rhs.footprint_bytes, dst.footprint_bytes, phases)
        tile = KernelTile(0, 0, 0, 0, 1, lhs.shape[0], prod(lhs.shape[1:]), 1, 1, lhs.shape[0], prod(lhs.shape[1:]), 1)
        attrs = VectorKernelAttrs(OpCode.ADD, semantic, tile, cost, VectorAlgorithm.ELEMENTWISE)
        computation_id = len(self._computations) + 1
        region = ElementRegion((0,) * len(lhs.shape), lhs.shape, (1,) * len(lhs.shape))
        operation = self._effect(name, KernelOpcode.VECTOR, ((left, region), (right, region)), destination, (region,), attrs, computation_id)
        self._computations.append(KernelComputation(computation_id, OpCode.ADD, (left.tensor_id, right.tensor_id), destination.tensor_id, semantic))
        self._tensors[destination.tensor_id - 1] = replace(dst_tensor, producer_computation_id=computation_id)
        return operation
