from dataclasses import dataclass, fields, is_dataclass
from functools import wraps
from inspect import signature
from typing import Callable, ParamSpec, TypeVar

from mesh_ir.architecture import validate_arch


P = ParamSpec('P')
R = TypeVar('R')


def same_records(left: object, right: object) -> bool:
    pending = [(left, right)]
    while pending:
        first, second = pending.pop()
        if first is second:
            continue
        if type(first) is not type(second):
            return False
        if is_dataclass(first) and not isinstance(first, type):
            pending.extend((getattr(first, field.name), getattr(second, field.name)) for field in fields(first))
        elif type(first) is tuple:
            if len(first) != len(second):
                return False
            pending.extend(zip(first, second))
        elif first != second:
            return False
    return True


@dataclass(frozen=True, eq=False)
class _RecordIdentity:
    record: object

    def __hash__(self) -> int:
        return id(self.record)

    def __eq__(self, other: object) -> bool:
        return type(other) is _RecordIdentity and self.record is other.record


def derived_record(function: Callable[P, R]) -> Callable[P, R]:
    parameters = signature(function)

    @wraps(function)
    def derive(source, *arguments, **keywords):
        if isinstance(source, type) or not is_dataclass(source) or not source.__dataclass_params__.frozen:
            return function(source, *arguments, **keywords)
        keys = []
        bound = parameters.bind(source, *arguments, **keywords)
        bound.apply_defaults()
        for name, value in tuple(bound.arguments.items())[1:]:
            if name == 'arch':
                validate_arch(value)
                keys.append((id(value), value.digest()))
            elif is_dataclass(value) and value.__dataclass_params__.frozen:
                keys.append(_RecordIdentity(value))
            else:
                keys.append(value)
        key = (function.__module__, function.__qualname__, tuple(keys))
        cache = source.__dict__.setdefault('_derived_records', {})
        if key not in cache:
            cache[key] = function(source, *arguments, **keywords)
        return cache[key]

    return derive
