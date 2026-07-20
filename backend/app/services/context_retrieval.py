from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import models
from app.core.text import extract_visible_text
from app.services.usage_control import estimate_text_tokens


_TERM_RE = re.compile(r"[A-Za-z0-9_]{2,40}|[\u3400-\u9fff]{2,40}")


@dataclass(frozen=True)
class RetrievalQuery:
    project_id: int
    chapter_id: int | None
    scene_id: int | None
    agent_type: str
    query_text: str
    workflow_input: dict[str, Any]
    upstream_outputs: dict[str, Any]
    recent_chapter_count: int
    max_results: int

    @property
    def search_text(self) -> str:
        return "\n".join(
            value
            for value in (
                self.query_text,
                _compact_json(self.workflow_input),
                _compact_json(self.upstream_outputs),
            )
            if value
        )[:500_000]


@dataclass
class RetrievalCandidate:
    source_type: str
    source_id: int
    section: str
    title: str
    content: str
    relevance: float
    reasons: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    pinned: bool = False
    pin_priority: int | None = None
    required: bool = False

    @property
    def key(self) -> str:
        return f"{self.source_type}:{self.source_id}:{self.section}"

    @property
    def token_estimate(self) -> int:
        return estimate_text_tokens(self.content)


class Retriever(Protocol):
    name: str

    def retrieve(self, db: Session, query: RetrievalQuery) -> list[RetrievalCandidate]: ...


class EmbeddingRetriever(Protocol):
    """Extension point for optional local embeddings; no vector service is required."""

    name: str

    def retrieve(self, db: Session, query: RetrievalQuery) -> list[RetrievalCandidate]: ...


class DisabledEmbeddingRetriever:
    name = "embedding-disabled"

    def retrieve(self, db: Session, query: RetrievalQuery) -> list[RetrievalCandidate]:
        del db, query
        return []


class CurrentSceneRetriever:
    name = "current-scene"

    def retrieve(self, db: Session, query: RetrievalQuery) -> list[RetrievalCandidate]:
        if query.scene_id is not None:
            scene = db.get(models.Scene, query.scene_id)
            if scene is None or scene.deleted_at is not None:
                return []
            parts = [f"场景：{scene.title}"]
            synopsis = extract_visible_text(scene.synopsis)
            content = extract_visible_text(scene.content)
            if synopsis:
                parts.append(f"梗概：{synopsis}")
            if content:
                parts.append(f"正文：\n{content}")
            return [
                RetrievalCandidate(
                    source_type="scene",
                    source_id=scene.id,
                    section="current_scene",
                    title=f"当前场景 · {scene.title}",
                    content="\n".join(parts),
                    relevance=1.0,
                    reasons=["用户选择的当前场景"],
                    required=True,
                    metadata={"chapter_id": scene.chapter_id, "revision": scene.revision},
                )
            ]
        if query.chapter_id is None:
            return []
        chapter = db.get(models.Chapter, query.chapter_id)
        if chapter is None or chapter.deleted_at is not None:
            return []
        chapter_text = extract_visible_text(chapter.content)
        content = chapter_text[:12_000]
        return [
            RetrievalCandidate(
                source_type="chapter",
                source_id=chapter.id,
                section="current_scene",
                title=f"当前章节 · {chapter.title}",
                content=f"章节：{chapter.title}\n当前正文片段：\n{content}",
                relevance=0.97,
                reasons=["未指定场景，使用当前章节的有限片段"],
                required=True,
                metadata={
                    "revision": chapter.revision,
                    "capped_characters": len(chapter_text) > len(content),
                },
            )
        ]


