from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any, cast

from fastapi import HTTPException
from jsonschema import Draft202012Validator, SchemaError
from sqlalchemy.orm import Session

from app import models
from app.repositories import get_or_404
from app.schemas import (
    WorkflowEdgeWrite,
    WorkflowNodeWrite,
    WorkflowValidationIssue,
    WorkflowValidationRead,
)
from app.services.safe_templates import SafeTemplateError, validate_path, validate_template


SUPPORTED_NODE_TYPES = {
    "start",
    "input_mapping",
    "context_retrieval",
    "agent",
    "human_approval",
    "state_extraction",
    "proposed_changes",
    "database_writeback",
    "merge",
    "condition",
    "text_template",
    "data_transform",
    "output",
}
CONDITION_OPERATORS = {
    "equals",
    "not_equals",
    "contains",
    "exists",
    "gt",
    "gte",
    "lt",
    "lte",
}
TRANSFORM_OPERATIONS = {"passthrough", "pick", "rename", "to_json", "from_json"}


def validate_graph(
    db: Session,
    project_id: int,
    nodes: list[WorkflowNodeWrite],
    edges: list[WorkflowEdgeWrite],
) -> WorkflowValidationRead:
    get_or_404(db, models.Project, project_id)
    issues: list[WorkflowValidationIssue] = []
    by_key: dict[str, WorkflowNodeWrite] = {}
    for node in nodes:
        if node.key in by_key:
            issues.append(_issue("duplicate_node_key", f"节点 key 重复：{node.key}", [node.key]))
        else:
            by_key[node.key] = node
        if len(_dump(node.config).encode("utf-8")) > 250_000:
            issues.append(_issue("node_config_too_large", f"节点 {node.key} 配置超过 250 KB", [node.key]))

    edge_keys: set[str] = set()
    valid_edges: list[WorkflowEdgeWrite] = []
    for edge in edges:
        if edge.key in edge_keys:
            issues.append(_issue("duplicate_edge_key", f"边 key 重复：{edge.key}"))
        edge_keys.add(edge.key)
        if edge.source not in by_key or edge.target not in by_key:
            issues.append(
                _issue(
                    "missing_edge_node",
                    f"边 {edge.key} 引用了不存在的节点",
                    [edge.source, edge.target],
                )
            )
            continue
        if edge.source == edge.target:
            issues.append(_issue("self_loop", f"节点 {edge.source} 不能连接自身", [edge.source]))
            continue
        valid_edges.append(edge)

    starts = [node for node in nodes if node.type == "start"]
    outputs = [node for node in nodes if node.type == "output"]
    if len(starts) != 1:
        issues.append(_issue("start_count", f"工作流必须有且只有一个 Start，当前为 {len(starts)}"))
    if len(outputs) != 1:
        issues.append(_issue("output_count", f"工作流必须有且只有一个 Output，当前为 {len(outputs)}"))

    incoming: dict[str, list[WorkflowEdgeWrite]] = {key: [] for key in by_key}
    outgoing: dict[str, list[WorkflowEdgeWrite]] = {key: [] for key in by_key}
    for edge in valid_edges:
        outgoing[edge.source].append(edge)
        incoming[edge.target].append(edge)

    for node in starts:
        if incoming.get(node.key):
            issues.append(_issue("start_incoming", "Start 不能有入边", [node.key]))
    for node in outputs:
        if outgoing.get(node.key):
            issues.append(_issue("output_outgoing", "Output 不能有出边", [node.key]))

    cycle = _find_cycle(by_key, outgoing)
    if cycle:
        issues.append(
            WorkflowValidationIssue(
                code="cycle",
                message=f"工作流存在循环：{' -> '.join(cycle)}",
                node_keys=list(dict.fromkeys(cycle)),
                path=cycle,
            )
        )

    if len(starts) == 1:
        reachable = _reachable(starts[0].key, outgoing)
        for key in by_key:
            if key not in reachable:
                issues.append(_issue("unreachable", f"节点 {key} 无法从 Start 到达", [key]))

    for node in nodes:
        _validate_node(db, project_id, node, incoming, outgoing, issues)

    error_issues = [issue for issue in issues if issue.severity == "error"]
    if error_issues:
        return WorkflowValidationRead(valid=False, issues=issues)
    order = _topological_order(by_key, incoming, outgoing)
    plan = _build_plan(nodes, valid_edges, order)
    return WorkflowValidationRead(
        valid=True,
        issues=issues,
        plan_hash=cast(str, plan["hash"]),
        topological_order=order,
    )


