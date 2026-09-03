"""Explainable, budgeted context construction for generation stages."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from .common import safe_text, token_estimate


@dataclass(slots=True)
class ContextSource:
    kind: str
    source_id: str | int | None
    label: str
    excerpt: str
    start: int | None = None
    end: int | None = None
    chapter_id: str | None = None
    revision_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_id": self.source_id,
            "label": self.label,
            "excerpt": self.excerpt,
            "start": self.start,
            "end": self.end,
            "chapter_id": self.chapter_id,
            "revision_id": self.revision_id,
        }


def _value(instance: Any, field: str, default: Any = None) -> Any:
    return getattr(instance, field, default)


def _source(
    kind: str,
    item: Any,
    label: str,
    value: Any,
    *,
    start: int | None = None,
    end: int | None = None,
    chapter_id: str | None = None,
    revision_id: str | None = None,
) -> ContextSource:
    return ContextSource(
        kind,
        _value(item, "id"),
        label,
        safe_text(value),
        start,
        end,
        chapter_id or _value(item, "source_chapter_id"),
        revision_id or _value(item, "source_revision_id"),
    )


def _constraint_items(value: Any) -> list[Any]:
    """Return project constraint values as displayable, non-empty items.

    The current schema stores these fields as JSON lists, while legacy callers
    and small test doubles may still provide a single string or another scalar.
    Treat strings as one item (rather than iterating characters) and preserve
    structured values for the JSON-safe ``safe_text`` formatter below.
    """

    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if safe_text(item).strip()]
    rendered = safe_text(value).strip()
    return [value] if rendered else []


def _constraint_excerpt(label: str, value: Any) -> str:
    """Render a project constraint list with an explicit, stable structure."""

    items = _constraint_items(value)
    if not items:
        return ""
    lines = [f"{label}："]
    for index, item in enumerate(items, start=1):
        rendered = safe_text(item).strip()
        if rendered:
            lines.append(f"{index}. {rendered}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _current_revision(session: Any, chapter: Any, *, accepted_only: bool = False) -> Any | None:
    field = "accepted_revision_id" if accepted_only else "current_revision_id"
    revision_id = _value(chapter, field)
    if not revision_id:
        return None
    from ..models import ChapterRevision

    return session.scalar(select(ChapterRevision).where(ChapterRevision.id == revision_id))


def _fts_search(
    session: Any,
    project: Any,
    query: str,
    limit: int = 8,
) -> list[tuple[Any, Any, str]]:
    """Search accepted text with explicit tenant/project predicates."""

    from ..models import Chapter, ChapterRevision
    from .search import search_accepted_chapters

    query = " ".join(query.split())[:256]
    project_id = str(project.id)
    if query:
        rows = search_accepted_chapters(
            session,
            owner_id=str(project.owner_id),
            project_id=project_id,
            query=query,
            limit=limit,
        )
        if rows:
            return [(row[0], row[1], safe_text(row[2])) for row in rows]

    revisions = (
        session.execute(
            select(Chapter.id, Chapter.accepted_revision_id, ChapterRevision.content)
            .join(ChapterRevision, ChapterRevision.id == Chapter.accepted_revision_id)
            .where(
                Chapter.project_id == project_id,
                Chapter.accepted_revision_id.is_not(None),
            )
            .order_by(Chapter.sort_order.desc(), Chapter.chapter_number.desc())
            .limit(max(limit * 4, 16))
        )
    ).all()
    terms = [term.lower() for term in query.split() if term]
    scored: list[tuple[int, Any, Any, str]] = []
    for chapter_id, revision_id, content in revisions:
        body = safe_text(content)
        score = sum(body.lower().count(term) for term in terms) if terms else 0
        scored.append((score, chapter_id, revision_id, body))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [(chapter_id, revision_id, body) for _, chapter_id, revision_id, body in scored[:limit]]


def build_context(
    session: Any,
    project: Any,
    chapter: Any | None = None,
    *,
    budget: int | None = None,
    query: str = "",
    recent_chapter_content_count: int = 2,
    include_change_overlay: bool = False,
    include_search_chapter_bodies: bool = False,
) -> dict[str, Any]:
    """Build a context snapshot with non-droppable hard/current sections.

    Every section is a citation-bearing source.  The snapshot is plain JSON and
    can therefore be persisted verbatim in ``GenerationRun.context_snapshot``.
    """

    from ..models import (
        AuditLog,
        CanonItem,
        Chapter,
        ChapterRevision,
        Character,
        PlotThread,
        StoryGraphEdge,
        StoryGraphNode,
        StorySummary,
        TimelineEvent,
    )

    budget = max(512, int(budget or 32768))
    sources: list[ContextSource] = []
    mandatory: list[ContextSource] = []
    optional: list[ContextSource] = []

    # ``description`` and ``hard_constraints`` are the first-release storage for
    # the story bible; newer schemas may add a dedicated story_bible column.
    story_bible = safe_text(_value(project, "story_bible", ""))
    if not story_bible:
        story_bible = safe_text(_value(project, "description", ""))
    hard_constraints = _value(project, "hard_constraints", [])
    hard_excerpt = _constraint_excerpt("硬约束", hard_constraints)
    if hard_excerpt:
        # Keep hard constraints in the mandatory story-bible section so they
        # are never trimmed when the context budget is exhausted.
        story_bible = (story_bible + "\n" + hard_excerpt).strip()
    outline = safe_text(_value(project, "outline", ""))
    if story_bible:
        mandatory.append(_source("project", project, "故事圣经", story_bible))
    if outline:
        mandatory.append(_source("outline", project, "当前大纲", outline))

    project_summary = session.scalar(
        select(StorySummary)
        .where(
            StorySummary.project_id == project.id,
            StorySummary.scope == "project",
            StorySummary.status == "current",
        )
        .order_by(StorySummary.updated_at.desc())
    )
    if project_summary is not None and project_summary.summary_text:
        mandatory.append(
            _source("story_summary", project_summary, "小说当前总览", project_summary.summary_text)
        )

    # A completed memory snapshot is replaced atomically, so it is safe to
    # keep using it while the next build runs.  Accepted edits made after that
    # snapshot form a small, explicit overlay.  This closes the otherwise
    # dangerous gap where an Agent would see a reliable old summary but miss
    # the author's most recent confirmed decisions.
    if include_change_overlay and project_summary is not None:
        changed_chapters = session.scalars(
            select(Chapter)
            .where(
                Chapter.project_id == project.id,
                Chapter.accepted_revision_id.is_not(None),
                Chapter.updated_at > project_summary.updated_at,
            )
            .order_by(Chapter.sort_order.desc(), Chapter.chapter_number.desc())
            .limit(10)
        ).all()
        for changed in changed_chapters:
            revision = _current_revision(session, changed, accepted_only=True)
            if revision is None or not safe_text(revision.content).strip():
                continue
            mandatory.append(
                _source(
                    "memory_overlay",
                    changed,
                    f"摘要版本后的第{changed.chapter_number}章确认正文",
                    revision.content,
                    chapter_id=changed.id,
                    revision_id=revision.id,
                )
            )
        recent_audits = session.scalars(
            select(AuditLog)
            .where(
                AuditLog.project_id == project.id,
                AuditLog.created_at > project_summary.updated_at,
            )
            .order_by(AuditLog.created_at.desc())
            .limit(30)
        ).all()
        audit_lines = []
        for audit in reversed(recent_audits):
            if audit.action in {
                "assistant.chapter_draft_written",
                "memory.queued",
                "memory.completed",
            }:
                continue
            detail = safe_text(audit.after_json or {})
            audit_lines.append(
                f"{audit.action}｜{audit.entity_type or 'story'}#{audit.entity_id or ''}"
                + (f"｜{detail[:900]}" if detail else "")
            )
        if audit_lines:
            mandatory.append(
                ContextSource(
                    "memory_overlay",
                    project.id,
                    "摘要版本后的已确认变更账本",
                    "\n".join(audit_lines),
                )
            )

    # Project-level generation requirements are durable story settings, not
    # optional search context.  Keep both lists mandatory and label them
    # separately so every generation role can distinguish positive and
    # negative requirements.  They remain in the project row after a run or
    # review decision; users can edit or clear them explicitly.
    for label, values in (
        ("必须发生", _value(project, "must_happen", [])),
        ("禁止发生", _value(project, "must_not_happen", [])),
    ):
        excerpt = _constraint_excerpt(label, values)
        if excerpt:
            mandatory.append(_source("project_constraint", project, label, excerpt))

    canon_rows = (
        (
            session.execute(
                select(CanonItem)
                .where(CanonItem.project_id == project.id)
                .order_by(
                    CanonItem.is_hard.desc(), CanonItem.canon_version.desc(), CanonItem.id.desc()
                )
            )
        )
        .scalars()
        .all()
    )
    related_terms = set(query.split())
    for item in canon_rows:
        status = str(_value(item, "status", "confirmed"))
        if status not in {"confirmed", "active", "已确认"}:
            continue
        aliases = safe_text(_value(item, "aliases", ""))
        key = safe_text(_value(item, "key", ""))
        value = safe_text(_value(item, "value_text", _value(item, "value", "")))
        line = f"{_value(item, 'category', '')}｜{key}：{value}（别名：{aliases}）"
        citation = _source(
            "canon",
            item,
            "正典设定",
            line,
            start=_value(item, "source_start"),
            end=_value(item, "source_end"),
        )
        is_hard = bool(_value(item, "is_hard", False))
        # Hard rules and current status are never trimmed.  A query match is
        # promoted to mandatory as it is likely needed for this scene.
        if is_hard or (related_terms and any(term and term in line for term in related_terms)):
            mandatory.append(citation)
        else:
            optional.append(citation)

    # First-class character cards and graph relations are authoritative only
    # after confirmation.  Agent/analysis proposals live in ChangeSet and are
    # intentionally absent from these queries.
    characters = session.scalars(
        select(Character)
        .where(
            Character.project_id == project.id,
            Character.status.in_(("active", "confirmed", "current")),
        )
        .order_by(Character.name)
    ).all()
    character_names = {str(item.id): item.name for item in characters}
    for item in characters:
        details = [
            f"姓名：{item.name}",
            f"别名：{safe_text(item.aliases)}" if item.aliases else "",
            f"定位：{safe_text(item.role)}" if item.role else "",
            f"外貌：{safe_text(item.appearance)}" if item.appearance else "",
            f"性格：{safe_text(item.personality)}" if item.personality else "",
            f"背景：{safe_text(item.background)}" if item.background else "",
            f"目标：{safe_text(item.goals)}" if item.goals else "",
            f"动机：{safe_text(item.motivation)}" if item.motivation else "",
            f"成长弧：{safe_text(item.arc)}" if item.arc else "",
            f"说话风格：{safe_text(item.voice)}" if item.voice else "",
            f"自定义：{safe_text(item.custom_fields)}" if item.custom_fields else "",
        ]
        optional.append(
            _source(
                "character",
                item,
                "已确认人物卷宗",
                "；".join(value for value in details if value),
            )
        )

    nodes = {
        str(node.id): node
        for node in session.scalars(
            select(StoryGraphNode).where(
                StoryGraphNode.project_id == project.id,
                StoryGraphNode.status.in_(("active", "confirmed", "current")),
            )
        ).all()
    }
    for edge in session.scalars(
        select(StoryGraphEdge).where(
            StoryGraphEdge.project_id == project.id,
            StoryGraphEdge.status.in_(("active", "confirmed", "current")),
        )
    ).all():
        source_node = nodes.get(str(edge.source_node_id))
        target_node = nodes.get(str(edge.target_node_id))
        if source_node is None or target_node is None:
            continue
        source_label = character_names.get(
            str(getattr(source_node, "character_id", None)), source_node.label
        )
        target_label = character_names.get(
            str(getattr(target_node, "character_id", None)), target_node.label
        )
        direction = "→" if edge.directed else "↔"
        optional.append(
            _source(
                "story_relation",
                edge,
                "已确认故事关系",
                f"{source_label}{direction}{target_label}｜{edge.label or edge.relation_type}",
            )
        )

    all_chapters = (
        (
            session.execute(
                select(Chapter)
                .where(Chapter.project_id == project.id)
                .order_by(Chapter.sort_order.desc(), Chapter.chapter_number.desc())
            )
        )
        .scalars()
        .all()
    )
    current_ordinal = (
        _value(chapter, "sort_order", _value(chapter, "chapter_number", 10**9))
        if chapter is not None
        else 10**9
    )
    previous = [
        item
        for item in all_chapters
        if _value(item, "sort_order", _value(item, "chapter_number", 0)) < current_ordinal
        and _value(item, "accepted_revision_id")
    ]
    recent_count = max(0, min(10, int(recent_chapter_content_count)))
    # The current chapter and the requested number of previous accepted
    # chapters are high priority.  Agent conversations request ten so a new
    # chapter thread has the agreed near-history; other generation paths keep
    # the conservative default of two.
    for item in ([chapter] if chapter is not None else []) + previous[:recent_count]:
        if item is None:
            continue
        is_target = chapter is not None and item.id == chapter.id
        revision = _current_revision(session, item, accepted_only=not is_target)
        body = safe_text(_value(revision, "content", "")) if revision else ""
        summary = safe_text(_value(item, "summary", ""))
        if body:
            mandatory.append(
                _source(
                    "chapter",
                    item,
                    f"第{_value(item, 'chapter_number', '?')}章正文",
                    body,
                    chapter_id=item.id,
                    revision_id=revision.id,
                )
            )
        elif summary:
            mandatory.append(
                _source(
                    "summary",
                    item,
                    f"第{_value(item, 'chapter_number', '?')}章摘要",
                    summary,
                    chapter_id=item.id,
                    revision_id=_value(item, "accepted_revision_id"),
                )
            )

    # Summaries of older chapters are useful but are the first data to trim.
    for item in previous[recent_count:]:
        summary = safe_text(_value(item, "summary", ""))
        if summary and _value(item, "summary_status", "current") == "current":
            optional.append(
                _source(
                    "summary",
                    item,
                    f"第{_value(item, 'chapter_number', '?')}章摘要",
                    summary,
                    chapter_id=item.id,
                    revision_id=_value(item, "accepted_revision_id"),
                )
            )

    for thread in session.scalars(
        select(PlotThread).where(
            PlotThread.project_id == project.id,
            PlotThread.status.in_(("active", "dormant")),
        )
    ).all():
        extra = _value(thread, "extra", {}) or {}
        thread_text = (
            f"{_value(thread, 'thread_type', '剧情线')}｜{_value(thread, 'name', '')}："
            f"{safe_text(_value(thread, 'description', ''))}；"
            f"下一拍：{safe_text(extra.get('next_beat', '未规划'))}；"
            f"埋设：{safe_text(_value(thread, 'planted_at', ''))}；"
            f"回收：{safe_text(_value(thread, 'payoff_at', ''))}"
        )
        mandatory.append(_source("plot_thread", thread, "活跃剧情线", thread_text))

    for event in session.scalars(
        select(TimelineEvent)
        .where(
            TimelineEvent.project_id == project.id,
            TimelineEvent.needs_review.is_(False),
            TimelineEvent.status.in_(("confirmed", "current", "planned")),
        )
        .order_by(TimelineEvent.sequence.desc())
        .limit(20)
    ).all():
        event_text = (
            f"{safe_text(event.story_time)}｜{event.title}："
            f"{safe_text(event.description)}（{event.status}）"
        )
        optional.append(
            _source(
                "timeline",
                event,
                "时间线事件",
                event_text,
                chapter_id=event.chapter_id,
                revision_id=event.source_revision_id,
            )
        )

    # Structured/entity search makes distant callbacks discoverable without
    # pulling all old prose into the prompt.
    search_query = query or safe_text(_value(project, "description", ""))[:120]
    search_hits = _fts_search(
        session,
        project,
        search_query,
    )
    included_chapter_ids = {
        str(item.chapter_id)
        for item in mandatory
        if item.chapter_id
    }
    if include_search_chapter_bodies:
        for chapter_id, revision_id, _excerpt in search_hits[:10]:
            if str(chapter_id) in included_chapter_ids:
                continue
            revision = session.scalar(
                select(ChapterRevision).where(
                    ChapterRevision.id == str(revision_id),
                    ChapterRevision.chapter_id == str(chapter_id),
                )
            )
            matched_chapter = session.scalar(
                select(Chapter).where(
                    Chapter.id == str(chapter_id),
                    Chapter.project_id == project.id,
                )
            )
            if revision is None or matched_chapter is None:
                continue
            optional.append(
                _source(
                    "retrieved_chapter",
                    matched_chapter,
                    f"检索命中的第{matched_chapter.chapter_number}章完整正文",
                    revision.content,
                    chapter_id=matched_chapter.id,
                    revision_id=revision.id,
                )
            )
            included_chapter_ids.add(str(chapter_id))
    for chapter_id, revision_id, excerpt in search_hits:
        if not excerpt:
            continue
        optional.append(
            ContextSource(
                "search",
                revision_id,
                "全文检索片段",
                excerpt[:1800],
                chapter_id=str(chapter_id),
                revision_id=str(revision_id),
            )
        )

    def size(items: Iterable[ContextSource]) -> int:
        return sum(token_estimate(item.excerpt) for item in items)

    mandatory_size = size(mandatory)
    # A pathological hard canon must still be retained; report the overflow to
    # the UI instead of silently dropping a hard rule.
    selected: list[ContextSource] = list(mandatory)
    remaining = max(0, budget - mandatory_size)
    omitted = 0
    for item in optional:
        cost = token_estimate(item.excerpt)
        if cost <= remaining:
            selected.append(item)
            remaining -= cost
        else:
            omitted += 1

    sources = selected
    blocks = []
    for item in sources:
        blocks.append(
            f"【来源:{item.kind}#{item.source_id} {item.label} {item.start or ''}-{item.end or ''}】\n{item.excerpt}"
        )
    text_value = "\n\n".join(blocks)
    return {
        "text": text_value,
        "sources": [item.as_dict() for item in sources],
        "token_count": token_estimate(text_value),
        "budget": budget,
        "omitted_optional_sources": omitted,
        "mandatory_token_count": mandatory_size,
    }