class EntityAliasRetriever:
    name = "entity-alias"

    def retrieve(self, db: Session, query: RetrievalQuery) -> list[RetrievalCandidate]:
        entities = db.scalars(
            select(models.StoryEntity)
            .where(
                models.StoryEntity.project_id == query.project_id,
                models.StoryEntity.deleted_at.is_(None),
            )
            .order_by(models.StoryEntity.id)
        ).all()
        if not entities:
            return []
        entity_ids = [row.id for row in entities]
        aliases = db.scalars(
            select(models.EntityAlias).where(
                models.EntityAlias.entity_id.in_(entity_ids),
                models.EntityAlias.deleted_at.is_(None),
            )
        ).all()
        aliases_by_entity: dict[int, list[str]] = {}
        for row in aliases:
            aliases_by_entity.setdefault(row.entity_id, []).append(row.alias)
        scene_state = _scene_state(db, query.scene_id)
        state_ids = _state_entity_ids(scene_state)
        manual_ids = set(
            db.scalars(
                select(models.ChapterEntityLink.entity_id).where(
                    models.ChapterEntityLink.chapter_id == query.chapter_id,
                    models.ChapterEntityLink.deleted_at.is_(None),
                )
            ).all()
            if query.chapter_id is not None
            else []
        )
        summary_ids = set(_summary_entity_ids(db, query.chapter_id))
        search_lower = query.search_text.casefold()
        matched: list[tuple[models.StoryEntity, list[str], list[str], float, list[str]]] = []
        matched_ids: set[int] = set()
        for entity in entities:
            names = [entity.name, *aliases_by_entity.get(entity.id, [])]
            tags = _json_string_list(entity.tags)
            reasons: list[str] = []
            score = 0.0
            exact_matches = [name for name in names if name.casefold() in search_lower]
            tag_matches = [tag for tag in tags if tag.casefold() in search_lower]
            if exact_matches:
                score = max(score, 0.92)
                reasons.append(f"任务或上游输出命中名称/别名：{'、'.join(exact_matches[:4])}")
            if tag_matches:
                score = max(score, 0.72)
                reasons.append(f"命中实体标签：{'、'.join(tag_matches[:4])}")
            if entity.id in state_ids:
                score = max(score, 0.98)
                role = state_ids[entity.id]
                reasons.append(f"当前场景状态指定为{role}")
            if entity.id in manual_ids:
                score = max(score, 0.95)
                reasons.append("当前章节存在人工实体链接")
            if entity.id in summary_ids:
                score = max(score, 0.74)
                reasons.append("当前章节摘要标记了该实体")
            if query.agent_type in {"character", "continuity", "editor"} and (
                entity.kind == "character" or "主角" in tags
            ):
                score = max(score, 0.58)
                reasons.append(f"{query.agent_type} Agent 提升人物资料优先级")
            if score == 0:
                continue
            matched_ids.add(entity.id)
            matched.append(
                (
                    entity,
                    aliases_by_entity.get(entity.id, []),
                    tags,
                    score,
                    reasons,
                )
            )
        changes_by_entity = _recent_entity_changes(db, matched_ids)
        candidates = [
            _entity_candidate(
                db,
                entity,
                aliases,
                tags,
                score,
                reasons,
                query.chapter_id,
                changes=changes_by_entity.get(entity.id, []),
            )
            for entity, aliases, tags, score, reasons in matched
        ]
        candidates.extend(_relation_candidates(db, query.project_id, matched_ids))
        return candidates


class ManualLinkRetriever:
    name = "manual-link"

    def retrieve(self, db: Session, query: RetrievalQuery) -> list[RetrievalCandidate]:
        if query.chapter_id is None:
            return []
        links = db.scalars(
            select(models.ChapterEntityLink)
            .where(
                models.ChapterEntityLink.chapter_id == query.chapter_id,
                models.ChapterEntityLink.deleted_at.is_(None),
            )
            .order_by(models.ChapterEntityLink.relevance.desc(), models.ChapterEntityLink.id)
        ).all()
        result: list[RetrievalCandidate] = []
        for link in links:
            entity = db.get(models.StoryEntity, link.entity_id)
            if entity is None or entity.deleted_at is not None:
                continue
            aliases = db.scalars(
                select(models.EntityAlias.alias).where(
                    models.EntityAlias.entity_id == entity.id,
                    models.EntityAlias.deleted_at.is_(None),
                )
            ).all()
            candidate = _entity_candidate(
                db,
                entity,
                list(aliases),
                _json_string_list(entity.tags),
                max(0.7, link.relevance),
                [
                    f"人工链接：{link.link_type}",
                    *([f"链接说明：{link.notes}"] if link.notes else []),
                ],
                query.chapter_id,
            )
            candidate.metadata["manual_link_id"] = link.id
            result.append(candidate)
        return result


class RecentChapterRetriever:
    name = "recent-chapter"

    def retrieve(self, db: Session, query: RetrievalQuery) -> list[RetrievalCandidate]:
        if query.chapter_id is None or query.recent_chapter_count <= 0:
            return []
        chapters = db.scalars(
            select(models.Chapter)
            .join(models.Volume, models.Volume.id == models.Chapter.volume_id)
            .where(
                models.Volume.project_id == query.project_id,
                models.Chapter.deleted_at.is_(None),
                models.Volume.deleted_at.is_(None),
            )
            .order_by(models.Volume.position, models.Chapter.position, models.Chapter.id)
        ).all()
        index_by_id = {row.id: index for index, row in enumerate(chapters)}
        current_index = index_by_id.get(query.chapter_id)
        if current_index is None:
            return []
        start = max(0, current_index - query.recent_chapter_count)
        neighbors = chapters[start:current_index]
        result: list[RetrievalCandidate] = []
        for distance, chapter in enumerate(reversed(neighbors), start=1):
            summary = db.scalar(
                select(models.ChapterSummary).where(
                    models.ChapterSummary.chapter_id == chapter.id,
                    models.ChapterSummary.deleted_at.is_(None),
                )
            )
            if summary is not None:
                content = summary.summary
                source_type = "chapter_summary"
                source_id = summary.id
                title = f"邻章摘要 · {chapter.title}"
                reason = f"当前章之前第 {distance} 章的人工/已审批摘要"
                metadata = {
                    "chapter_id": chapter.id,
                    "chapter_revision": chapter.revision,
                    "summary_revision": summary.revision,
                    "summary_used": True,
                }
            else:
                chapter_text = extract_visible_text(chapter.content)
                content = chapter_text[:1_200]
                source_type = "chapter"
                source_id = chapter.id
                title = f"邻章有限片段 · {chapter.title}"
                reason = f"当前章之前第 {distance} 章尚无摘要，仅取有限片段"
                metadata = {
                    "chapter_id": chapter.id,
                    "chapter_revision": chapter.revision,
                    "summary_used": False,
                    "capped_characters": len(chapter_text) > len(content),
                }
            result.append(
                RetrievalCandidate(
                    source_type=source_type,
                    source_id=source_id,
                    section="neighbor_summaries",
                    title=title,
                    content=content,
                    relevance=max(0.35, 0.72 - (distance - 1) * 0.1),
                    reasons=[reason],
                    metadata=metadata,
                )
            )
        return result


class TimelineRetriever:
    name = "timeline"

    def retrieve(self, db: Session, query: RetrievalQuery) -> list[RetrievalCandidate]:
        events = db.scalars(
            select(models.TimelineEvent)
            .where(
                models.TimelineEvent.project_id == query.project_id,
                models.TimelineEvent.deleted_at.is_(None),
            )
            .order_by(models.TimelineEvent.position, models.TimelineEvent.id)
        ).all()
        result: list[RetrievalCandidate] = []
        search = query.search_text.casefold()
        for event in events:
            reasons: list[str] = []
            score = 0.0
            if event.chapter_id == query.chapter_id:
                score = 0.94
                reasons.append("时间线事件属于当前章节")
            haystack = f"{event.label}\n{event.event_time}\n{event.description}".casefold()
            terms = [term for term in _search_terms(search) if term.casefold() in haystack]
            if terms:
                score = max(score, 0.76)
                reasons.append(f"任务命中时间线内容：{'、'.join(terms[:4])}")
            if query.agent_type in {"continuity", "timeline", "editor"}:
                score = max(score, 0.58)
                reasons.append(f"{query.agent_type} Agent 需要时间顺序")
            if score == 0:
                continue
            result.append(
                RetrievalCandidate(
                    source_type="timeline",
                    source_id=event.id,
                    section="timeline",
                    title=f"时间线 · {event.label}",
                    content=(
                        f"时间：{event.event_time or '未指定'}\n"
                        f"事件：{event.label}\n说明：{event.description}"
                    ),
                    relevance=score,
                    reasons=reasons,
                    metadata={"chapter_id": event.chapter_id, "revision": event.revision},
                )
            )
        return result


class ForeshadowRetriever:
    name = "foreshadow"

    def retrieve(self, db: Session, query: RetrievalQuery) -> list[RetrievalCandidate]:
        rows = db.scalars(
            select(models.Foreshadow)
            .where(
                models.Foreshadow.project_id == query.project_id,
                models.Foreshadow.deleted_at.is_(None),
            )
            .order_by(models.Foreshadow.id)
        ).all()
        result: list[RetrievalCandidate] = []
        search = query.search_text.casefold()
        for row in rows:
            reasons: list[str] = []
            score = 0.0
            if row.chapter_id == query.chapter_id:
                score = 0.92
                reasons.append("伏笔与当前章节关联")
            if row.status in {"open", "developed", "active"}:
                score = max(score, 0.6)
                reasons.append(f"伏笔状态为 {row.status}，仍需跟踪")
            haystack = f"{row.setup_text}\n{row.payoff_text}".casefold()
            terms = [term for term in _search_terms(search) if term.casefold() in haystack]
            if terms:
                score = max(score, 0.82)
                reasons.append(f"任务命中伏笔内容：{'、'.join(terms[:4])}")
            if query.agent_type in {"foreshadow", "continuity", "editor"}:
                score = max(score, 0.7)
                reasons.append(f"{query.agent_type} Agent 提升伏笔优先级")
            if score == 0:
                continue
            result.append(
                RetrievalCandidate(
                    source_type="foreshadow",
                    source_id=row.id,
                    section="foreshadow",
                    title=f"伏笔 · {row.status}",
                    content=f"埋设：{row.setup_text}\n回收：{row.payoff_text or '尚未回收'}",
                    relevance=score,
                    reasons=reasons,
                    metadata={"status": row.status, "revision": row.revision},
                )
            )
        return result


class RuleStyleRetriever:
    name = "rule-style"

    def retrieve(self, db: Session, query: RetrievalQuery) -> list[RetrievalCandidate]:
        rows = db.scalars(
            select(models.StyleGuide)
            .where(
                models.StyleGuide.project_id == query.project_id,
                models.StyleGuide.deleted_at.is_(None),
            )
            .order_by(models.StyleGuide.id)
        ).all()
        result: list[RetrievalCandidate] = []
        for row in rows:
            section = "world_rules" if row.category in {"world", "canon", "rule"} else "style"
            score = 0.8 if section == "world_rules" else 0.72
            reasons = [f"项目 {row.category} 规则"]
            if query.agent_type in {"writer", "style", "editor", "worldbuilding"}:
                score = min(1.0, score + 0.12)
                reasons.append(f"{query.agent_type} Agent 需要项目规则")
            result.append(
                RetrievalCandidate(
                    source_type="style_guide",
                    source_id=row.id,
                    section=section,
                    title=f"规则 · {row.name}",
                    content=row.rule_text,
                    relevance=score,
                    reasons=reasons,
                    metadata={"category": row.category, "revision": row.revision},
                )
            )
        return result


class FtsRetriever:
    name = "sqlite-fts"

    def retrieve(self, db: Session, query: RetrievalQuery) -> list[RetrievalCandidate]:
        terms = _search_terms(query.search_text)
        if not terms:
            return []
        expression = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:12])
        rows = (
            db.execute(
                text(
                    "SELECT source_type, source_id, title, content, bm25(context_fts) AS rank "
                    "FROM context_fts WHERE context_fts MATCH :query "
                    "AND project_id = :project_id ORDER BY rank LIMIT :limit"
                ),
                {
                    "query": expression,
                    "project_id": str(query.project_id),
                    "limit": min(query.max_results, 100),
                },
            )
            .mappings()
            .all()
        )
        result: list[RetrievalCandidate] = []
        for index, row in enumerate(rows):
            source_type = str(row["source_type"])
            source_id = int(row["source_id"])
            content = str(row["content"])
            title = str(row["title"])
            result.append(
                RetrievalCandidate(
                    source_type=source_type,
                    source_id=source_id,
                    section=_section_for_source(source_type),
                    title=f"全文检索 · {title}",
                    content=content,
                    relevance=max(0.35, 0.68 - index * 0.012),
                    reasons=[f"SQLite FTS 命中：{'、'.join(terms[:4])}"],
                    metadata={"retriever": self.name, "fts_rank": row["rank"]},
                )
            )
        return result