def compile_graph(
    db: Session,
    project_id: int,
    nodes: list[WorkflowNodeWrite],
    edges: list[WorkflowEdgeWrite],
) -> dict[str, Any]:
    validation = validate_graph(db, project_id, nodes, edges)
    if not validation.valid:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "工作流验证失败",
                "issues": [issue.model_dump(mode="json") for issue in validation.issues],
            },
        )
    return _build_plan(nodes, edges, validation.topological_order)


def _validate_node(
    db: Session,
    project_id: int,
    node: WorkflowNodeWrite,
    incoming: dict[str, list[WorkflowEdgeWrite]],
    outgoing: dict[str, list[WorkflowEdgeWrite]],
    issues: list[WorkflowValidationIssue],
) -> None:
    if node.type not in SUPPORTED_NODE_TYPES:
        issues.append(_issue("unsupported_node", f"节点类型未实现：{node.type}", [node.key]))
        return
    config = node.config
    if node.type == "agent":
        _validate_agent_node(db, project_id, node, issues)
    elif node.type == "human_approval":
        _validate_human_approval_node(db, project_id, node, incoming, issues)
    elif node.type == "state_extraction":
        _validate_state_extraction_node(db, project_id, node, incoming, issues)
    elif node.type == "proposed_changes":
        _validate_change_node_paths(node, incoming, issues)
    elif node.type == "database_writeback":
        _validate_database_writeback_node(node, incoming, issues)
    elif node.type == "context_retrieval":
        _validate_context_config(db, project_id, node, issues, automatic=False)
    elif node.type == "input_mapping":
        mapping = config.get("mapping")
        if not isinstance(mapping, dict) or not mapping:
            issues.append(_issue("input_mapping", "Input Mapping 必须配置非空 mapping", [node.key]))
        else:
            _validate_mapping_paths(mapping, node.key, issues)
    elif node.type == "merge":
        mode = config.get("mode", "object")
        if mode not in {"object", "array", "concat"}:
            issues.append(_issue("merge_mode", f"Merge 模式不支持：{mode}", [node.key]))
        if len(incoming.get(node.key, [])) < 2:
            issues.append(
                WorkflowValidationIssue(
                    severity="warning",
                    code="merge_inputs",
                    message="Merge 少于两条入边，仍可运行但没有合并意义",
                    node_keys=[node.key],
                )
            )
    elif node.type == "condition":
        path = config.get("path")
        operator = config.get("operator", "equals")
        if not isinstance(path, str):
            issues.append(_issue("condition_path", "Condition 必须配置变量 path", [node.key]))
        else:
            _check_path(path, node.key, issues)
        if operator not in CONDITION_OPERATORS:
            issues.append(_issue("condition_operator", f"Condition 操作符不支持：{operator}", [node.key]))
        handles = {edge.source_handle for edge in outgoing.get(node.key, [])}
        if handles != {"true", "false"}:
            issues.append(
                _issue(
                    "condition_branches",
                    "Condition 必须各有一条 true 和 false 分支",
                    [node.key],
                )
            )
    elif node.type == "text_template":
        template = config.get("template")
        if not isinstance(template, str) or not template:
            issues.append(_issue("text_template", "Text Template 必须配置模板", [node.key]))
        else:
            _check_template(template, node.key, issues)
    elif node.type == "data_transform":
        operation = config.get("operation", "passthrough")
        if operation not in TRANSFORM_OPERATIONS:
            issues.append(_issue("transform_operation", f"Data Transform 操作不支持：{operation}", [node.key]))
        _validate_transform_config(config, node.key, issues)
    elif node.type == "output":
        path = config.get("path")
        if path is not None:
            if not isinstance(path, str):
                issues.append(_issue("output_path", "Output path 必须是字符串", [node.key]))
            else:
                _check_path(path, node.key, issues)


