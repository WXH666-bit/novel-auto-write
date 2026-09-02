"""Editable character/plot graph endpoints with project-level isolation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    AuditLog,
    Chapter,
    Character,
    PlotThread,
    Project,
    StoryGraphEdge,
    StoryGraphLayout,
    StoryGraphNode,
    TimelineEvent,
    User,
)
from ..schemas import (
    StoryGraphEdgeCreate,
    StoryGraphEdgeRead,
    StoryGraphEdgeUpdate,
    StoryGraphLayoutRead,
    StoryGraphLayoutUpdate,
    StoryGraphNodeCreate,
    StoryGraphNodeRead,
    StoryGraphNodeUpdate,
    StoryGraphRead,
)
from ..security import get_current_user, user_id_of
from . import require_project

router = APIRouter(prefix="/api", tags=["story-graph"])


def _node(db: Session, project: Project, node_id: str) -> StoryGraphNode:
    node = db.scalar(
        select(StoryGraphNode).where(
            StoryGraphNode.id == node_id, StoryGraphNode.project_id == project.id
        )
    )
    if node is None:
        raise HTTPException(status_code=404, detail="图谱节点不存在")
    return node


def _edge(db: Session, project: Project, edge_id: str) -> StoryGraphEdge:
    edge = db.scalar(
        select(StoryGraphEdge).where(
            StoryGraphEdge.id == edge_id, StoryGraphEdge.project_id == project.id
        )
    )
    if edge is None:
        raise HTTPException(status_code=404, detail="图谱连线不存在")
    return edge


def _validate_entity_refs(
    db: Session,
    project: Project,
    *,
    node_type: str,
    ref_id: str | None,
    character_id: str | None,
    chapter_id: str | None,
    plot_thread_id: str | None,
) -> dict[str, Any]:
    kind = node_type.strip().lower() or "custom"
    values: dict[str, Any] = {"node_type": kind}
    if ref_id and kind in {"character", "person"}:
        character_id = ref_id
        kind = "character"
    elif ref_id and kind in {"chapter", "paper"}:
        chapter_id = ref_id
        kind = "chapter"
    elif ref_id and kind in {"plot", "plot_thread", "story_line"}:
        plot_thread_id = ref_id
        kind = "plot"
    values["node_type"] = kind
    values["ref_id"] = ref_id
    values["character_id"] = character_id
    values["chapter_id"] = chapter_id
    values["plot_thread_id"] = plot_thread_id
    if character_id:
        if db.scalar(
            select(Character.id).where(
                Character.id == character_id, Character.project_id == project.id
            )
        ) is None:
            raise HTTPException(status_code=404, detail="关联人物不属于当前项目")
        values["ref_id"] = ref_id or character_id
    if chapter_id:
        if db.scalar(
            select(Chapter.id).where(Chapter.id == chapter_id, Chapter.project_id == project.id)
        ) is None:
            raise HTTPException(status_code=404, detail="关联章节不属于当前项目")
        values["ref_id"] = ref_id or chapter_id
    if plot_thread_id:
        if db.scalar(
            select(PlotThread.id).where(
                PlotThread.id == plot_thread_id, PlotThread.project_id == project.id
            )
        ) is None:
            raise HTTPException(status_code=404, detail="关联剧情线不属于当前项目")
        values["ref_id"] = ref_id or plot_thread_id
    if kind in {"event", "timeline"} and ref_id:
        if db.scalar(
            select(TimelineEvent.id).where(
                TimelineEvent.id == ref_id, TimelineEvent.project_id == project.id
            )
        ) is None:
            raise HTTPException(status_code=404, detail="关联情节事件不属于当前项目")
        values["node_type"] = "event"
    return values


def _audit(db: Session, user: User, project: Project, action: str, entity: Any) -> None:
    db.add(
        AuditLog(
            project_id=project.id,
            actor_user_id=user_id_of(user),
            actor=user.username or user.email or user.id,
            action=action,
            entity_type="story_graph",
            entity_id=entity.id,
            after_json={"version": getattr(entity, "version", None)},
        )
    )


def _touch(project: Project) -> None:
    project.memory_epoch = int(project.memory_epoch or 0) + 1


def _scope_chapter(
    db: Session,
    project: Project,
    chapter_id: str | None,
) -> str | None:
    """Resolve and validate the chapter that owns this graph surface."""

    selected = chapter_id or project.current_chapter_id
    if not selected:
        return None
    if db.scalar(
        select(Chapter.id).where(
            Chapter.id == selected,
            Chapter.project_id == project.id,
        )
    ) is None:
        raise HTTPException(status_code=404, detail="图谱章节不存在或不属于当前项目")
    return str(selected)


@router.get("/projects/{project_id}/story-graph", response_model=StoryGraphRead)
@router.get("/projects/{project_id}/graph", response_model=StoryGraphRead, include_in_schema=False)
def get_graph(
    project_id: str,
    chapter_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StoryGraphRead:
    project = require_project(db, project_id, current_user)
    scope_chapter_id = _scope_chapter(db, project, chapter_id)
    nodes = db.scalars(
        select(StoryGraphNode)
        .where(
            StoryGraphNode.project_id == project.id,
            StoryGraphNode.scope_chapter_id == scope_chapter_id,
        )
        .order_by(StoryGraphNode.created_at)
    ).all()
    edges = db.scalars(
        select(StoryGraphEdge)
        .where(
            StoryGraphEdge.project_id == project.id,
            StoryGraphEdge.scope_chapter_id == scope_chapter_id,
        )
        .order_by(StoryGraphEdge.created_at)
    ).all()
    layout = db.scalar(
        select(StoryGraphLayout).where(
            StoryGraphLayout.project_id == project.id,
            StoryGraphLayout.scope_chapter_id == scope_chapter_id,
        )
    )
    return StoryGraphRead(
        chapter_id=scope_chapter_id,
        nodes=[StoryGraphNodeRead.model_validate(item) for item in nodes],
        edges=[StoryGraphEdgeRead.model_validate(item) for item in edges],
        layout=StoryGraphLayoutRead.model_validate(layout) if layout else None,
    )


@router.get("/projects/{project_id}/story-graph/nodes", response_model=list[StoryGraphNodeRead])
def list_nodes(
    project_id: str,
    chapter_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[StoryGraphNodeRead]:
    project = require_project(db, project_id, current_user)
    scope_chapter_id = _scope_chapter(db, project, chapter_id)
    rows = db.scalars(
        select(StoryGraphNode)
        .where(
            StoryGraphNode.project_id == project.id,
            StoryGraphNode.scope_chapter_id == scope_chapter_id,
        )
        .order_by(StoryGraphNode.created_at)
    ).all()
    return [StoryGraphNodeRead.model_validate(item) for item in rows]


@router.post(
    "/projects/{project_id}/story-graph/nodes",
    response_model=StoryGraphNodeRead,
    status_code=status.HTTP_201_CREATED,
)
def create_node(
    project_id: str,
    payload: StoryGraphNodeCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StoryGraphNodeRead:
    project = require_project(db, project_id, current_user)
    scope_chapter_id = _scope_chapter(db, project, payload.scope_chapter_id)
    values = _validate_entity_refs(
        db,
        project,
        node_type=payload.node_type,
        ref_id=payload.ref_id,
        character_id=payload.character_id,
        chapter_id=payload.chapter_id,
        plot_thread_id=payload.plot_thread_id,
    )
    node = StoryGraphNode(
        project_id=project.id,
        scope_chapter_id=scope_chapter_id,
        **values,
        label=payload.label,
        data=payload.data,
        position_x=payload.position_x,
        position_y=payload.position_y,
        width=payload.width,
        height=payload.height,
        status=payload.status,
    )
    db.add(node)
    try:
        db.flush()
        _touch(project)
        _audit(db, current_user, project, "story_graph.node_created", node)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="图谱中已经存在相同关联节点") from exc
    db.refresh(node)
    return StoryGraphNodeRead.model_validate(node)


@router.patch("/projects/{project_id}/story-graph/nodes/{node_id}", response_model=StoryGraphNodeRead)
def update_node(
    project_id: str,
    node_id: str,
    payload: StoryGraphNodeUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StoryGraphNodeRead:
    project = require_project(db, project_id, current_user)
    node = db.scalar(
        select(StoryGraphNode)
        .where(StoryGraphNode.id == node_id, StoryGraphNode.project_id == project.id)
        .with_for_update()
    )
    if node is None:
        raise HTTPException(status_code=404, detail="图谱节点不存在")
    if payload.expected_version is not None and payload.expected_version != node.version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "graph_node_conflict",
                "message": "图谱节点已被其他窗口更新",
                "expected_version": payload.expected_version,
                "actual_version": node.version,
            },
        )
    incoming = payload.model_dump(exclude_unset=True)
    incoming.pop("expected_version", None)
    if "scope_chapter_id" in incoming:
        incoming["scope_chapter_id"] = _scope_chapter(
            db,
            project,
            incoming["scope_chapter_id"],
        )
    if any(key in incoming for key in ("node_type", "ref_id", "character_id", "chapter_id", "plot_thread_id")):
        refs = _validate_entity_refs(
            db,
            project,
            node_type=str(incoming.get("node_type", node.node_type)),
            ref_id=incoming.get("ref_id", node.ref_id),
            character_id=incoming.get("character_id", node.character_id),
            chapter_id=incoming.get("chapter_id", node.chapter_id),
            plot_thread_id=incoming.get("plot_thread_id", node.plot_thread_id),
        )
        incoming.update(refs)
    if not incoming:
        return StoryGraphNodeRead.model_validate(node)
    semantic_change = bool(
        set(incoming).intersection(
            {"node_type", "ref_id", "character_id", "chapter_id", "plot_thread_id", "label", "data", "status"}
        )
    )
    for key, value in incoming.items():
        setattr(node, key, value)
    node.version += 1
    if semantic_change:
        _touch(project)
    _audit(db, current_user, project, "story_graph.node_updated", node)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="图谱节点关联已存在") from exc
    db.refresh(node)
    return StoryGraphNodeRead.model_validate(node)


@router.delete("/projects/{project_id}/story-graph/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_node(
    project_id: str,
    node_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    project = require_project(db, project_id, current_user)
    node = _node(db, project, node_id)
    db.query(StoryGraphEdge).filter(
        (StoryGraphEdge.source_node_id == node.id) | (StoryGraphEdge.target_node_id == node.id)
    ).delete(synchronize_session=False)
    db.delete(node)
    _touch(project)
    _audit(db, current_user, project, "story_graph.node_deleted", node)
    db.commit()


@router.get("/projects/{project_id}/story-graph/edges", response_model=list[StoryGraphEdgeRead])
def list_edges(
    project_id: str,
    chapter_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[StoryGraphEdgeRead]:
    project = require_project(db, project_id, current_user)
    scope_chapter_id = _scope_chapter(db, project, chapter_id)
    rows = db.scalars(
        select(StoryGraphEdge)
        .where(
            StoryGraphEdge.project_id == project.id,
            StoryGraphEdge.scope_chapter_id == scope_chapter_id,
        )
        .order_by(StoryGraphEdge.created_at)
    ).all()
    return [StoryGraphEdgeRead.model_validate(item) for item in rows]


@router.post(
    "/projects/{project_id}/story-graph/edges",
    response_model=StoryGraphEdgeRead,
    status_code=status.HTTP_201_CREATED,
)
def create_edge(
    project_id: str,
    payload: StoryGraphEdgeCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StoryGraphEdgeRead:
    project = require_project(db, project_id, current_user)
    source = _node(db, project, payload.source_node_id)
    target = _node(db, project, payload.target_node_id)
    scope_chapter_id = _scope_chapter(
        db,
        project,
        payload.scope_chapter_id or source.scope_chapter_id,
    )
    if (
        source.scope_chapter_id != scope_chapter_id
        or target.scope_chapter_id != scope_chapter_id
    ):
        raise HTTPException(status_code=422, detail="图谱连线两端必须属于当前章节")
    if source.id == target.id:
        raise HTTPException(status_code=422, detail="图谱连线不能连接节点自身")
    edge = StoryGraphEdge(
        project_id=project.id,
        scope_chapter_id=scope_chapter_id,
        source_node_id=source.id,
        target_node_id=target.id,
        relation_type=payload.relation_type,
        label=payload.label,
        directed=payload.directed,
        weight=payload.weight,
        data=payload.data,
        status=payload.status,
    )
    db.add(edge)
    try:
        db.flush()
        _touch(project)
        _audit(db, current_user, project, "story_graph.edge_created", edge)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="相同类型的图谱连线已经存在") from exc
    db.refresh(edge)
    return StoryGraphEdgeRead.model_validate(edge)


@router.patch("/projects/{project_id}/story-graph/edges/{edge_id}", response_model=StoryGraphEdgeRead)
def update_edge(
    project_id: str,
    edge_id: str,
    payload: StoryGraphEdgeUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StoryGraphEdgeRead:
    project = require_project(db, project_id, current_user)
    edge = db.scalar(
        select(StoryGraphEdge)
        .where(StoryGraphEdge.id == edge_id, StoryGraphEdge.project_id == project.id)
        .with_for_update()
    )
    if edge is None:
        raise HTTPException(status_code=404, detail="图谱连线不存在")
    if payload.expected_version is not None and payload.expected_version != edge.version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "graph_edge_conflict",
                "message": "图谱连线已被其他窗口更新",
                "expected_version": payload.expected_version,
                "actual_version": edge.version,
            },
        )
    values = payload.model_dump(exclude_unset=True)
    values.pop("expected_version", None)
    if "scope_chapter_id" in values:
        values["scope_chapter_id"] = _scope_chapter(
            db,
            project,
            values["scope_chapter_id"],
        )
    if not values:
        return StoryGraphEdgeRead.model_validate(edge)
    if any(
        key in values
        for key in ("source_node_id", "target_node_id", "scope_chapter_id")
    ):
        source = _node(db, project, str(values.get("source_node_id") or edge.source_node_id))
        target = _node(db, project, str(values.get("target_node_id") or edge.target_node_id))
        if source.id == target.id:
            raise HTTPException(status_code=422, detail="图谱连线不能连接节点自身")
        edge_scope = values.get("scope_chapter_id", edge.scope_chapter_id)
        if source.scope_chapter_id != edge_scope or target.scope_chapter_id != edge_scope:
            raise HTTPException(status_code=422, detail="图谱连线两端必须属于当前章节")
        values["source_node_id"] = source.id
        values["target_node_id"] = target.id
    for key, value in values.items():
        setattr(edge, key, value)
    edge.version += 1
    _touch(project)
    _audit(db, current_user, project, "story_graph.edge_updated", edge)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="图谱连线更新冲突") from exc
    db.refresh(edge)
    return StoryGraphEdgeRead.model_validate(edge)


@router.delete("/projects/{project_id}/story-graph/edges/{edge_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_edge(
    project_id: str,
    edge_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    project = require_project(db, project_id, current_user)
    edge = _edge(db, project, edge_id)
    db.delete(edge)
    _touch(project)
    _audit(db, current_user, project, "story_graph.edge_deleted", edge)
    db.commit()


@router.get("/projects/{project_id}/story-graph/layout", response_model=StoryGraphLayoutRead)
def get_layout(
    project_id: str,
    chapter_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StoryGraphLayoutRead:
    project = require_project(db, project_id, current_user)
    scope_chapter_id = _scope_chapter(db, project, chapter_id)
    layout = db.scalar(
        select(StoryGraphLayout).where(
            StoryGraphLayout.project_id == project.id,
            StoryGraphLayout.scope_chapter_id == scope_chapter_id,
        )
    )
    if layout is None:
        raise HTTPException(status_code=404, detail="当前项目还没有保存图谱布局")
    return StoryGraphLayoutRead.model_validate(layout)


@router.put("/projects/{project_id}/story-graph/layout", response_model=StoryGraphLayoutRead)
@router.patch("/projects/{project_id}/story-graph/layout", response_model=StoryGraphLayoutRead)
def save_layout(
    project_id: str,
    payload: StoryGraphLayoutUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StoryGraphLayoutRead:
    project = require_project(db, project_id, current_user)
    scope_chapter_id = _scope_chapter(db, project, payload.scope_chapter_id)
    layout = db.scalar(
        select(StoryGraphLayout)
        .where(
            StoryGraphLayout.project_id == project.id,
            StoryGraphLayout.scope_chapter_id == scope_chapter_id,
        )
        .with_for_update()
    )
    if layout is None:
        if payload.expected_version not in (None, 0, 1):
            raise HTTPException(status_code=409, detail="图谱布局版本冲突")
        layout = StoryGraphLayout(
            project_id=project.id,
            scope_chapter_id=scope_chapter_id,
            layout_json=payload.layout_json,
        )
        db.add(layout)
    else:
        if payload.expected_version is not None and payload.expected_version != layout.version:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "graph_layout_conflict",
                    "message": "图谱布局已被其他窗口更新",
                    "expected_version": payload.expected_version,
                    "actual_version": layout.version,
                },
            )
        layout.layout_json = payload.layout_json
        layout.version += 1
    db.flush()
    _audit(db, current_user, project, "story_graph.layout_saved", layout)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="图谱布局保存冲突") from exc
    db.refresh(layout)
    return StoryGraphLayoutRead.model_validate(layout)