class PinRetriever:
    name = "context-pin"

    def retrieve(self, db: Session, query: RetrievalQuery) -> list[RetrievalCandidate]:
        pins = db.scalars(
            select(models.ContextPin)
            .where(
                models.ContextPin.project_id == query.project_id,
                models.ContextPin.deleted_at.is_(None),
                models.ContextPin.enabled.is_(True),
            )
            .order_by(models.ContextPin.priority.desc(), models.ContextPin.id)
        ).all()
        result: list[RetrievalCandidate] = []
        for pin in pins:
            resolved = _resolve_source(db, pin.source_type, pin.source_id)
            if resolved is None and not pin.content_override:
                continue
            title, content, section, metadata = resolved or (
                pin.label or "Pin",
                "",
                "history",
                {},
            )
            if pin.label:
                title = pin.label
            if pin.content_override:
                content = pin.content_override
                metadata = {**metadata, "content_override": True}
            result.append(
                RetrievalCandidate(
                    source_type=pin.source_type,
                    source_id=pin.source_id,
                    section=section,
                    title=f"Pin · {title}",
                    content=content,
                    relevance=1.0,
                    reasons=[f"用户 Pin，优先级 {pin.priority}"],
                    metadata={**metadata, "pin_id": pin.id, "pin_revision": pin.revision},
                    pinned=True,
                    pin_priority=pin.priority,
                    required=pin.required,
                )
            )
        return result


class CompositeRetriever:
    def __init__(
        self,
        retrievers: list[Retriever] | None = None,
        embedding_retriever: EmbeddingRetriever | None = None,
    ) -> None:
        self.retrievers = retrievers or [
            CurrentSceneRetriever(),
            EntityAliasRetriever(),
            ManualLinkRetriever(),
            RecentChapterRetriever(),
            TimelineRetriever(),
            ForeshadowRetriever(),
            RuleStyleRetriever(),
            FtsRetriever(),
            PinRetriever(),
        ]
        self.embedding_retriever = embedding_retriever or DisabledEmbeddingRetriever()

    def retrieve(self, db: Session, query: RetrievalQuery) -> list[RetrievalCandidate]:
        candidates: dict[str, RetrievalCandidate] = {}
        for retriever in [*self.retrievers, self.embedding_retriever]:
            for candidate in retriever.retrieve(db, query):
                identity = f"{candidate.source_type}:{candidate.source_id}"
                existing = candidates.get(identity)
                if existing is None:
                    candidates[identity] = candidate
                    continue
                existing.relevance = max(existing.relevance, candidate.relevance)
                existing.reasons = list(dict.fromkeys([*existing.reasons, *candidate.reasons]))
                existing.metadata.update(candidate.metadata)
                existing.required = existing.required or candidate.required
                if candidate.pinned:
                    existing.pinned = True
                    existing.pin_priority = candidate.pin_priority
                    existing.title = candidate.title
                    existing.content = candidate.content
        return sorted(
            candidates.values(),
            key=lambda item: (
                not item.required,
                not item.pinned,
                -(item.pin_priority or 0),
                -item.relevance,
                item.key,
            ),
        )[: query.max_results * 3]


def rebuild_fts_index(db: Session, project_id: int) -> int:
    db.execute(
        text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS context_fts USING fts5("
            "project_id UNINDEXED, source_type UNINDEXED, source_id UNINDEXED, "
            "title, content, tags, tokenize='unicode61')"
        )
    )
    db.execute(
        text("DELETE FROM context_fts WHERE project_id = :project_id"),
        {"project_id": str(project_id)},
    )
    records = _fts_records(db, project_id)
    if records:
        db.execute(
            text(
                "INSERT INTO context_fts "
                "(project_id, source_type, source_id, title, content, tags) "
                "VALUES (:project_id, :source_type, :source_id, :title, :content, :tags)"
            ),
            records,
        )
    return len(records)


def _fts_records(db: Session, project_id: int) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []

    def append(source_type: str, source_id: int, title: str, content: str, tags: str = "") -> None:
        records.append(
            {
                "project_id": str(project_id),
                "source_type": source_type,
                "source_id": str(source_id),
                "title": title,
                "content": content,
                "tags": tags,
            }
        )

    chapters = db.scalars(
        select(models.Chapter)
        .join(models.Volume, models.Volume.id == models.Chapter.volume_id)
        .where(
            models.Volume.project_id == project_id,
            models.Chapter.deleted_at.is_(None),
        )
    ).all()
    chapter_ids = [chapter_row.id for chapter_row in chapters]
    for chapter_row in chapters:
        append(
            "chapter",
            chapter_row.id,
            chapter_row.title,
            extract_visible_text(chapter_row.content),
        )
    for scene_row in db.scalars(
        select(models.Scene).where(
            models.Scene.chapter_id.in_(chapter_ids or [-1]),
            models.Scene.deleted_at.is_(None),
        )
    ).all():
        append(
            "scene",
            scene_row.id,
            scene_row.title,
            "\n".join(
                value
                for value in (
                    extract_visible_text(scene_row.synopsis),
                    extract_visible_text(scene_row.content),
                )
                if value
            ),
        )
    for summary_row in db.scalars(
        select(models.ChapterSummary).where(
            models.ChapterSummary.chapter_id.in_(chapter_ids or [-1]),
            models.ChapterSummary.deleted_at.is_(None),
        )
    ).all():
        chapter = next((item for item in chapters if item.id == summary_row.chapter_id), None)
        append(
            "chapter_summary",
            summary_row.id,
            chapter.title if chapter else "章节摘要",
            extract_visible_text(summary_row.summary),
        )
    for entity_row in db.scalars(
        select(models.StoryEntity).where(
            models.StoryEntity.project_id == project_id,
            models.StoryEntity.deleted_at.is_(None),
        )
    ).all():
        append(
            "entity",
            entity_row.id,
            entity_row.name,
            entity_row.description,
            " ".join(_json_string_list(entity_row.tags)),
        )
    for style_row in db.scalars(
        select(models.StyleGuide).where(
            models.StyleGuide.project_id == project_id,
            models.StyleGuide.deleted_at.is_(None),
        )
    ).all():
        append(
            "style_guide",
            style_row.id,
            style_row.name,
            style_row.rule_text,
            style_row.category,
        )
    for timeline_row in db.scalars(
        select(models.TimelineEvent).where(
            models.TimelineEvent.project_id == project_id,
            models.TimelineEvent.deleted_at.is_(None),
        )
    ).all():
        append(
            "timeline",
            timeline_row.id,
            timeline_row.label,
            f"{timeline_row.event_time}\n{timeline_row.description}",
        )
    for foreshadow_row in db.scalars(
        select(models.Foreshadow).where(
            models.Foreshadow.project_id == project_id,
            models.Foreshadow.deleted_at.is_(None),
        )
    ).all():
        append(
            "foreshadow",
            foreshadow_row.id,
            f"伏笔 {foreshadow_row.id}",
            f"{foreshadow_row.setup_text}\n{foreshadow_row.payoff_text}",
            foreshadow_row.status,
        )
    return records


def _entity_candidate(
    db: Session,
    entity: models.StoryEntity,
    aliases: list[str],
    tags: list[str],
    score: float,
    reasons: list[str],
    chapter_id: int | None,
    *,
    changes: list[tuple[int | None, str, str, str, str]] | None = None,
) -> RetrievalCandidate:
    if changes is None:
        changes = [
            (
                item.chapter_id,
                item.field_name,
                item.old_value,
                item.new_value,
                item.reason,
            )
            for item in db.scalars(
                select(models.EntityStateChange)
                .where(
                    models.EntityStateChange.entity_id == entity.id,
                    models.EntityStateChange.deleted_at.is_(None),
                )
                .order_by(models.EntityStateChange.id.desc())
                .limit(12)
            ).all()
        ]
    change_lines = [
        f"- {field_name}: {old_value or '未记录'} -> {new_value}（{reason or '无说明'}）"
        for item_chapter_id, field_name, old_value, new_value, reason in changes
        if chapter_id is None or item_chapter_id is None or item_chapter_id <= chapter_id
    ]
    section = (
        "character_state" if entity.kind in {"character", "person"} else "location_item_relation"
    )
    content = (
        f"名称：{entity.name}\n类型：{entity.kind}\n"
        f"别名：{'、'.join(aliases) if aliases else '无'}\n"
        f"标签：{'、'.join(tags) if tags else '无'}\n"
        f"说明：{entity.description or '无'}"
    )
    if change_lines:
        content += "\n最近状态变化：\n" + "\n".join(change_lines)
    return RetrievalCandidate(
        source_type="entity",
        source_id=entity.id,
        section=section,
        title=f"实体 · {entity.name}",
        content=content,
        relevance=min(1.0, score),
        reasons=reasons,
        metadata={"kind": entity.kind, "revision": entity.revision, "aliases": aliases},
    )