def _validate_human_approval_node(
    db: Session,
    project_id: int,
    node: WorkflowNodeWrite,
    incoming: dict[str, list[WorkflowEdgeWrite]],
    issues: list[WorkflowValidationIssue],
) -> None:
    config = node.config
    _require_incoming(node, incoming, issues)
    _reject_unknown_config(
        node,
        {
            "approval_type",
            "title",
            "instructions",
            "value_path",
            "revision_agent_id",
            "expires_in_seconds",
        },
        issues,
    )
    approval_type = config.get("approval_type", "generic")
    if approval_type not in {"prose", "change_set", "generic"}:
        issues.append(
            _issue(
                "approval_type",
                f"Human Approval 类型不支持：{approval_type}",
                [node.key],
            )
        )
    title = config.get("title", node.label)
    if not isinstance(title, str) or not title.strip() or len(title) > 240:
        issues.append(_issue("approval_title", "Human Approval 标题无效", [node.key]))
    instructions = config.get("instructions", "")
    if not isinstance(instructions, str) or len(instructions) > 20_000:
        issues.append(_issue("approval_instructions", "审批说明必须是 20,000 字以内文本", [node.key]))
    _validate_optional_path(config.get("value_path"), node.key, "approval_value_path", issues)
    expires = config.get("expires_in_seconds")
    if expires is not None and (
        not isinstance(expires, int)
        or isinstance(expires, bool)
        or not 30 <= expires <= 604_800
    ):
        issues.append(_issue("approval_expiry", "审批过期时间必须在 30 秒到 7 天之间", [node.key]))
    revision_agent_id = config.get("revision_agent_id")
    if revision_agent_id is not None:
        _validate_agent_reference(
            db, project_id, revision_agent_id, node.key, "revision_agent", issues
        )
    if approval_type == "prose" and revision_agent_id is None:
        issues.append(
            _issue(
                "approval_revision_agent",
                "正文审批必须配置修订 Agent，才能处理“要求修改”",
                [node.key],
            )
        )
    if approval_type == "change_set" and revision_agent_id is not None:
        issues.append(
            _issue(
                "approval_revision_agent",
                "ChangeSet 审批通过逐项编辑修订，不能配置正文修订 Agent",
                [node.key],
            )
        )


def _validate_state_extraction_node(
    db: Session,
    project_id: int,
    node: WorkflowNodeWrite,
    incoming: dict[str, list[WorkflowEdgeWrite]],
    issues: list[WorkflowValidationIssue],
) -> None:
    _require_incoming(node, incoming, issues)
    _reject_unknown_config(
        node,
        {
            "agent_id",
            "input_mapping",
            "automatic_context",
            "chapter_id_path",
            "scene_id_path",
            "prose_path",
            "context_policy_id",
            "context_query_template",
            "model_context_window",
            "reserved_output_tokens",
            "context_token_budget",
            "manual_model_profile_id",
        },
        issues,
    )
    _validate_agent_node(db, project_id, node, issues)
    for key in ("chapter_id_path", "scene_id_path", "prose_path"):
        _validate_optional_path(node.config.get(key), node.key, key, issues)


def _validate_change_node_paths(
    node: WorkflowNodeWrite,
    incoming: dict[str, list[WorkflowEdgeWrite]],
    issues: list[WorkflowValidationIssue],
) -> None:
    _require_incoming(node, incoming, issues)
    _reject_unknown_config(
        node,
        {"chapter_id_path", "scene_id_path", "extraction_path", "approval_path"},
        issues,
    )
    for key in ("chapter_id_path", "scene_id_path", "extraction_path", "approval_path"):
        _validate_optional_path(node.config.get(key), node.key, key, issues)


def _validate_database_writeback_node(
    node: WorkflowNodeWrite,
    incoming: dict[str, list[WorkflowEdgeWrite]],
    issues: list[WorkflowValidationIssue],
) -> None:
    _require_incoming(node, incoming, issues)
    _reject_unknown_config(
        node,
        {"change_set_path", "approval_path", "poll_seconds"},
        issues,
    )
    for key in ("change_set_path", "approval_path"):
        _validate_optional_path(node.config.get(key), node.key, key, issues)
    poll_seconds = node.config.get("poll_seconds", 0.5)
    if (
        not isinstance(poll_seconds, int | float)
        or isinstance(poll_seconds, bool)
        or not 0.1 <= float(poll_seconds) <= 5
    ):
        issues.append(_issue("writeback_poll", "写回冲突轮询必须在 0.1 到 5 秒之间", [node.key]))


def _validate_agent_reference(
    db: Session,
    project_id: int,
    value: Any,
    node_key: str,
    code: str,
    issues: list[WorkflowValidationIssue],
) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        issues.append(_issue(code, "Agent id 必须是整数", [node_key]))
        return
    agent = db.get(models.AgentDefinition, value)
    if (
        agent is None
        or agent.deleted_at is not None
        or agent.project_id != project_id
        or not agent.enabled
    ):
        issues.append(_issue(code, f"Agent #{value} 不存在、不属于项目或已停用", [node_key]))


