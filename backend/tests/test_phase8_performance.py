from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import models
from app.database import Base
from app.schemas import ChapterAutosave, WorkflowCreate, WorkflowEdgeWrite, WorkflowNodeWrite
from app.services import novels, workflow_validation, workflows
from app.services.context_retrieval import EntityAliasRetriever, RetrievalQuery


@pytest.fixture
def db(tmp_path: Path) -> Generator[Session, None, None]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'performance.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def test_100_node_graph_and_cursor_paginated_history(db: Session) -> None:
    project = models.Project(title="百节点性能验收")
    db.add(project)
    db.flush()
    nodes = [WorkflowNodeWrite(key="start", type="start", label="Start")]
    nodes.extend(
        WorkflowNodeWrite(
            key=f"step_{index}",
            type="text_template",
            label=f"步骤 {index}",
            config={"template": f"步骤 {index}: {{input.topic}}"},
        )
        for index in range(1, 99)
    )
    nodes.append(WorkflowNodeWrite(key="output", type="output", label="Output"))
    edges = [
        WorkflowEdgeWrite(
            key=f"edge_{index}",
            source=nodes[index].key,
            target=nodes[index + 1].key,
        )
        for index in range(len(nodes) - 1)
    ]

    started = perf_counter()
    plan = workflow_validation.compile_graph(db, project.id, nodes, edges)
    assert perf_counter() - started < 2.0
    assert len(plan["topological_order"]) == 100

    workflow = workflows.create_workflow(
        db,
        WorkflowCreate(
            project_id=project.id,
            name="百节点工作流",
            nodes=nodes,
            edges=edges,
        ),
    )
    db.add_all(
        [
            models.WorkflowRun(
                workflow_id=workflow.id,
                project_id=project.id,
                workflow_revision=workflow.revision,
                plan_json="{}",
                snapshot_json="{}",
            )
            for _ in range(125)
        ]
    )
    db.flush()

    first = workflows.list_runs(db, project_id=project.id, limit=50)
    second = workflows.list_runs(
        db,
        project_id=project.id,
        limit=50,
        before_id=first[-1].id,
    )
    third = workflows.list_runs(
        db,
        project_id=project.id,
        limit=50,
        before_id=second[-1].id,
    )
    assert [len(first), len(second), len(third)] == [50, 50, 25]
    assert not ({item.id for item in first} & {item.id for item in second})
    assert first[-1].id > second[0].id


def test_100k_character_chapter_autosave(db: Session) -> None:
    project = models.Project(title="十万字章节验收")
    db.add(project)
    db.flush()
    volume = models.Volume(project_id=project.id, title="第一卷", position=1)
    db.add(volume)
    db.flush()
    chapter = models.Chapter(
        volume_id=volume.id,
        title="长章节",
        content="起",
        word_count=1,
        position=1,
    )
    db.add(chapter)
    db.flush()

    content = "雾" * 100_000
    started = perf_counter()
    saved = novels.autosave_chapter(
        db,
        chapter.id,
        ChapterAutosave(
            title="长章节",
            content=content,
            expected_revision=chapter.revision,
        ),
    )
    db.flush()
    assert perf_counter() - started < 5.0
    assert saved.word_count == 100_000
    assert len(saved.content) == 100_000


def test_1000_entity_retrieval_batches_state_queries(db: Session) -> None:
    project = models.Project(title="千实体检索验收")
    db.add(project)
    db.flush()
    entities = [
        models.StoryEntity(
            project_id=project.id,
            name=f"人物{index:04d}",
            kind="character",
            description=f"第 {index} 号人物档案",
            tags="[]",
        )
        for index in range(1_000)
    ]
    db.add_all(entities)
    db.flush()
    db.add_all(
        [
            models.EntityStateChange(
                entity_id=item.id,
                field_name="位置",
                old_value="旧港",
                new_value="雾港",
                reason="性能验收",
            )
            for item in entities
        ]
    )
    db.flush()

    statements = 0

    def count_statement(
        _connection: Any,
        _cursor: Any,
        _statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        nonlocal statements
        statements += 1

    engine = db.get_bind()
    assert isinstance(engine, Engine)
    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        started = perf_counter()
        values = EntityAliasRetriever().retrieve(
            db,
            RetrievalQuery(
                project_id=project.id,
                chapter_id=None,
                scene_id=None,
                agent_type="character",
                query_text="检查人物状态",
                workflow_input={},
                upstream_outputs={},
                recent_chapter_count=0,
                max_results=1_000,
            ),
        )
        elapsed = perf_counter() - started
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert len(values) == 1_000
    assert statements <= 5
    assert elapsed < 5.0
    assert "位置: 旧港 -> 雾港" in values[0].content
