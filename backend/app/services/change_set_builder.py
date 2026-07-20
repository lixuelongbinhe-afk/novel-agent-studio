from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.schemas.approvals import (
    EntityExtraction,
    ForeshadowExtraction,
    ProposedChangeItem,
    RelationshipExtraction,
    SceneStateExtraction,
    SceneSummaryExtraction,
    StateExtractionResult,
    TimelineExtraction,
)
from app.services.entity_resolution import (
    EntityResolution,
    EntityResolver,
    json_string_list,
    normalize_entity_name,
)
from app.services.usage_control import estimate_text_tokens


ENTITY_GROUPS = (
    ("character", "characters"),
    ("location", "locations"),
    ("item", "items"),
    ("organization", "organizations"),
)


@dataclass(frozen=True)
class ChangeSetBuild:
    items: list[ProposedChangeItem]
    conflicts: list[str]
    base_revisions: dict[str, int]


class ChangeSetBuilder:
    def __init__(
        self,
        db: Session,
        *,
        project_id: int,
        chapter: models.Chapter | None,
        scene: models.Scene | None,
    ) -> None:
        self.db = db
        self.project_id = project_id
        self.chapter = chapter
        self.scene = scene
        self.resolver = EntityResolver(
            db,
            project_id,
            chapter_id=chapter.id if chapter is not None else None,
        )
        self.items: list[ProposedChangeItem] = []
        self.conflicts: list[str] = []
        self.base_revisions: dict[str, int] = {}
        self.sequence = 0
        self.new_entity_refs: dict[tuple[str, str], list[str]] = {}

    def build(
        self,
        extraction: StateExtractionResult,
        *,
        approved_prose: str | None,
    ) -> ChangeSetBuild:
        self.conflicts.extend(
            f"提取冲突 [{issue.code}] {issue.message}" for issue in extraction.conflicts
        )
        self.conflicts.extend(
            f"连续性警告 [{issue.code}] {issue.message}"
            for issue in extraction.continuity_warnings
        )
        if approved_prose is not None:
            self._chapter_content(approved_prose)
        self._chapter_summary(extraction)
        for kind, field_name in ENTITY_GROUPS:
            for entity in cast(list[EntityExtraction], getattr(extraction, field_name)):
                self._entity(entity, kind)
        for summary in extraction.scene_summaries:
            self._scene_summary(summary)
        for state in extraction.scene_states:
            self._scene_state(state)
        for relation in extraction.relationships:
            self._relationship(relation)
        for event in extraction.timeline_events:
            self._timeline(event)
        for foreshadow in extraction.foreshadows:
            self._foreshadow(foreshadow)
        return ChangeSetBuild(
            items=self.items,
            conflicts=list(dict.fromkeys(self.conflicts)),
            base_revisions=self.base_revisions,
        )

    def _chapter_content(self, prose: str) -> None:
        if self.chapter is None:
            self.conflicts.append("正文变更缺少目标章节")
            return
        self._base("chapter", self.chapter)
        self._add(
            kind="chapter_content",
            operation="update",
            target_id=self.chapter.id,
            target_label=self.chapter.title,
            base_revision=self.chapter.revision,
            before={"content": self.chapter.content, "word_count": self.chapter.word_count},
            proposed={"content": prose},
            evidence=["已通过正文审批的不可变快照"],
            confidence=1.0,
        )

    def _chapter_summary(self, extraction: StateExtractionResult) -> None:
        if self.chapter is None:
            self.conflicts.append("章节摘要缺少目标章节")
            return
        summary = self.db.scalar(
            select(models.ChapterSummary).where(
                models.ChapterSummary.chapter_id == self.chapter.id,
                models.ChapterSummary.deleted_at.is_(None),
            )
        )
        before: dict[str, Any] = {}
        if summary is not None:
            self._base("chapter_summary", summary)
            before = {
                "summary": summary.summary,
                "key_events": _json_list(summary.key_events_json),
                "entity_ids": _json_int_list(summary.entity_ids_json),
                "token_count": summary.token_count,
                "source": summary.source,
            }
        value = extraction.chapter_summary
        self._add(
            kind="chapter_summary",
            operation="update" if summary is not None else "upsert",
            target_id=summary.id if summary is not None else None,
            target_label=f"{self.chapter.title} · 章节摘要",
            base_revision=summary.revision if summary is not None else None,
            before=before,
            proposed={
                "chapter_id": self.chapter.id,
                "summary": value.summary,
                "key_events": value.key_events,
                "entity_ids": [],
                "token_count": estimate_text_tokens(value.summary),
                "source": "workflow_writeback",
            },
            evidence=value.evidence,
            confidence=value.confidence,
        )

    def _entity(self, value: EntityExtraction, kind: str) -> None:
        resolution = self.resolver.resolve(
            name=value.name,
            kind=kind,
            entity_id=value.entity_id,
            manual_link_type=value.manual_link_type,
        )
        intrinsic = _resolution_conflicts(value.name, resolution)
        target = (
            self.resolver.by_id.get(resolution.target_id)
            if resolution.target_id is not None
            else None
        )
        before: dict[str, Any] = {}
        if target is not None:
            self._base("entity", target)
            before = {
                "name": target.name,
                "kind": target.kind,
                "description": target.description,
                "tags": json_string_list(target.tags),
            }
        operation = "update" if target is not None else "create"
        proposed = {
            "name": value.name,
            "kind": kind,
            "description": (
                value.description
                if value.description is not None
                else (target.description if target is not None else "")
            ),
            "tags": value.tags or (json_string_list(target.tags) if target is not None else []),
        }
        entity_item = self._add(
            kind="entity",
            operation=operation,
            target_id=target.id if target is not None else None,
            target_label=value.name,
            base_revision=target.revision if target is not None else None,
            before=before,
            proposed=proposed,
            evidence=value.evidence,
            confidence=value.confidence,
            resolution=resolution.to_dict(),
            conflicts=intrinsic,
            decision="later" if intrinsic else "accept",
        )
        if target is None and resolution.status == "unmatched":
            key = (kind, normalize_entity_name(value.name))
            self.new_entity_refs.setdefault(key, []).append(entity_item.id)

        existing_aliases = {
            normalize_entity_name(alias)
            for alias in self.resolver.aliases_by_entity.get(target.id, [])
        } if target is not None else set()
        for alias in value.aliases:
            if target is not None and normalize_entity_name(alias) in existing_aliases:
                continue
            alias_conflicts = list(intrinsic)
            self._add(
                kind="entity_alias",
                operation="create",
                target_id=None,
                target_label=f"{value.name} · 别名 {alias}",
                base_revision=None,
                before={},
                proposed={
                    "entity_id": target.id if target is not None else None,
                    "entity_ref": entity_item.id if target is None else None,
                    "alias": alias,
                },
                evidence=value.evidence,
                confidence=value.confidence,
                resolution={"parent_entity_item_id": entity_item.id},
                conflicts=alias_conflicts,
                decision="later" if alias_conflicts else "accept",
            )
        for state in value.state_updates:
            self._add(
                kind="entity_state_change",
                operation="create",
                target_id=None,
                target_label=f"{value.name} · {state.field_name}",
                base_revision=None,
                before={},
                proposed={
                    "entity_id": target.id if target is not None else None,
                    "entity_ref": entity_item.id if target is None else None,
                    "chapter_id": self.chapter.id if self.chapter is not None else None,
                    "field_name": state.field_name,
                    "old_value": state.old_value or "",
                    "new_value": state.new_value,
                    "reason": state.reason,
                },
                evidence=value.evidence,
                confidence=value.confidence,
                resolution={"parent_entity_item_id": entity_item.id},
                conflicts=list(intrinsic),
                decision="later" if intrinsic else "accept",
            )

    def _scene_summary(self, value: SceneSummaryExtraction) -> None:
        scene, conflicts = self._resolve_scene(value.scene_id, value.scene_title)
        before = {"synopsis": scene.synopsis} if scene is not None else {}
        if scene is not None:
            self._base("scene", scene)
        self._add(
            kind="scene_synopsis",
            operation="update" if scene is not None else "upsert",
            target_id=scene.id if scene is not None else None,
            target_label=value.scene_title or (scene.title if scene is not None else "未解析场景"),
            base_revision=scene.revision if scene is not None else None,
            before=before,
            proposed={"synopsis": value.summary},
            evidence=value.evidence,
            confidence=value.confidence,
            conflicts=conflicts,
            decision="later" if conflicts else "accept",
        )

    def _scene_state(self, value: SceneStateExtraction) -> None:
        scene, conflicts = self._resolve_scene(value.scene_id, value.scene_title)
        existing = None
        if scene is not None:
            existing = self.db.scalar(
                select(models.SceneState).where(
                    models.SceneState.scene_id == scene.id,
                    models.SceneState.deleted_at.is_(None),
                )
            )
        viewpoint_id, viewpoint_ref, viewpoint_resolution, viewpoint_conflicts = (
            self._entity_reference(value.viewpoint_entity_id, value.viewpoint_name, "character")
        )
        location_id, location_ref, location_resolution, location_conflicts = (
            self._entity_reference(value.location_entity_id, value.location_name, "location")
        )
        item_ids: list[int] = []
        item_refs: list[str] = []
        item_resolutions: list[dict[str, object]] = []
        for index, item_name in enumerate(value.item_names):
            item_id = value.item_entity_ids[index] if index < len(value.item_entity_ids) else None
            resolved_id, resolved_ref, resolution, item_conflicts = self._entity_reference(
                item_id, item_name, "item"
            )
            if resolved_id is not None:
                item_ids.append(resolved_id)
            if resolved_ref is not None:
                item_refs.append(resolved_ref)
            item_resolutions.append(resolution)
            conflicts.extend(item_conflicts)
        for item_id in value.item_entity_ids[len(value.item_names) :]:
            resolved_id, resolved_ref, resolution, item_conflicts = self._entity_reference(
                item_id, "", "item"
            )
            if resolved_id is not None:
                item_ids.append(resolved_id)
            if resolved_ref is not None:
                item_refs.append(resolved_ref)
            item_resolutions.append(resolution)
            conflicts.extend(item_conflicts)
        conflicts.extend(viewpoint_conflicts)
        conflicts.extend(location_conflicts)
        state = {
            update.field_name: update.new_value for update in value.state_updates
        }
        before: dict[str, Any] = {}
        if existing is not None:
            self._base("scene_state", existing)
            before = {
                "scene_id": existing.scene_id,
                "viewpoint_entity_id": existing.viewpoint_entity_id,
                "location_entity_id": existing.location_entity_id,
                "item_entity_ids": _json_int_list(existing.item_entity_ids_json),
                "state": _json_object(existing.state_json),
                "notes": existing.notes,
            }
        self._add(
            kind="scene_state",
            operation="update" if existing is not None else "upsert",
            target_id=existing.id if existing is not None else None,
            target_label=f"{value.scene_title or (scene.title if scene else '未解析场景')} · 场景状态",
            base_revision=existing.revision if existing is not None else None,
            before=before,
            proposed={
                "scene_id": scene.id if scene is not None else None,
                "viewpoint_entity_id": viewpoint_id,
                "viewpoint_entity_ref": viewpoint_ref,
                "location_entity_id": location_id,
                "location_entity_ref": location_ref,
                "item_entity_ids": item_ids,
                "item_entity_refs": item_refs,
                "state": state,
                "notes": value.notes,
            },
            evidence=value.evidence,
            confidence=value.confidence,
            resolution={
                "viewpoint": viewpoint_resolution,
                "location": location_resolution,
                "items": item_resolutions,
            },
            conflicts=list(dict.fromkeys(conflicts)),
            decision="later" if conflicts else "accept",
        )

    def _relationship(self, value: RelationshipExtraction) -> None:
        source_id, source_ref, source_resolution, source_conflicts = self._entity_reference(
            value.source_entity_id, value.source_name, "character", allow_any_kind=True
        )
        target_id, target_ref, target_resolution, target_conflicts = self._entity_reference(
            value.target_entity_id, value.target_name, "character", allow_any_kind=True
        )
        conflicts = [*source_conflicts, *target_conflicts]
        existing = None
        if value.relation_id is not None:
            candidate = self.db.get(models.EntityRelation, value.relation_id)
            if candidate is None or candidate.deleted_at is not None or candidate.project_id != self.project_id:
                conflicts.append("关系 ID 不存在或不属于当前项目")
            else:
                existing = candidate
        elif source_id is not None and target_id is not None:
            existing = self.db.scalar(
                select(models.EntityRelation).where(
                    models.EntityRelation.project_id == self.project_id,
                    models.EntityRelation.source_entity_id == source_id,
                    models.EntityRelation.target_entity_id == target_id,
                    models.EntityRelation.relation_type == value.relation_type,
                    models.EntityRelation.deleted_at.is_(None),
                )
            )
        if existing is not None:
            self._base("entity_relation", existing)
        self._add(
            kind="entity_relation",
            operation="update" if existing is not None else "create",
            target_id=existing.id if existing is not None else None,
            target_label=f"{value.source_name} → {value.target_name} · {value.relation_type}",
            base_revision=existing.revision if existing is not None else None,
            before=(
                {
                    "source_entity_id": existing.source_entity_id,
                    "target_entity_id": existing.target_entity_id,
                    "relation_type": existing.relation_type,
                    "notes": existing.notes,
                }
                if existing is not None
                else {}
            ),
            proposed={
                "project_id": self.project_id,
                "source_entity_id": source_id,
                "source_entity_ref": source_ref,
                "target_entity_id": target_id,
                "target_entity_ref": target_ref,
                "relation_type": value.relation_type,
                "notes": value.notes,
            },
            evidence=value.evidence,
            confidence=value.confidence,
            resolution={"source": source_resolution, "target": target_resolution},
            conflicts=list(dict.fromkeys(conflicts)),
            decision="later" if conflicts else "accept",
        )

    def _timeline(self, value: TimelineExtraction) -> None:
        existing = None
        conflicts: list[str] = []
        if value.timeline_event_id is not None:
            candidate = self.db.get(models.TimelineEvent, value.timeline_event_id)
            if candidate is None or candidate.deleted_at is not None or candidate.project_id != self.project_id:
                conflicts.append("时间线事件 ID 不存在或不属于当前项目")
            else:
                existing = candidate
        if existing is not None:
            self._base("timeline_event", existing)
        self._add(
            kind="timeline_event",
            operation="update" if existing is not None else "create",
            target_id=existing.id if existing is not None else None,
            target_label=value.label,
            base_revision=existing.revision if existing is not None else None,
            before=(
                {
                    "label": existing.label,
                    "event_time": existing.event_time,
                    "description": existing.description,
                    "chapter_id": existing.chapter_id,
                    "position": existing.position,
                }
                if existing is not None
                else {}
            ),
            proposed={
                "project_id": self.project_id,
                "chapter_id": self.chapter.id if self.chapter is not None else None,
                "label": value.label,
                "event_time": value.event_time,
                "description": value.description,
                "position": existing.position if existing is not None else 0,
            },
            evidence=value.evidence,
            confidence=value.confidence,
            conflicts=conflicts,
            decision="later" if conflicts else "accept",
        )

    def _foreshadow(self, value: ForeshadowExtraction) -> None:
        existing = None
        conflicts: list[str] = []
        if value.action != "new":
            candidates: list[models.Foreshadow] = []
            if value.foreshadow_id is not None:
                candidate = self.db.get(models.Foreshadow, value.foreshadow_id)
                if candidate is not None and candidate.deleted_at is None and candidate.project_id == self.project_id:
                    candidates = [candidate]
            else:
                candidates = list(
                    self.db.scalars(
                        select(models.Foreshadow).where(
                            models.Foreshadow.project_id == self.project_id,
                            models.Foreshadow.setup_text == value.setup_text,
                            models.Foreshadow.deleted_at.is_(None),
                        )
                    ).all()
                )
            if len(candidates) == 1:
                existing = candidates[0]
            elif not candidates:
                conflicts.append("要推进或回收的伏笔未找到")
            else:
                conflicts.append("多个伏笔匹配，必须人工选择")
        if existing is not None:
            self._base("foreshadow", existing)
        self._add(
            kind="foreshadow",
            operation="update" if existing is not None else "create",
            target_id=existing.id if existing is not None else None,
            target_label=value.setup_text[:120],
            base_revision=existing.revision if existing is not None else None,
            before=(
                {
                    "setup_text": existing.setup_text,
                    "payoff_text": existing.payoff_text,
                    "status": existing.status,
                    "chapter_id": existing.chapter_id,
                }
                if existing is not None
                else {}
            ),
            proposed={
                "project_id": self.project_id,
                "setup_text": value.setup_text,
                "payoff_text": value.payoff_text,
                "status": "resolved" if value.action == "resolve" else "open",
                "chapter_id": self.chapter.id if self.chapter is not None else None,
            },
            evidence=value.evidence,
            confidence=value.confidence,
            resolution={"action": value.action},
            conflicts=conflicts,
            decision="later" if conflicts else "accept",
        )

    def _resolve_scene(
        self, scene_id: int | None, title: str
    ) -> tuple[models.Scene | None, list[str]]:
        if self.chapter is None:
            return None, ["场景变更缺少目标章节"]
        if scene_id is not None:
            scene = self.db.get(models.Scene, scene_id)
            if scene is None or scene.deleted_at is not None or scene.chapter_id != self.chapter.id:
                return None, ["场景 ID 不存在或不属于目标章节"]
            return scene, []
        normalized_title = normalize_entity_name(title)
        matches = list(
            self.db.scalars(
                select(models.Scene).where(
                    models.Scene.chapter_id == self.chapter.id,
                    models.Scene.deleted_at.is_(None),
                )
            ).all()
        )
        matches = [
            scene
            for scene in matches
            if normalize_entity_name(scene.title) == normalized_title
        ]
        if len(matches) == 1:
            return matches[0], []
        if len(matches) > 1:
            return None, ["多个场景标题精确匹配，必须人工选择"]
        return None, ["场景未找到，当前版本不会自动创建场景"]

    def _entity_reference(
        self,
        entity_id: int | None,
        name: str,
        kind: str,
        *,
        allow_any_kind: bool = False,
    ) -> tuple[int | None, str | None, dict[str, object], list[str]]:
        if entity_id is None and not name.strip():
            return None, None, {"status": "empty", "method": "none"}, []
        if entity_id is None and name:
            refs: list[str] = []
            if allow_any_kind:
                normalized = normalize_entity_name(name)
                for (candidate_kind, candidate_name), candidate_refs in self.new_entity_refs.items():
                    if candidate_name == normalized:
                        refs.extend(candidate_refs)
            else:
                refs = self.new_entity_refs.get((kind, normalize_entity_name(name)), [])
            if len(refs) == 1:
                return (
                    None,
                    refs[0],
                    {"status": "resolved", "method": "new_entity_ref", "item_id": refs[0]},
                    [],
                )
            if len(refs) > 1:
                return (
                    None,
                    None,
                    {"status": "ambiguous", "method": "new_entity_ref", "item_ids": refs},
                    [f"实体引用“{name}”对应多个新实体候选"],
                )
        if allow_any_kind:
            resolutions = [
                self.resolver.resolve(name=name, kind=candidate_kind, entity_id=entity_id)
                for candidate_kind, _ in ENTITY_GROUPS
            ]
            resolved = [item for item in resolutions if item.status == "resolved"]
            if len(resolved) == 1:
                resolution = resolved[0]
            elif len(resolved) > 1:
                resolution = EntityResolution(
                    status="ambiguous",
                    method="cross_kind",
                    target_id=None,
                    target_name=None,
                    candidates=tuple(
                        candidate
                        for item in resolved
                        for candidate in item.candidates
                    ),
                    message="名称在多个实体类型中匹配",
                )
            else:
                resolution = next(
                    (
                        item
                        for item in resolutions
                        if item.status in {"ambiguous", "invalid"}
                    ),
                    resolutions[0],
                )
        else:
            resolution = self.resolver.resolve(name=name, kind=kind, entity_id=entity_id)
        conflicts = _resolution_conflicts(name or str(entity_id), resolution)
        if resolution.status == "unmatched":
            conflicts.append(f"实体引用“{name or entity_id}”未找到")
        return (
            resolution.target_id,
            None,
            resolution.to_dict(),
            conflicts,
        )

    def _base(self, prefix: str, row: Any) -> None:
        self.base_revisions[f"{prefix}:{row.id}"] = int(row.revision)

    def _add(
        self,
        *,
        kind: str,
        operation: str,
        target_id: int | None,
        target_label: str,
        base_revision: int | None,
        before: dict[str, Any],
        proposed: dict[str, Any],
        evidence: list[str],
        confidence: float,
        resolution: dict[str, Any] | None = None,
        conflicts: list[str] | None = None,
        decision: str = "accept",
    ) -> ProposedChangeItem:
        self.sequence += 1
        item = ProposedChangeItem.model_validate(
            {
                "id": f"change-{self.sequence:04d}",
                "kind": kind,
                "operation": operation,
                "target_id": target_id,
                "target_label": target_label or kind,
                "base_revision": base_revision,
                "before": before,
                "proposed": proposed,
                "evidence": evidence,
                "confidence": confidence,
                "resolution": resolution or {},
                "conflicts": conflicts or [],
                "decision": decision,
            }
        )
        self.items.append(item)
        return item


def _resolution_conflicts(name: str, resolution: EntityResolution) -> list[str]:
    if resolution.status == "resolved" or resolution.status == "unmatched":
        return []
    return [f"实体“{name}”：{resolution.message}"]


def _json_list(value: str) -> list[Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_int_list(value: str) -> list[int]:
    return [item for item in _json_list(value) if isinstance(item, int)]


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
