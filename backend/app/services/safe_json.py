from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from typing import Any


ALLOWED_TEMPLATE_VARIABLES = {
    "model",
    "messages",
    "system",
    "prompt",
    "temperature",
    "top_p",
    "max_tokens",
    "stream",
    "tools",
    "json_schema",
    "metadata",
    "credential",
}
MAX_TEMPLATE_DEPTH = 32
MAX_TEMPLATE_NODES = 20_000


class SafeMappingError(ValueError):
    pass


def render_safe_template(template: Any, variables: Mapping[str, Any]) -> Any:
    counter = [0]

    def render(value: Any, depth: int) -> Any:
        counter[0] += 1
        if counter[0] > MAX_TEMPLATE_NODES or depth > MAX_TEMPLATE_DEPTH:
            raise SafeMappingError("Request template is too large or deeply nested")
        if isinstance(value, dict):
            if set(value) == {"$var"}:
                variable = value["$var"]
                if not isinstance(variable, str) or variable not in ALLOWED_TEMPLATE_VARIABLES:
                    raise SafeMappingError(f"Template variable {variable!r} is not allowed")
                if variable not in variables:
                    raise SafeMappingError(f"Template variable {variable!r} is unavailable")
                return copy.deepcopy(variables[variable])
            if "$var" in value:
                raise SafeMappingError("$var placeholders cannot contain sibling fields")
            return {str(key): render(item, depth + 1) for key, item in value.items()}
        if isinstance(value, list):
            return [render(item, depth + 1) for item in value]
        if isinstance(value, float) and not math.isfinite(value):
            raise SafeMappingError("Non-finite numbers are not valid JSON template values")
        if isinstance(value, str) and any(marker in value for marker in ("{{", "{%", "${")):
            raise SafeMappingError("Executable or interpolated string templates are not supported")
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise SafeMappingError(f"Unsupported template value type: {type(value).__name__}")

    return render(template, 0)


def parse_json_path(path: str) -> list[str | int]:
    if not path.startswith("$"):
        raise SafeMappingError("JSONPath must begin with $")
    tokens: list[str | int] = []
    index = 1
    while index < len(path):
        character = path[index]
        if character == ".":
            index += 1
            start = index
            while index < len(path) and (path[index].isalnum() or path[index] in "_-" ):
                index += 1
            if start == index:
                raise SafeMappingError("JSONPath field name is missing")
            tokens.append(path[start:index])
            continue
        if character == "[":
            end = path.find("]", index + 1)
            if end < 0:
                raise SafeMappingError("JSONPath bracket is not closed")
            content = path[index + 1 : end]
            if content.isdigit():
                tokens.append(int(content))
            elif (
                len(content) >= 2
                and content[0] == content[-1]
                and content[0] in {"'", '"'}
            ):
                key = content[1:-1]
                if not key or "\\" in key:
                    raise SafeMappingError("Escaped or empty JSONPath keys are not supported")
                tokens.append(key)
            else:
                raise SafeMappingError("Only numeric or quoted-key brackets are supported")
            index = end + 1
            continue
        raise SafeMappingError(f"Unsupported JSONPath operator at position {index}")
    return tokens


def extract_json_path(value: Any, path: str, default: Any = None) -> Any:
    if path == "$":
        return value
    current = value
    for token in parse_json_path(path):
        if isinstance(token, int):
            if not isinstance(current, Sequence) or isinstance(current, (str, bytes)):
                return default
            if token >= len(current):
                return default
            current = current[token]
        else:
            if not isinstance(current, Mapping) or token not in current:
                return default
            current = current[token]
    return current


def set_json_path(document: Any, path: str, value: Any) -> Any:
    tokens = parse_json_path(path)
    if not tokens:
        return copy.deepcopy(value)
    if not isinstance(document, (dict, list)):
        raise SafeMappingError("Parameter mapping requires an object or array request template")
    current = document
    for position, token in enumerate(tokens):
        final = position == len(tokens) - 1
        next_token = None if final else tokens[position + 1]
        if isinstance(token, int):
            if not isinstance(current, list):
                raise SafeMappingError("JSONPath array index does not target an array")
            while len(current) <= token:
                current.append(None)
            if final:
                current[token] = copy.deepcopy(value)
            else:
                if current[token] is None:
                    current[token] = [] if isinstance(next_token, int) else {}
                current = current[token]
        else:
            if not isinstance(current, dict):
                raise SafeMappingError("JSONPath field does not target an object")
            if final:
                current[token] = copy.deepcopy(value)
            else:
                if token not in current or current[token] is None:
                    current[token] = [] if isinstance(next_token, int) else {}
                current = current[token]
    return document


def redact_bound_secret(value: Any, secret: str | None) -> Any:
    if not secret:
        return copy.deepcopy(value)
    if isinstance(value, dict):
        return {key: redact_bound_secret(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_bound_secret(item, secret) for item in value]
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    return value


def find_secret_material(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            child_path = f"{path}.{key}"
            if _looks_like_secret_key(lowered):
                if item not in (None, "", "[REDACTED]") and item != {
                    "$var": "credential"
                }:
                    findings.append(child_path)
            findings.extend(find_secret_material(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(find_secret_material(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        lowered = value.lower().strip()
        if lowered.startswith(("sk-", "bearer ", "basic ")):
            findings.append(path)
    return sorted(set(findings))


def _looks_like_secret_key(value: str) -> bool:
    normalized = value.replace("-", "_").replace(" ", "_")
    parts = {part for part in normalized.split("_") if part}
    if normalized in {
        "api_key",
        "apikey",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "credential",
    }:
        return True
    return bool(parts & {"authorization", "password", "secret", "token", "credential", "cookie"}) or (
        "key" in parts and bool(parts & {"api", "auth", "provider", "access"})
    )
