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
) -> dict[str, Any]:
    """Build a context snapshot with non-droppable hard/current sections.

    Every section is a citation-bearing source.  The snapshot is plain JSON and
    can therefore be persisted verbatim in ``GenerationRun.context_snapshot``.
    """

    from ..models import CanonItem, Chapter, PlotThread, TimelineEvent

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
    # The current chapter and the last two accepted chapters are high priority.
    for item in ([chapter] if chapter is not None else []) + previous[:2]:
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
    for item in previous[2:]:
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
    for chapter_id, revision_id, excerpt in _fts_search(
        session,
        project,
        search_query,
    ):
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
