"""
Converts YAML documents into JSON-like trees (dict, list, str, int, float, bool, None).
Plain scalars are typed with the YAML core schema (null, bool, int, float);
quoted scalars always stay strings, so "4" and 4 are different values.
"""

import re

import yaml

from .errors import ConfigurationError

_INT = re.compile(r"^[-+]?[0-9]+$")
_FLOAT = re.compile(r"^[-+]?([0-9]+\.[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?$")


def parse(text: str, source_name: str):
    try:
        root = next(iter(yaml.compose_all(text, Loader=yaml.SafeLoader)), None)
    except yaml.YAMLError as ex:
        mark = getattr(ex, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ConfigurationError(f"{source_name}: invalid YAML{where}: {ex}") from ex

    return None if root is None else _to_json(root)


def parse_file(path: str):
    with open(path, encoding="utf-8-sig") as f:
        return parse(f.read(), path)


def _to_json(node):
    if isinstance(node, yaml.MappingNode):
        result = {}
        for key, value in node.value:
            result[str(key.value) if isinstance(key, yaml.ScalarNode) else ""] = _to_json(value)
        return result

    if isinstance(node, yaml.SequenceNode):
        return [_to_json(item) for item in node.value]

    if isinstance(node, yaml.ScalarNode):
        return _scalar(node)

    raise ConfigurationError(f"Unsupported YAML node at line {node.start_mark.line + 1}")


def _scalar(node: "yaml.ScalarNode"):
    value = node.value or ""

    # Quoted, literal (|) and folded (>) scalars are strings; style None means plain.
    if node.style is not None:
        return value

    if value in ("", "~", "null", "Null", "NULL"):
        return None
    if value in ("true", "True", "TRUE"):
        return True
    if value in ("false", "False", "FALSE"):
        return False

    if _INT.match(value):
        return int(value)

    if _FLOAT.match(value):
        return float(value)

    return value