def _validate_optional_path(
    value: Any,
    node_key: str,
    code: str,
    issues: list[WorkflowValidationIssue],
) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        issues.append(_issue(code, "变量路径必须是字符串", [node_key]))
        return
    _check_path(value, node_key, issues)


def _require_incoming(
    node: WorkflowNodeWrite,
    incoming: dict[str, list[WorkflowEdgeWrite]],
    issues: list[WorkflowValidationIssue],
) -> None:
    if not incoming.get(node.key):
        issues.append(_issue("node_input", f"节点 {node.label} 至少需要一条入边", [node.key]))


def _reject_unknown_config(
    node: WorkflowNodeWrite,
    allowed: set[str],
    issues: list[WorkflowValidationIssue],
) -> None:
    unknown = sorted(set(node.config) - allowed)
    if unknown:
        issues.append(
            _issue(
                "node_config_fields",
                f"节点 {node.label} 含未知配置字段：{', '.join(unknown)}",
                [node.key],
            )
        )


def _validate_agent_node(
    db: Session,
    project_id: int,
    node: WorkflowNodeWrite,
    issues: list[WorkflowValidationIssue],
) -> None:
    agent_id = node.config.get("agent_id")
    if not isinstance(agent_id, int) or isinstance(agent_id, bool):
        issues.append(_issue("agent_reference", "Agent 节点必须选择 AgentDefinition", [node.key]))
        return
    agent = db.get(models.AgentDefinition, agent_id)
    if agent is None or agent.deleted_at is not None or agent.project_id != project_id:
        issues.append(_issue("agent_reference", f"Agent #{agent_id} 不存在或不属于当前项目", [node.key]))
        return
    if not agent.enabled:
        issues.append(_issue("agent_disabled", f"Agent {agent.name} 已停用", [node.key]))
    try:
        validate_template(agent.system_prompt)
        validate_template(agent.prompt_template)
        Draft202012Validator.check_schema(json.loads(agent.input_schema_json))
        Draft202012Validator.check_schema(json.loads(agent.output_schema_json))
    except (SafeTemplateError, SchemaError, json.JSONDecodeError) as exc:
        issues.append(_issue("agent_config", f"Agent {agent.name} 配置无效：{exc}", [node.key]))
    if agent.model_profile_id is not None:
        profile = db.get(models.ModelProfile, agent.model_profile_id)
        if profile is None or profile.deleted_at is not None or not profile.enabled:
            issues.append(_issue("agent_model", f"Agent {agent.name} 的固定模型不可用", [node.key]))
        elif (provider := db.get(models.ProviderAccount, profile.provider_account_id)) is None or not provider.enabled:
            issues.append(_issue("agent_provider", f"Agent {agent.name} 的 Provider 不可用", [node.key]))
    elif agent.route_id is not None:
        route = db.get(models.ModelRoute, agent.route_id)
        if route is None or route.deleted_at is not None or not route.enabled:
            issues.append(_issue("agent_route", f"Agent {agent.name} 的 Route 不可用", [node.key]))
        elif route.project_id is not None and route.project_id != project_id:
            issues.append(_issue("agent_route_boundary", f"Agent {agent.name} 的 Route 属于其他项目", [node.key]))
    else:
        issues.append(_issue("agent_target", f"Agent {agent.name} 没有模型或 Route", [node.key]))
    mapping = node.config.get("input_mapping")
    if mapping is not None:
        if not isinstance(mapping, dict):
            issues.append(_issue("agent_input_mapping", "Agent input_mapping 必须是对象", [node.key]))
        else:
            _validate_mapping_paths(mapping, node.key, issues)
    if node.config.get("automatic_context", False):
        _validate_context_config(db, project_id, node, issues, automatic=True)