def _recent_entity_changes(
    db: Session, entity_ids: set[int]
) -> dict[int, list[tuple[int | None, str, str, str, str]]]:
    if not entity_ids:
        return {}
    ranked = (
        select(
            models.EntityStateChange.entity_id.label("entity_id"),
            models.EntityStateChange.chapter_id.label("chapter_id"),
            models.EntityStateChange.field_name.label("field_name"),
            models.EntityStateChange.old_value.label("old_value"),
            models.EntityStateChange.new_value.label("new_value"),
            models.EntityStateChange.reason.label("reason"),
            func.row_number()
            .over(
                partition_by=models.EntityStateChange.entity_id,
                order_by=models.EntityStateChange.id.desc(),
            )
            .label("row_number"),
        )
        .where(models.EntityStateChange.deleted_at.is_(None))
        .subquery()
    )
    rows = db.execute(
        select(ranked)
        .where(
            ranked.c.entity_id.in_(entity_ids),
            ranked.c.row_number <= 12,
        )
        .order_by(ranked.c.entity_id, ranked.c.row_number)
    ).mappings()
    result: dict[int, list[tuple[int | None, str, str, str, str]]] = {}
    for row in rows:
        result.setdefault(int(row["entity_id"]), []).append(
            (
                int(row["chapter_id"]) if row["chapter_id"] is not None else None,
                str(row["field_name"]),
                str(row["old_value"]),
                str(row["new_value"]),
                str(row["reason"]),
            )
        )
    return result


def _relation_candidates(
    db: Session, project_id: int, entity_ids: set[int]
) -> list[RetrievalCandidate]:
    if not entity_ids:
        return []
    rows = db.scalars(
        select(models.EntityRelation).where(
            models.EntityRelation.project_id == project_id,
            models.EntityRelation.deleted_at.is_(None),
            (
                models.EntityRelation.source_entity_id.in_(entity_ids)
                | models.EntityRelation.target_entity_id.in_(entity_ids)
            ),
        )
    ).all()
    result: list[RetrievalCandidate] = []
    for row in rows:
        source = db.get(models.StoryEntity, row.source_entity_id)
        target = db.get(models.StoryEntity, row.target_entity_id)
        if source is None or target is None:
            continue
        result.append(
            RetrievalCandidate(
                source_type="relation",
                source_id=row.id,
                section="location_item_relation",
                title=f"关系 · {source.name} / {target.name}",
                content=f"{source.name} --{row.relation_type}--> {target.name}\n{row.notes}",
                relevance=0.78,
                reasons=["关系两端包含已命中的实体"],
                metadata={
                    "source_entity_id": source.id,
                    "target_entity_id": target.id,
                    "revision": row.revision,
                },
            )
        )
    return result


def _scene_state(db: Session, scene_id: int | None) -> models.SceneState | None:
    if scene_id is None:
        return None
    return db.scalar(
        select(models.SceneState).where(
            models.SceneState.scene_id == scene_id,
            models.SceneState.deleted_at.is_(None),
        )
    )


def _state_entity_ids(state: models.SceneState | None) -> dict[int, str]:
    if state is None:
        return {}
    result: dict[int, str] = {}
    if state.viewpoint_entity_id is not None:
        result[state.viewpoint_entity_id] = "视角人物"
    if state.location_entity_id is not None:
        result[state.location_entity_id] = "当前地点"
    for item in _json_int_list(state.item_entity_ids_json):
        result[item] = "当前物品"
    return result


def _summary_entity_ids(db: Session, chapter_id: int | None) -> list[int]:
    if chapter_id is None:
        return []
    row = db.scalar(
        select(models.ChapterSummary).where(
            models.ChapterSummary.chapter_id == chapter_id,
            models.ChapterSummary.deleted_at.is_(None),
        )
    )
    return _json_int_list(row.entity_ids_json) if row is not None else []


