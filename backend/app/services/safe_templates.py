from __future__ import annotations

import json
import re
from string import Formatter
from typing import Any


PATH_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(?:\.(?:[A-Za-z][A-Za-z0-9_]*|[0-9]+))*$"
)
ALLOWED_ROOTS = {"input", "nodes", "project", "run", "upstream", "value"}
_FORMATTER = Formatter()


class SafeTemplateError(ValueError):
    pass


def validate_template(template: str) -> list[str]:
    fields: list[str] = []
    try:
        parsed = list(_FORMATTER.parse(template))
    except ValueError as exc:
        raise SafeTemplateError(f"模板括号不完整：{exc}") from exc
    for _literal, field_name, format_spec, conversion in parsed:
        if field_name is None:
            continue
        validate_path(field_name)
        if format_spec:
            raise SafeTemplateError("模板不允许格式说明符")
        if conversion:
            raise SafeTemplateError("模板不允许类型转换")
        fields.append(field_name)
    return fields


def validate_path(path: str) -> None:
    if not PATH_PATTERN.fullmatch(path):
        raise SafeTemplateError(f"不安全或无效的变量路径：{path}")
    root = path.split(".", 1)[0]
    if root not in ALLOWED_ROOTS:
        raise SafeTemplateError(f"模板变量根不受支持：{root}")
    if any(part.startswith("_") for part in path.split(".")):
        raise SafeTemplateError("模板变量不能访问私有名称")


def resolve_path(context: dict[str, Any], path: str) -> Any:
    validate_path(path)
    current: Any = context
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                raise SafeTemplateError(f"变量路径不存在：{path}")
            current = current[part]
            continue
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                raise SafeTemplateError(f"变量索引越界：{path}")
            current = current[index]
            continue
        raise SafeTemplateError(f"变量路径不能继续读取：{path}")
    return current


def render_template(template: str, context: dict[str, Any]) -> str:
    validate_template(template)
    parts: list[str] = []
    for literal, field_name, _format_spec, _conversion in _FORMATTER.parse(template):
        parts.append(literal)
        if field_name is not None:
            parts.append(_render_value(resolve_path(context, field_name)))
    return "".join(parts)


def _render_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
