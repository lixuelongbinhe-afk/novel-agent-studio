from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from app.schemas import (
    NormalizedContentPart,
    NormalizedMessage,
    NormalizedModelRequest,
    NormalizedModelResponse,
    NormalizedProviderError,
    NormalizedToolCall,
    NormalizedToolDefinition,
    NormalizedUsage,
)
from app.schemas.model_control import CapabilityStatus
from app.services.control_errors import ModelControlError


StructuredMode = Literal["none", "native_schema", "json_object", "prompted_json"]


@dataclass(frozen=True)
class PreparedRequest:
    request: NormalizedModelRequest
    warnings: tuple[str, ...]
    structured_mode: StructuredMode
    schema: dict[str, Any] | None
    emulated_tools: tuple[NormalizedToolDefinition, ...]
    repair_allowed: bool


def prepare_request(
    request: NormalizedModelRequest,
    capabilities: dict[str, CapabilityStatus],
    *,
    allow_degradation: bool,
) -> PreparedRequest:
    working = request.model_copy(deep=True)
    warnings: list[str] = []

    system_status = capabilities.get("system_prompt", "unknown")
    if any(message.role == "system" for message in working.messages) and system_status != "supported":
        if not allow_degradation:
            raise ModelControlError(
                "capability_unsupported", "模型不支持原生 System Prompt"
            )
        working = _emulate_system_prompt(working)
        warnings.append("System Prompt 非原生支持，已显式合并到用户消息。")

    if working.top_p is not None and capabilities.get("top_p", "unknown") != "supported":
        if not allow_degradation:
            raise ModelControlError("capability_unsupported", "模型不支持 top_p 参数")
        working.top_p = None
        warnings.append("模型不支持 top_p，已移除该参数。")
    if capabilities.get("temperature", "unknown") == "unsupported":
        if not allow_degradation and working.temperature != 0.7:
            raise ModelControlError("capability_unsupported", "模型不支持 temperature 参数")
        if working.temperature != 0.7:
            warnings.append("模型不支持 temperature，已回退为默认值。")
        working.temperature = 0.7
    if capabilities.get("max_output_tokens", "unknown") in {"unsupported", "unknown"}:
        warnings.append("Provider 未确认 max output tokens 能力，仍会保留硬上限并校验响应。")

    emulated_tools: tuple[NormalizedToolDefinition, ...] = ()
    if working.tools and capabilities.get("tool_calling", "unknown") != "supported":
        if any(tool.side_effect for tool in working.tools):
            raise ModelControlError(
                "capability_unsupported",
                "包含副作用的工具调用不能降级为文本模拟",
            )
        if not allow_degradation:
            raise ModelControlError("capability_unsupported", "模型不支持原生工具调用")
        emulated_tools = tuple(working.tools)
        working = _emulate_side_effect_free_tools(working)
        warnings.append("工具调用非原生支持；仅对无副作用工具使用受控 JSON 模拟。")

    schema = working.json_schema
    mode: StructuredMode = "none"
    if working.response_format == "json" or schema is not None or emulated_tools:
        if schema is not None:
            _validate_schema(schema)
        schema_status = capabilities.get("json_schema", "unknown")
        object_status = capabilities.get("json_object", "unknown")
        if schema is not None and schema_status == "supported" and not emulated_tools:
            mode = "native_schema"
        elif object_status in {"supported", "degraded", "emulated"}:
            mode = "json_object"
            working.json_schema = None
            working.response_format = "json"
            if schema is not None:
                warnings.append("已降级为 JSON Object，并在本地执行 JSON Schema 校验。")
        else:
            if not allow_degradation:
                raise ModelControlError("capability_unsupported", "模型不支持结构化 JSON")
            mode = "prompted_json"
            working.json_schema = None
            working.response_format = "text"
            working = _append_json_instruction(working, schema)
            warnings.append("已降级为提示词 JSON，将使用安全提取器和本地校验。")

    return PreparedRequest(
        request=working,
        warnings=tuple(warnings),
        structured_mode=mode,
        schema=schema,
        emulated_tools=emulated_tools,
        repair_allowed=mode in {"json_object", "prompted_json"},
    )


def normalize_structured_response(
    response: NormalizedModelResponse, prepared: PreparedRequest
) -> NormalizedModelResponse:
    if response.error is not None or prepared.structured_mode == "none":
        return response.model_copy(
            update={"warnings": [*response.warnings, *prepared.warnings]}
        )
    value = response.structured_data
    if value is None:
        value = extract_json_value(response.text)
    if value is None:
        raise ValidationError("响应中没有可安全提取的 JSON 值")
    if prepared.schema is not None:
        Draft202012Validator(prepared.schema).validate(value)

    tool_calls = response.tool_calls
    structured_data: dict[str, Any]
    if isinstance(value, dict):
        structured_data = value
    else:
        structured_data = {"value": value}
    if prepared.emulated_tools:
        tool_calls = _parse_emulated_tool_calls(value, prepared.emulated_tools)
    return response.model_copy(
        update={
            "structured_data": structured_data,
            "tool_calls": tool_calls,
            "warnings": [*response.warnings, *prepared.warnings],
        }
    )