def _resolve_source(
    db: Session, source_type: str, source_id: int
) -> tuple[str, str, str, dict[str, Any]] | None:
    if source_type == "chapter":
        chapter_row = db.get(models.Chapter, source_id)
        return (
            (
                chapter_row.title,
                extract_visible_text(chapter_row.content),
                "history",
                {"revision": chapter_row.revision},
            )
            if chapter_row is not None and chapter_row.deleted_at is None
            else None
        )
    if source_type == "scene":
        scene_row = db.get(models.Scene, source_id)
        return (
            (
                scene_row.title,
                "\n".join(
                    value
                    for value in (
                        extract_visible_text(scene_row.synopsis),
                        extract_visible_text(scene_row.content),
                    )
                    if value
                ),
                "current_scene",
                {"revision": scene_row.revision},
            )
            if scene_row is not None and scene_row.deleted_at is None
            else None
        )
    if source_type == "chapter_summary":
        summary_row = db.get(models.ChapterSummary, source_id)
        return (
            (
                "章节摘要",
                extract_visible_text(summary_row.summary),
                "neighbor_summaries",
                {"revision": summary_row.revision, "chapter_id": summary_row.chapter_id},
            )
            if summary_row is not None and summary_row.deleted_at is None
            else None
        )
    if source_type == "entity":
        entity_row = db.get(models.StoryEntity, source_id)
        if entity_row is None or entity_row.deleted_at is not None:
            return None
        return (
            entity_row.name,
            f"名称：{entity_row.name}\n类型：{entity_row.kind}\n说明：{entity_row.description}",
            "character_state"
            if entity_row.kind in {"character", "person"}
            else "location_item_relation",
            {"revision": entity_row.revision},
        )
    if source_type == "style_guide":
        style_row = db.get(models.StyleGuide, source_id)
        return (
            (
                style_row.name,
                style_row.rule_text,
                "world_rules" if style_row.category in {"world", "canon", "rule"} else "style",
                {"revision": style_row.revision},
            )
            if style_row is not None and style_row.deleted_at is None
            else None
        )
    if source_type == "timeline":
        timeline_row = db.get(models.TimelineEvent, source_id)
        return (
            (
                timeline_row.label,
                f"{timeline_row.event_time}\n{timeline_row.description}",
                "timeline",
                {"revision": timeline_row.revision},
            )
            if timeline_row is not None and timeline_row.deleted_at is None
            else None
        )
    if source_type == "foreshadow":
        foreshadow_row = db.get(models.Foreshadow, source_id)
        return (
            (
                f"伏笔 {foreshadow_row.id}",
                f"埋设：{foreshadow_row.setup_text}\n回收：{foreshadow_row.payoff_text or '尚未回收'}",
                "foreshadow",
                {"revision": foreshadow_row.revision, "status": foreshadow_row.status},
            )
            if foreshadow_row is not None and foreshadow_row.deleted_at is None
            else None
        )
    if source_type == "relation":
        relation_row = db.get(models.EntityRelation, source_id)
        if relation_row is None or relation_row.deleted_at is not None:
            return None
        source = db.get(models.StoryEntity, relation_row.source_entity_id)
        target = db.get(models.StoryEntity, relation_row.target_entity_id)
        if source is None or target is None:
            return None
        return (
            f"{source.name} / {target.name}",
            f"{source.name} --{relation_row.relation_type}--> {target.name}\n{relation_row.notes}",
            "location_item_relation",
            {"revision": relation_row.revision},
        )
    return None


def _search_terms(value: str) -> list[str]:
    terms: list[str] = []
    for match in _TERM_RE.findall(value):
        normalized = match.strip().casefold()
        if normalized and normalized not in terms:
            terms.append(normalized)
        if len(terms) >= 24:
            break
    return terms


def _section_for_source(source_type: str) -> str:
    return {
        "scene": "history",
        "chapter": "history",
        "chapter_summary": "neighbor_summaries",
        "entity": "character_state",
        "style_guide": "style",
        "timeline": "timeline",
        "foreshadow": "foreshadow",
    }.get(source_type, "history")


def _json_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _json_string_list(value: str) -> list[str]:
    parsed = _json_value(value)
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _json_int_list(value: str) -> list[int]:
    parsed = _json_value(value)
    if not isinstance(parsed, list):
        return []
    return [int(item) for item in parsed if isinstance(item, int) and not isinstance(item, bool)]


def _compact_json(value: dict[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)
