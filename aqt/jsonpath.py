"""The small JSONPath subset scenarios need: $, .name, ['name'], [0] and [*]."""

import json

from .errors import ConfigurationError


def select(root, path: str) -> list:
    """All values matched by the path. A present JSON null is returned as a None element."""
    current = [root]
    for kind, value in _parse(path):
        nxt = []
        for node in current:
            if kind == "wildcard":
                if isinstance(node, list):
                    nxt.extend(node)
                elif isinstance(node, dict):
                    nxt.extend(node.values())
            elif kind == "index":
                if isinstance(node, list) and 0 <= value < len(node):
                    nxt.append(node[value])
            else:
                if isinstance(node, dict) and value in node:
                    nxt.append(node[value])
        current = nxt
    return current


def exists(root, path: str) -> bool:
    return len(select(root, path)) > 0


def as_text(node) -> str:
    """Text form of a matched value: strings unquoted, null as "null", other JSON compact."""
    if node is None:
        return "null"
    if isinstance(node, str):
        return node
    if isinstance(node, bool):
        return "true" if node else "false"
    if isinstance(node, int):
        return str(node)
    if isinstance(node, float):
        return str(int(node)) if node.is_integer() and abs(node) < 1e16 else repr(node)
    return to_json(node)


def to_json(node) -> str:
    return json.dumps(node, separators=(",", ":"), ensure_ascii=False)


def try_parse_json(text: str, media_type: str | None):
    """Parses a response body when it is (or looks like) JSON; returns None otherwise."""
    trimmed = text.lstrip()
    looks_like_json = (media_type is not None and "json" in media_type.lower()) or trimmed.startswith(("{", "["))
    if not looks_like_json or not trimmed:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _parse(path: str):
    p = path.strip()
    if p.startswith("$"):
        p = p[1:]

    result = []
    i = 0
    while i < len(p):
        if p[i] == ".":
            i += 1
            start = i
            while i < len(p) and p[i] not in ".[":
                i += 1
            name = p[start:i]
            result.append(("wildcard", None) if name == "*" else ("name", name))
        elif p[i] == "[":
            end = p.find("]", i)
            if end < 0:
                raise ConfigurationError(f"Invalid JSON path '{path}': missing ']'")
            inner = p[i + 1:end].strip()
            i = end + 1

            if inner == "*":
                result.append(("wildcard", None))
            elif len(inner) >= 2 and inner[0] in "'\"":
                result.append(("name", inner[1:-1]))
            elif inner.isdigit() and inner.isascii():
                result.append(("index", int(inner)))
            else:
                raise ConfigurationError(f"Invalid JSON path '{path}': unsupported selector [{inner}]")
        else:
            # Allow paths without a leading '$.' such as "consentId".
            start = i
            while i < len(p) and p[i] not in ".[":
                i += 1
            result.append(("name", p[start:i]))

    return result
