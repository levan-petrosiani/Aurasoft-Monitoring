"""
Binds parsed YAML (dicts) to dataclasses, like System.Text.Json in the C# version:
property names are camelCase and matched case-insensitively, unknown properties are rejected to catch typos,
and values must have the declared type.

Field metadata:
  required=True  the property must be present
  skip=True      not bound from YAML (set by the loader)
Fields typed ``Any`` (or dict[str, Any]) keep the raw JSON value.
"""

import copy
import dataclasses
import types
import typing
from typing import Any

from .errors import ConfigurationError


def required(default=None):
    return dataclasses.field(default=default, metadata={"required": True})


def skip(default=None):
    return dataclasses.field(default=default, metadata={"skip": True})


def camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def bind(cls, data, path: str = "$"):
    if not isinstance(data, dict):
        raise ConfigurationError(f"{path}: expected a mapping for {cls.__name__}, got {_kind(data)}")

    hints = typing.get_type_hints(cls)
    fields = {camel(f.name).lower(): f for f in dataclasses.fields(cls) if not f.metadata.get("skip")}

    kwargs = {}
    for key, value in data.items():
        field = fields.get(key.lower())
        if field is None:
            raise ConfigurationError(f"{path}: unknown property '{key}' ({cls.__name__})")
        kwargs[field.name] = _convert(hints[field.name], value, f"{path}.{key}")

    for field in fields.values():
        if field.metadata.get("required") and field.name not in kwargs:
            raise ConfigurationError(f"{path}: required property '{camel(field.name)}' is missing ({cls.__name__})")

    # Mutable defaults (lists) are created fresh for every instance by the dataclasses themselves.
    return cls(**kwargs)


def _convert(tp, value, path):
    if tp is Any:
        return copy.deepcopy(value)

    origin = typing.get_origin(tp)
    args = typing.get_args(tp)

    if origin in (typing.Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        inner = [a for a in args if a is not type(None)]
        return _convert(inner[0], value, path)

    if tp is str:
        if not isinstance(value, str):
            raise ConfigurationError(f"{path}: expected a string, got {_kind(value)}")
        return value

    if tp is bool:
        if not isinstance(value, bool):
            raise ConfigurationError(f"{path}: expected true or false, got {_kind(value)}")
        return value

    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigurationError(f"{path}: expected a whole number, got {_kind(value)}")
        return value

    if origin is list:
        if not isinstance(value, list):
            raise ConfigurationError(f"{path}: expected a list, got {_kind(value)}")
        return [_convert(args[0], item, f"{path}[{i}]") for i, item in enumerate(value)]

    if origin is dict:
        if not isinstance(value, dict):
            raise ConfigurationError(f"{path}: expected a mapping, got {_kind(value)}")
        return {k: _convert(args[1], v, f"{path}.{k}") for k, v in value.items()}

    if dataclasses.is_dataclass(tp):
        return bind(tp, value, path)

    raise ConfigurationError(f"{path}: unsupported type {tp}")


def _kind(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a mapping"
    return type(value).__name__