def _validate_context_config(
    db: Session,
    project_id: int,
    node: WorkflowNodeWrite,
    issues: list[WorkflowValidationIssue],
    *,
    automatic: bool,
) -> None:
    config = node.config
    if automatic and not isinstance(config.get("automatic_context"), bool):
        issues.append(_issue("context_automatic", "automatic_context 必须是布尔值", [node.key]))
    policy_id = config.get("context_policy_id" if automatic else "policy_id")
    if policy_id is not None:
        if not isinstance(policy_id, int) or isinstance(policy_id, bool):
            issues.append(_issue("context_policy", "ContextPolicy id 必须是整数", [node.key]))
        else:
            policy = db.get(models.ContextPolicy, policy_id)
            if (
                policy is None
                or policy.deleted_at is not None
                or policy.project_id != project_id
                or not policy.enabled
            ):
                issues.append(
                    _issue(
                        "context_policy",
                        f"ContextPolicy #{policy_id} 不存在、不属于当前项目或已停用",
                        [node.key],
                    )
                )
    if not automatic:
        agent_id = config.get("agent_id")
        if agent_id is not None:
            if not isinstance(agent_id, int) or isinstance(agent_id, bool):
                issues.append(_issue("context_agent", "Context Agent id 必须是整数", [node.key]))
            else:
                agent = db.get(models.AgentDefinition, agent_id)
                if (
                    agent is None
                    or agent.deleted_at is not None
                    or agent.project_id != project_id
                    or not agent.enabled
                ):
                    issues.append(
                        _issue("context_agent", f"Agent #{agent_id} 不可用于上下文检索", [node.key])
                    )
        model_id = config.get("model_profile_id")
        if model_id is not None and (
            not isinstance(model_id, int) or isinstance(model_id, bool)
        ):
            issues.append(_issue("context_model", "Context ModelProfile id 必须是整数", [node.key]))
        elif isinstance(model_id, int):
            profile = db.get(models.ModelProfile, model_id)
            if profile is None or profile.deleted_at is not None or not profile.enabled:
                issues.append(
                    _issue("context_model", f"ModelProfile #{model_id} 不可用", [node.key])
                )
    for key in ("chapter_id_path", "scene_id_path"):
        path = config.get(key)
        if path is not None:
            if not isinstance(path, str):
                issues.append(_issue("context_path", f"{key} 必须是字符串", [node.key]))
            else:
                _check_path(path, node.key, issues)
    template_key = "context_query_template" if automatic else "query_template"
    template = config.get(template_key)
    if template is not None:
        if not isinstance(template, str):
            issues.append(_issue("context_template", f"{template_key} 必须是字符串", [node.key]))
        elif template:
            _check_template(template, node.key, issues)
    token_budget = config.get("context_token_budget" if automatic else "token_budget")
    if token_budget is not None and (
        not isinstance(token_budget, int)
        or isinstance(token_budget, bool)
        or not 128 <= token_budget <= 2_000_000
    ):
        issues.append(_issue("context_budget", "上下文 Token 预算必须在 128 到 2000000 之间", [node.key]))
    reserved = config.get("reserved_output_tokens")
    if reserved is not None and (
        not isinstance(reserved, int)
        or isinstance(reserved, bool)
        or not 0 <= reserved <= 1_000_000
    ):
        issues.append(_issue("context_reserved", "预留输出 Token 必须在 0 到 1000000 之间", [node.key]))
    model_window = config.get("model_context_window")
    if model_window is not None and (
        not isinstance(model_window, int)
        or isinstance(model_window, bool)
        or not 128 <= model_window <= 2_000_000
    ):
        issues.append(_issue("context_window", "模型上下文窗口必须在 128 到 2000000 之间", [node.key]))


def _validate_mapping_paths(
    mapping: dict[Any, Any], node_key: str, issues: list[WorkflowValidationIssue]
) -> None:
    for key, path in mapping.items():
        if not isinstance(key, str) or not key or not key.replace("_", "").isalnum():
            issues.append(_issue("mapping_key", f"节点 {node_key} 的映射键无效：{key}", [node_key]))
        if not isinstance(path, str):
            issues.append(_issue("mapping_path", f"节点 {node_key} 的映射路径必须是字符串", [node_key]))
        else:
            _check_path(path, node_key, issues)


