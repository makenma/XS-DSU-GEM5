from __future__ import annotations

import copy
from importlib.resources import files
import re
from typing import Any

import jsonschema
from referencing import Registry, Resource
from referencing import exceptions as referencing_exceptions
import yaml

from mesh_ir.canonical import U64_MAX, strict_json_loads
from mesh_ir.diagnostics import MeshIrError


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise MeshIrError("E_CONFIG", "YAML mapping keys must be strings")
        if key in result:
            raise MeshIrError("E_CONFIG", "duplicate YAML key", key=key)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


_SCHEMA_RESOURCE_NAME = re.compile(r"[a-z0-9_]+_v[0-9]+\.schema\.json")
_GRAPH_SCHEMA_NAME = "mesh_graph_v1.schema.json"
_GRAPH_SCHEMA_ID = "mesh-graph-v1"


def load_yaml_mapping(text: str, label: str) -> dict[str, Any]:
    try:
        value = yaml.load(text, Loader=_UniqueKeyLoader)
    except MeshIrError:
        raise
    except yaml.YAMLError as error:
        raise MeshIrError("E_CONFIG", f"malformed {label} YAML", detail=str(error)) from error
    if not isinstance(value, dict):
        raise MeshIrError("E_CONFIG", f"{label} root must be a mapping")
    return value


def load_schema(name: str) -> dict[str, Any]:
    if type(name) is not str or _SCHEMA_RESOURCE_NAME.fullmatch(name) is None:
        raise MeshIrError("E_CONFIG", "schema resource name is invalid", resource=name)
    try:
        schema = strict_json_loads(files("mesh_ir.schemas").joinpath(name).read_text(encoding="utf-8"))
        validator_type = jsonschema.validators.validator_for(schema)
        validator_type.check_schema(schema)
    except MeshIrError:
        raise
    except (OSError, jsonschema.SchemaError) as error:
        raise MeshIrError("E_CONFIG", "schema resource is unavailable or invalid", resource=name, detail=str(error)) from error
    return schema


def _value_paths(value: object, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        if not value:
            return {prefix}
        return set().union(*(_value_paths(item, f"{prefix}.{key}" if prefix else key) for key, item in value.items()))
    if isinstance(value, list):
        if not value:
            return {prefix}
        return set().union(*(_value_paths(item, f"{prefix}.{index}" if prefix else str(index)) for index, item in enumerate(value)))
    return {prefix}


def validate_schema(name: str, value: dict[str, Any], label: str, apply_defaults: bool = False) -> tuple[str, ...]:
    original_paths = _value_paths(value)
    schema = load_schema(name)
    base = jsonschema.validators.validator_for(schema)
    properties = base.VALIDATORS["properties"]
    type_validator = base.VALIDATORS["type"]

    def set_defaults(validator, property_map, instance, current_schema):
        if apply_defaults and isinstance(instance, dict):
            for field, subschema in property_map.items():
                if "default" in subschema and field not in instance:
                    instance[field] = copy.deepcopy(subschema["default"])
        yield from properties(validator, property_map, instance, current_schema)

    def strict_type(validator, expected, instance, current_schema):
        yield from type_validator(validator, expected, instance, current_schema)
        expected_types = (expected,) if isinstance(expected, str) else tuple(expected)
        if "integer" in expected_types and type(instance) is int and not -(1 << 63) <= instance <= U64_MAX:
            yield jsonschema.ValidationError("integer is outside the supported 64-bit range")

    checker = base.TYPE_CHECKER.redefine("integer", lambda checker, instance: type(instance) is int)
    validator_type = jsonschema.validators.extend(base, {"properties": set_defaults, "type": strict_type}, type_checker=checker)
    try:
        graph_schema = schema if schema.get("$id") == _GRAPH_SCHEMA_ID else load_schema(_GRAPH_SCHEMA_NAME)
        registry = Registry().with_resource(_GRAPH_SCHEMA_ID, Resource.from_contents(graph_schema))
        errors = sorted(validator_type(schema, registry=registry).iter_errors(value), key=lambda item: tuple(str(part) for part in item.absolute_path))
    except (referencing_exceptions.Unresolvable, referencing_exceptions.CannotDetermineSpecification) as error:
        raise MeshIrError("E_CONFIG", f"{label} schema reference resolution failed", detail=str(error)) from error
    if errors:
        error = errors[0]
        raise MeshIrError("E_CONFIG", f"{label} schema validation failed", path=".".join(map(str, error.absolute_path)), detail=error.message)
    return tuple(sorted(_value_paths(value) - original_paths))
