from __future__ import annotations

from mesh_ir.compat import RootSymbolBinding
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Symbol
from mesh_ir.ir.graph_ir import GraphModule, specialize_graph


def root_symbol_bindings_from_graph(graph: GraphModule) -> tuple[RootSymbolBinding, ...]:
    if type(graph) is not GraphModule:
        raise MeshIrError("E_CONFIG", "source Graph record type is invalid")
    graph.verify()
    values = {value.value_id: value for value in graph.values}
    bindings = []
    for function in graph.functions:
        for value_id in function.inputs:
            value = values[value_id]
            for axis, dimension in enumerate(value.shape):
                if type(dimension) is Symbol:
                    bindings.append(RootSymbolBinding(value.name, axis, dimension.symbol_id))
    return tuple(bindings)


def specialize_profiles(
    graph: GraphModule,
    root_bindings: tuple[tuple[str, str, int], ...],
    dto_bindings,
    profiles: tuple[tuple[str, tuple[tuple[str, int], ...]], ...],
) -> tuple[GraphModule, ...]:
    coordinate_symbols = {(item.input_name, item.axis): item.symbol_id for item in dto_bindings}
    logical_symbols: dict[str, int] = {}
    if root_bindings:
        for logical_name, input_name, axis in root_bindings:
            coordinate = (input_name, axis)
            if coordinate not in coordinate_symbols:
                raise MeshIrError("E_SHAPE_UNBOUND", "configured symbol binding is not a dynamic root", symbol=logical_name, input=input_name, axis=axis)
            symbol_id = coordinate_symbols[coordinate]
            if logical_name in logical_symbols and logical_symbols[logical_name] != symbol_id:
                raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "logical symbol maps to unequal Torch symbols", symbol=logical_name)
            logical_symbols[logical_name] = symbol_id
    else:
        logical_symbols = {symbol.name: symbol.symbol_id for symbol in graph.symbols}
    if set(logical_symbols.values()) != {symbol.symbol_id for symbol in graph.symbols}:
        raise MeshIrError("E_SHAPE_UNBOUND", "configured bindings do not name every exported root symbol")
    variants = []
    profile_ids = [profile_id for profile_id, _ in profiles]
    if any(not profile_id for profile_id in profile_ids) or len(set(profile_ids)) != len(profile_ids):
        raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "shape profile ids must be nonempty and unique")
    for profile_id, values in profiles:
        named = dict(values)
        if len(named) != len(values) or set(named) != set(logical_symbols):
            raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "shape profile names do not exactly match root symbols", profile_id=profile_id)
        concrete = {}
        for name, symbol_id in logical_symbols.items():
            if symbol_id in concrete and concrete[symbol_id] != named[name]:
                raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "aliases of one exported symbol have contradictory profile values", profile_id=profile_id, symbol_id=symbol_id)
            concrete[symbol_id] = named[name]
        variants.append(specialize_graph(graph, profile_id, concrete))
    return tuple(variants)