def build_repair_request(
    prepared: PreparedRequest, invalid_text: str, validation_message: str
) -> NormalizedModelRequest:
    schema_text = json.dumps(
        prepared.schema or {"type": "object"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    bounded_invalid = invalid_text[:100_000]
    prompt = (
        "Repair the JSON once. Return JSON only; do not add prose.\n"
        f"Schema: {schema_text}\n"
        f"Validation error: {validation_message[:1000]}\n"
        f"Invalid value: {bounded_invalid}"
    )
    return NormalizedModelRequest(
        model=prepared.request.model,
        messages=[
            NormalizedMessage(
                role="user", content=[NormalizedContentPart(type="text", text=prompt)]
            )
        ],
        temperature=0,
        max_tokens=min(1024, max(64, prepared.request.max_tokens)),
        response_format=(
            "json" if prepared.structured_mode == "json_object" else "text"
        ),
        metadata={**prepared.request.metadata, "nas_operation": "schema_repair"},
    )


def schema_failure_response(
    request: NormalizedModelRequest,
    response: NormalizedModelResponse,
    message: str,
    warnings: list[str],
) -> NormalizedModelResponse:
    return NormalizedModelResponse(
        model=response.model or request.model,
        text=response.text,
        content=response.content,
        structured_data=None,
        tool_calls=[],
        finish_reason="error",
        usage=response.usage,
        request_id=response.request_id,
        error=NormalizedProviderError(
            code="schema_validation",
            message=f"结构化输出校验失败：{message[:1000]}",
            retryable=False,
            status_code=422,
            request_id=response.request_id or None,
        ),
        warnings=[*response.warnings, *warnings, "有限修复失败，已进入人工处理。"],
    )


def extract_json_value(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped:
        return None
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(stripped)
        if not stripped[end:].strip():
            return value
    except json.JSONDecodeError:
        pass
    for index, character in enumerate(stripped):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        return value
    return None


def empty_schema_error(request: NormalizedModelRequest, message: str) -> NormalizedModelResponse:
    return NormalizedModelResponse(
        model=request.model,
        text="",
        usage=NormalizedUsage(),
        request_id="",
        finish_reason="error",
        error=NormalizedProviderError(
            code="schema_validation", message=message, status_code=422
        ),
    )


def _validate_schema(schema: dict[str, Any]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ModelControlError(
            "invalid_schema", f"JSON Schema 无效：{exc.message}", status_code=422
        ) from exc


def _emulate_system_prompt(request: NormalizedModelRequest) -> NormalizedModelRequest:
    system_text = "\n".join(
        part.text or ""
        for message in request.messages
        if message.role == "system"
        for part in message.content
        if part.type == "text"
    ).strip()
    messages = [message for message in request.messages if message.role != "system"]
    prefix = f"[System instructions]\n{system_text}\n[End system instructions]\n"
    if messages and messages[0].role == "user":
        first = messages[0].model_copy(deep=True)
        first.content.insert(0, NormalizedContentPart(type="text", text=prefix))
        messages[0] = first
    else:
        messages.insert(
            0,
            NormalizedMessage(
                role="user", content=[NormalizedContentPart(type="text", text=prefix)]
            ),
        )
    return request.model_copy(update={"messages": messages})


def _emulate_side_effect_free_tools(
    request: NormalizedModelRequest,
) -> NormalizedModelRequest:
    definitions = [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        for tool in request.tools
    ]
    instruction = (
        "Available side-effect-free tools are listed below. Do not execute anything. "
        "Return only JSON in the form "
        '{"tool_calls":[{"name":"tool_name","arguments":{}}]}.\n'
        + json.dumps(definitions, ensure_ascii=False, sort_keys=True)
    )
    messages = [*request.messages]
    messages.append(
        NormalizedMessage(
            role="user", content=[NormalizedContentPart(type="text", text=instruction)]
        )
    )
    return request.model_copy(
        update={"messages": messages, "tools": [], "tool_choice": "none"}
    )


def _append_json_instruction(
    request: NormalizedModelRequest, schema: dict[str, Any] | None
) -> NormalizedModelRequest:
    schema_text = json.dumps(
        schema or {"type": "object"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    messages = [*request.messages]
    messages.append(
        NormalizedMessage(
            role="user",
            content=[
                NormalizedContentPart(
                    type="text",
                    text=(
                        "Return exactly one JSON value and no Markdown. "
                        f"The value must satisfy this JSON Schema: {schema_text}"
                    ),
                )
            ],
        )
    )
    return request.model_copy(update={"messages": messages})


def _parse_emulated_tool_calls(
    value: Any, tools: tuple[NormalizedToolDefinition, ...]
) -> list[NormalizedToolCall]:
    if not isinstance(value, dict) or not isinstance(value.get("tool_calls"), list):
        raise ValidationError("模拟工具输出缺少 tool_calls 数组")
    allowed = {tool.name for tool in tools}
    calls: list[NormalizedToolCall] = []
    for index, raw in enumerate(value["tool_calls"]):
        if not isinstance(raw, dict):
            raise ValidationError("模拟工具调用必须是对象")
        name = raw.get("name")
        arguments = raw.get("arguments", {})
        if not isinstance(name, str) or name not in allowed:
            raise ValidationError("模拟工具调用使用了未声明的工具")
        if not isinstance(arguments, dict):
            raise ValidationError("模拟工具参数必须是对象")
        calls.append(
            NormalizedToolCall(id=f"emulated-{index + 1}", name=name, arguments=arguments)
        )
    return calls