def _validate_transform_config(
    config: dict[str, Any], node_key: str, issues: list[WorkflowValidationIssue]
) -> None:
    operation = config.get("operation", "passthrough")
    if operation in {"passthrough", "to_json", "from_json"}:
        path = config.get("path", "upstream")
        if not isinstance(path, str):
            issues.append(_issue("transform_path", "Transform path 必须是字符串", [node_key]))
        else:
            _check_path(path, node_key, issues)
    elif operation == "pick":
        paths = config.get("paths")
        if not isinstance(paths, list) or not paths:
            issues.append(_issue("transform_paths", "pick 必须配置非空 paths", [node_key]))
        else:
            for path in paths:
                if not isinstance(path, str):
                    issues.append(_issue("transform_paths", "pick path 必须是字符串", [node_key]))
                else:
                    _check_path(path, node_key, issues)
    elif operation == "rename":
        mapping = config.get("mapping")
        if not isinstance(mapping, dict) or not mapping:
            issues.append(_issue("transform_mapping", "rename 必须配置非空 mapping", [node_key]))
        else:
            _validate_mapping_paths(mapping, node_key, issues)


def _check_path(path: str, node_key: str, issues: list[WorkflowValidationIssue]) -> None:
    try:
        validate_path(path)
    except SafeTemplateError as exc:
        issues.append(_issue("unsafe_path", str(exc), [node_key]))


def _check_template(template: str, node_key: str, issues: list[WorkflowValidationIssue]) -> None:
    try:
        validate_template(template)
    except SafeTemplateError as exc:
        issues.append(_issue("unsafe_template", str(exc), [node_key]))


def _find_cycle(
    nodes: dict[str, WorkflowNodeWrite],
    outgoing: dict[str, list[WorkflowEdgeWrite]],
) -> list[str]:
    state: dict[str, int] = {key: 0 for key in nodes}
    stack: list[str] = []

    def visit(key: str) -> list[str]:
        state[key] = 1
        stack.append(key)
        for edge in outgoing.get(key, []):
            target = edge.target
            if state[target] == 0:
                found = visit(target)
                if found:
                    return found
            elif state[target] == 1:
                index = stack.index(target)
                return [*stack[index:], target]
        stack.pop()
        state[key] = 2
        return []

    for key in nodes:
        if state[key] == 0:
            found = visit(key)
            if found:
                return found
    return []


def _reachable(start: str, outgoing: dict[str, list[WorkflowEdgeWrite]]) -> set[str]:
    seen = {start}
    queue = deque([start])
    while queue:
        source = queue.popleft()
        for edge in outgoing.get(source, []):
            if edge.target not in seen:
                seen.add(edge.target)
                queue.append(edge.target)
    return seen


def _topological_order(
    nodes: dict[str, WorkflowNodeWrite],
    incoming: dict[str, list[WorkflowEdgeWrite]],
    outgoing: dict[str, list[WorkflowEdgeWrite]],
) -> list[str]:
    degrees = {key: len(incoming.get(key, [])) for key in nodes}
    ready = deque(sorted(key for key, degree in degrees.items() if degree == 0))
    order: list[str] = []
    while ready:
        key = ready.popleft()
        order.append(key)
        for edge in outgoing.get(key, []):
            degrees[edge.target] -= 1
            if degrees[edge.target] == 0:
                ready.append(edge.target)
    return order


def _build_plan(
    nodes: list[WorkflowNodeWrite],
    edges: list[WorkflowEdgeWrite],
    order: list[str],
) -> dict[str, Any]:
    node_map = {node.key: node.model_dump(mode="json") for node in nodes}
    edge_values = [edge.model_dump(mode="json") for edge in edges]
    incoming: dict[str, list[str]] = {node.key: [] for node in nodes}
    outgoing: dict[str, list[str]] = {node.key: [] for node in nodes}
    descendants: dict[str, list[str]] = {node.key: [] for node in nodes}
    edge_map = {edge.key: edge.model_dump(mode="json") for edge in edges}
    for edge in edges:
        incoming[edge.target].append(edge.key)
        outgoing[edge.source].append(edge.key)
    for key in reversed(order):
        seen: set[str] = set()
        for edge_key in outgoing[key]:
            target = cast(str, edge_map[edge_key]["target"])
            seen.add(target)
            seen.update(descendants[target])
        descendants[key] = [item for item in order if item in seen]
    base: dict[str, Any] = {
        "version": 1,
        "topological_order": order,
        "nodes": node_map,
        "edges": edge_values,
        "incoming": incoming,
        "outgoing": outgoing,
        "descendants": descendants,
    }
    base["hash"] = hashlib.sha256(_dump(base).encode("utf-8")).hexdigest()
    return base


def _issue(code: str, message: str, node_keys: list[str] | None = None) -> WorkflowValidationIssue:
    return WorkflowValidationIssue(code=code, message=message, node_keys=node_keys or [])


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
