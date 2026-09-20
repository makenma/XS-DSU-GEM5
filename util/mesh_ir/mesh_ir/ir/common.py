from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, TypeAlias

from mesh_ir.canonical import U64_MAX
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated.semantic_enums import Access, DType, DmaKind, Engine, Layout, MemorySpace, StorageClass, TensorRole


INVALID_CORE_ID = 0xFFFF


def _checked(value: int, what: str) -> int:
    if type(value) is not int or not 0 <= value <= U64_MAX:
        raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "shape arithmetic is outside u64", field=what, value=value)
    return value


@dataclass(frozen=True)
class Const:
    value: int

    def __post_init__(self):
        _checked(self.value, "dimension")

    def evaluate(self, bindings: Mapping[int, int]) -> int:
        return self.value

    def symbol_ids(self) -> frozenset[int]:
        return frozenset()


@dataclass(frozen=True)
class Symbol:
    symbol_id: int
    name: str
    minimum: int
    maximum: int
    multiple_of: int = 1

    def __post_init__(self):
        if type(self.symbol_id) is not int or self.symbol_id < 1 or not self.name:
            raise MeshIrError("E_CONFIG", "symbol identity is invalid")
        _checked(self.minimum, "symbol minimum")
        _checked(self.maximum, "symbol maximum")
        if self.minimum > self.maximum or type(self.multiple_of) is not int or self.multiple_of < 1:
            raise MeshIrError("E_CONFIG", "symbol bounds are invalid", symbol=self.name)

    def evaluate(self, bindings: Mapping[int, int]) -> int:
        if self.symbol_id not in bindings:
            raise MeshIrError("E_SHAPE_UNBOUND", "shape symbol is unbound", symbol_id=self.symbol_id, symbol=self.name)
        value = bindings[self.symbol_id]
        if type(value) is not int or not self.minimum <= value <= self.maximum or value % self.multiple_of:
            raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "shape binding violates bounds or divisibility", symbol_id=self.symbol_id, symbol=self.name, value=value)
        return value

    def symbol_ids(self) -> frozenset[int]:
        return frozenset((self.symbol_id,))


@dataclass(frozen=True)
class Add:
    lhs: "DimExpr"
    rhs: "DimExpr"

    def evaluate(self, bindings: Mapping[int, int]) -> int:
        return _checked(self.lhs.evaluate(bindings) + self.rhs.evaluate(bindings), "dimension addition")

    def symbol_ids(self) -> frozenset[int]:
        return self.lhs.symbol_ids() | self.rhs.symbol_ids()


@dataclass(frozen=True)
class MulByConst:
    value: "DimExpr"
    factor: int

    def __post_init__(self):
        _checked(self.factor, "dimension multiplier")

    def evaluate(self, bindings: Mapping[int, int]) -> int:
        return _checked(self.value.evaluate(bindings) * self.factor, "dimension multiplication")

    def symbol_ids(self) -> frozenset[int]:
        return self.value.symbol_ids()


@dataclass(frozen=True)
class FloorDivByConst:
    value: "DimExpr"
    divisor: int

    def __post_init__(self):
        if type(self.divisor) is not int or self.divisor < 1:
            raise MeshIrError("E_CONFIG", "shape divisor must be positive")

    def evaluate(self, bindings: Mapping[int, int]) -> int:
        return self.value.evaluate(bindings) // self.divisor

    def symbol_ids(self) -> frozenset[int]:
        return self.value.symbol_ids()


@dataclass(frozen=True)
class CeilDivByConst:
    value: "DimExpr"
    divisor: int

    def __post_init__(self):
        if type(self.divisor) is not int or self.divisor < 1:
            raise MeshIrError("E_CONFIG", "shape divisor must be positive")

    def evaluate(self, bindings: Mapping[int, int]) -> int:
        value = self.value.evaluate(bindings)
        return _checked((value + self.divisor - 1) // self.divisor, "dimension ceiling division")

    def symbol_ids(self) -> frozenset[int]:
        return self.value.symbol_ids()


DimExpr: TypeAlias = Const | Symbol | Add | MulByConst | FloorDivByConst | CeilDivByConst


@dataclass(frozen=True)
class FixedStride:
    value: int

    def __post_init__(self):
        _checked(self.value, "stride")

    def evaluate(self, bindings: Mapping[int, int]) -> int:
        return self.value

    def symbol_ids(self) -> frozenset[int]:
        return frozenset()


@dataclass(frozen=True)
class ShapeProductStride:
    dimensions: tuple[DimExpr, ...]
    factor: int = 1

    def __post_init__(self):
        _checked(self.factor, "stride factor")

    def evaluate(self, bindings: Mapping[int, int]) -> int:
        result = self.factor
        for dimension in self.dimensions:
            result = _checked(result * dimension.evaluate(bindings), "derived layout stride")
        return result

    def symbol_ids(self) -> frozenset[int]:
        return frozenset().union(*(dimension.symbol_ids() for dimension in self.dimensions))


StrideExpr: TypeAlias = FixedStride | ShapeProductStride


def contiguous_strides(shape: tuple[DimExpr, ...]) -> tuple[StrideExpr, ...]:
    return tuple(ShapeProductStride(shape[index + 1 :]) for index in range(len(shape)))


def dimension_data(expr: DimExpr) -> dict[str, object]:
    if isinstance(expr, Const):
        return {"kind": "const", "value": expr.value}
    if isinstance(expr, Symbol):
        return {"kind": "symbol", "symbol_id": expr.symbol_id, "name": expr.name, "minimum": expr.minimum, "maximum": expr.maximum, "multiple_of": expr.multiple_of}
    if isinstance(expr, Add):
        return {"kind": "add", "lhs": dimension_data(expr.lhs), "rhs": dimension_data(expr.rhs)}
    if isinstance(expr, MulByConst):
        return {"kind": "mul_by_const", "value": dimension_data(expr.value), "factor": expr.factor}
    if isinstance(expr, FloorDivByConst):
        return {"kind": "floor_div_by_const", "value": dimension_data(expr.value), "divisor": expr.divisor}
    return {"kind": "ceil_div_by_const", "value": dimension_data(expr.value), "divisor": expr.divisor}


def stride_data(expr: StrideExpr) -> dict[str, object]:
    if isinstance(expr, FixedStride):
        return {"kind": "fixed", "value": expr.value}
    return {"kind": "shape_product", "dimensions": [dimension_data(item) for item in expr.dimensions], "factor": expr.factor}
