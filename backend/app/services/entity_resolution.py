from __future__ import annotations

import json
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models


ResolutionStatus = Literal["resolved", "unmatched", "ambiguous", "invalid"]


@dataclass(frozen=True)
class EntityCandidate:
    entity_id: int
    name: str
    kind: str
    method: str
    score: float


@dataclass(frozen=True)
class EntityResolution:
    status: ResolutionStatus
    method: str
    target_id: int | None
    target_name: str | None
    candidates: tuple[EntityCandidate, ...] = ()
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "method": self.method,
            "target_id": self.target_id,
            "target_name": self.target_name,
            "candidates": [asdict(candidate) for candidate in self.candidates],
            "message": self.message,
        }


class EntityResolver:
    def __init__(
        self,
        db: Session,
        project_id: int,
        *,
        chapter_id: int | None = None,
        high_confidence_threshold: float = 0.92,
        uniqueness_margin: float = 0.05,
    ) -> None:
        self.db = db
        self.project_id = project_id
        self.chapter_id = chapter_id
        self.high_confidence_threshold = high_confidence_threshold
        self.uniqueness_margin = uniqueness_margin
        self.entities = list(
            db.scalars(
                select(models.StoryEntity).where(
                    models.StoryEntity.project_id == project_id,
                    models.StoryEntity.deleted_at.is_(None),
                )
            ).all()
        )
        entity_ids = [entity.id for entity in self.entities]
        self.aliases = list(
            db.scalars(
                select(models.EntityAlias).where(
                    models.EntityAlias.entity_id.in_(entity_ids or [-1]),
                    models.EntityAlias.deleted_at.is_(None),
                )
            ).all()
        )
        self.by_id = {entity.id: entity for entity in self.entities}
        self.aliases_by_entity: dict[int, list[str]] = {}
        for alias in self.aliases:
            self.aliases_by_entity.setdefault(alias.entity_id, []).append(alias.alias)

    def resolve(
        self,
        *,
        name: str,
        kind: str,
        entity_id: int | None = None,
        manual_link_type: str | None = None,
    ) -> EntityResolution:
        if entity_id is not None:
            return self._by_explicit_id(entity_id, kind)

        normalized = normalize_entity_name(name)
        exact_name = [
            entity
            for entity in self.entities
            if entity.kind == kind and normalize_entity_name(entity.name) == normalized
        ]
        exact_result = self._exact_result(exact_name, "exact_name")
        if exact_result is not None:
            return exact_result

        alias_entity_ids = {
            alias.entity_id
            for alias in self.aliases
            if normalize_entity_name(alias.alias) == normalized
        }
        exact_alias = [
            entity
            for entity in self.entities
            if entity.id in alias_entity_ids and entity.kind == kind
        ]
        alias_result = self._exact_result(exact_alias, "alias")
        if alias_result is not None:
            return alias_result

        if manual_link_type and self.chapter_id is not None:
            linked = self._manual_link_candidates(manual_link_type, kind)
            manual_result = self._exact_result(linked, "manual_link")
            if manual_result is not None:
                return manual_result

        fuzzy = self._high_confidence(normalized, kind)
        if fuzzy is not None:
            return fuzzy

        return EntityResolution(
            status="unmatched",
            method="new_candidate",
            target_id=None,
            target_name=None,
            message="没有可安全合并的现有实体，将作为新实体候选显示",
        )

    def _by_explicit_id(self, entity_id: int, kind: str) -> EntityResolution:
        entity = self.by_id.get(entity_id)
        if entity is None:
            return EntityResolution(
                status="invalid",
                method="id",
                target_id=None,
                target_name=None,
                message="显式实体 ID 不存在或不属于当前项目",
            )
        if entity.kind != kind:
            return EntityResolution(
                status="invalid",
                method="id",
                target_id=None,
                target_name=None,
                candidates=(self._candidate(entity, "id", 1.0),),
                message=f"显式实体 ID 的类型是 {entity.kind}，不是 {kind}",
            )
        return self._resolved(entity, "id", 1.0)

    def _exact_result(
        self, entities: list[models.StoryEntity], method: str
    ) -> EntityResolution | None:
        if len(entities) == 1:
            return self._resolved(entities[0], method, 1.0)
        if len(entities) > 1:
            candidates = tuple(
                self._candidate(entity, method, 1.0)
                for entity in sorted(entities, key=lambda item: item.id)
            )
            return EntityResolution(
                status="ambiguous",
                method=method,
                target_id=None,
                target_name=None,
                candidates=candidates,
                message="多个实体同时匹配，必须人工选择，系统不会强制合并",
            )
        return None

    def _manual_link_candidates(
        self, link_type: str, kind: str
    ) -> list[models.StoryEntity]:
        linked_ids = set(
            self.db.scalars(
                select(models.ChapterEntityLink.entity_id).where(
                    models.ChapterEntityLink.chapter_id == self.chapter_id,
                    models.ChapterEntityLink.link_type == link_type,
                    models.ChapterEntityLink.deleted_at.is_(None),
                )
            ).all()
        )
        return [
            entity
            for entity in self.entities
            if entity.id in linked_ids and entity.kind == kind
        ]

    def _high_confidence(
        self, normalized_name: str, kind: str
    ) -> EntityResolution | None:
        scored: list[tuple[float, models.StoryEntity]] = []
        for entity in self.entities:
            if entity.kind != kind:
                continue
            names = [entity.name, *self.aliases_by_entity.get(entity.id, [])]
            score = max(
                SequenceMatcher(
                    None, normalized_name, normalize_entity_name(candidate_name)
                ).ratio()
                for candidate_name in names
            )
            scored.append((score, entity))
        scored.sort(key=lambda item: (-item[0], item[1].id))
        if not scored or scored[0][0] < self.high_confidence_threshold:
            return None
        top_score, top_entity = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        visible = tuple(
            self._candidate(entity, "high_confidence", score)
            for score, entity in scored[:5]
            if score >= self.high_confidence_threshold - 0.08
        )
        if top_score - second_score < self.uniqueness_margin:
            return EntityResolution(
                status="ambiguous",
                method="high_confidence",
                target_id=None,
                target_name=None,
                candidates=visible,
                message="高置信候选不唯一，必须人工确认",
            )
        return EntityResolution(
            status="resolved",
            method="high_confidence",
            target_id=top_entity.id,
            target_name=top_entity.name,
            candidates=visible,
            message="唯一高置信候选",
        )

    def _resolved(
        self, entity: models.StoryEntity, method: str, score: float
    ) -> EntityResolution:
        return EntityResolution(
            status="resolved",
            method=method,
            target_id=entity.id,
            target_name=entity.name,
            candidates=(self._candidate(entity, method, score),),
        )

    @staticmethod
    def _candidate(
        entity: models.StoryEntity, method: str, score: float
    ) -> EntityCandidate:
        return EntityCandidate(
            entity_id=entity.id,
            name=entity.name,
            kind=entity.kind,
            method=method,
            score=round(score, 4),
        )


def normalize_entity_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if not character.isspace())


def json_string_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []
