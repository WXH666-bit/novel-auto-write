"""TXT/Markdown import and chapter segmentation.

The importer deliberately works on bytes first.  This keeps the decoding decision
and the original hash deterministic, which is useful when an import is resumed or
replayed after an application restart.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from sqlalchemy import func, select

CHAPTER_RE = re.compile(
    r"^\s*(?P<title>(?:第\s*[0-9０-９一二三四五六七八九十百千万零〇两〇]+\s*"
    r"(?:章|节|回|卷|部|篇|集)(?:\s*[^\r\n]*)?|"
    r"(?:序章|楔子|引子|尾声|后记|前言|番外(?:\s*[^\r\n]*)?)))\s*$",
    re.IGNORECASE,
)
MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*#*\s*$")
SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.I | re.S)
HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


@dataclass(slots=True)
class ImportChapter:
    ordinal: int
    title: str
    content: str
    source_start: int
    source_end: int
    content_hash: str
    source_type: str = "import"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "title": self.title,
            "content": self.content,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "content_hash": self.content_hash,
            "source_type": self.source_type,
        }


@dataclass(slots=True)
class ImportPreview:
    filename: str
    encoding: str
    source_hash: str
    chapters: list[ImportChapter] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "encoding": self.encoding,
            "source_hash": self.source_hash,
            "chapters": [chapter.as_dict() for chapter in self.chapters],
            "warnings": self.warnings,
        }


def content_hash(value: str | bytes) -> str:
    """Return a stable SHA-256 hash for imported or generated content."""

    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def detect_encoding(raw: bytes) -> tuple[str, str]:
    """Decode common Chinese novel encodings without silently replacing data.

    UTF-8 (including BOM) is preferred.  GB18030 is intentionally attempted
    before a lossy replacement fallback because it is a superset of GBK/GB2312.
    """

    candidates: list[tuple[str, bytes]] = []
    if raw.startswith(b"\xef\xbb\xbf"):
        candidates.append(("utf-8-sig", raw))
    elif raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        candidates.append(("utf-16", raw))
    candidates.extend((("utf-8", raw), ("gb18030", raw), ("utf-16", raw)))
    for encoding, candidate in candidates:
        try:
            return encoding, candidate.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    # Never fail an import because a legacy file has one malformed byte.  The
    # warning is attached by ``preview_import`` so the user can review it.
    return "utf-8-replaced", raw.decode("utf-8", errors="replace")


def sanitize_markdown(text: str) -> tuple[str, list[str]]:
    """Remove executable/raw HTML from Markdown while retaining readable text."""

    warnings: list[str] = []
    if SCRIPT_RE.search(text):
        warnings.append("已移除 Markdown 中的 script 标签内容")
        text = SCRIPT_RE.sub("", text)
    if HTML_TAG_RE.search(text):
        warnings.append("已移除 Markdown 中的原始 HTML 标签")
        text = HTML_TAG_RE.sub("", text)
    # Keep entities encoded.  Decoding ``&lt;script&gt;`` here would recreate a
    # raw tag for a later Markdown renderer.
    return text.replace("\r\n", "\n").replace("\r", "\n"), warnings


def _heading_title(line: str) -> str | None:
    match = CHAPTER_RE.match(line)
    if match:
        return match.group("title").strip().rstrip("#").strip()
    match = MARKDOWN_HEADING_RE.match(line)
    if match:
        title = match.group("title").strip()
        # Do not treat arbitrary Markdown headings as chapter boundaries unless
        # they look like the conventional chapter labels.
        if CHAPTER_RE.match(title) or re.match(r"^(?:序章|楔子|引子|尾声|后记|番外)", title):
            return title
    return None


def _default_title(filename: str) -> str:
    stem = PurePath(filename or "未命名稿件").stem.strip()
    return stem or "未命名章节"


def split_chapters(text: str, filename: str = "") -> list[ImportChapter]:
    """Split a normalized text into previewable chapters.

    ``source_start``/``source_end`` refer to character offsets in the normalized
    source.  Empty heading-only sections are retained only when they contain
    meaningful body text, preventing accidental empty chapters.
    """

    lines = text.splitlines(keepends=True)
    boundaries: list[tuple[int, str]] = []
    offset = 0
    for line in lines:
        title = _heading_title(line.rstrip("\r\n"))
        if title:
            boundaries.append((offset, title))
        offset += len(line)

    result: list[ImportChapter] = []
    if not boundaries:
        body = text.strip()
        if body:
            start = text.find(body)
            result.append(
                ImportChapter(
                    1, _default_title(filename), body, start, start + len(body), content_hash(body)
                )
            )
        return result

    # Text before the first explicit heading is preserved as a prologue chapter
    # when it is not merely a cover/title line.
    first_offset = boundaries[0][0]
    preamble = text[:first_offset].strip()
    if preamble:
        start = text.find(preamble)
        result.append(
            ImportChapter(1, "序章", preamble, start, start + len(preamble), content_hash(preamble))
        )

    for index, (heading_offset, title) in enumerate(boundaries):
        body_start = heading_offset
        body_end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
        section = text[body_start:body_end]
        section_lines = section.splitlines(keepends=True)
        if section_lines:
            section_body = "".join(section_lines[1:]).strip()
        else:
            section_body = ""
        if not section_body:
            continue
        # Locate the trimmed body exactly, retaining useful source positions.
        local = section.find(section_body, len(section_lines[0]) if section_lines else 0)
        start = body_start + (local if local >= 0 else len(section_lines[0]))
        result.append(
            ImportChapter(
                len(result) + 1,
                title,
                section_body,
                start,
                start + len(section_body),
                content_hash(section_body),
            )
        )

    # If all sections were empty, retain a single editable chapter rather than
    # returning a surprising empty import.
    if not result:
        body = text.strip()
        if body:
            result.append(
                ImportChapter(1, _default_title(filename), body, 0, len(text), content_hash(body))
            )
    return result


def preview_import(raw: bytes, filename: str = "") -> ImportPreview:
    source_hash = content_hash(raw)
    encoding, decoded = detect_encoding(raw)
    normalized, warnings = sanitize_markdown(decoded)
    if encoding == "utf-8-replaced":
        warnings.append("文件不是有效 UTF-8/GB18030，已使用替换字符解码")
    chapters = split_chapters(normalized, filename)
    if not chapters:
        warnings.append("文件没有可导入的正文")
    return ImportPreview(filename or "未命名稿件", encoding, source_hash, chapters, warnings)


def apply_preview_edits(
    preview: ImportPreview,
    edits: Sequence[dict[str, Any]] | None = None,
) -> list[ImportChapter]:
    """Apply user chapter merge/split/rename edits to a preview.

    The API accepts a simple list of chapter objects.  Supplying ``content``
    replaces a chapter (and is useful for splitting); ``merge_with_previous``
    joins it with the prior item.  Unknown fields are ignored intentionally so
    clients can round-trip preview objects.
    """

    source = [chapter.as_dict() for chapter in preview.chapters]
    if edits is not None:
        source = [dict(item) for item in edits]
    output: list[ImportChapter] = []
    for item in source:
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        title = str(item.get("title") or f"第{len(output) + 1}章").strip()
        if item.get("merge_with_previous") and output:
            previous = output[-1]
            merged = f"{previous.content}\n\n{content}".strip()
            output[-1] = ImportChapter(
                previous.ordinal,
                str(item.get("title") or previous.title),
                merged,
                previous.source_start,
                int(item.get("source_end") or previous.source_end),
                content_hash(merged),
            )
            continue
        output.append(
            ImportChapter(
                len(output) + 1,
                title,
                content,
                int(item.get("source_start") or 0),
                int(item.get("source_end") or len(content)),
                content_hash(content),
                str(item.get("source_type") or "import"),
            )
        )
    return output


def _mapped_kwargs(model: Any, values: dict[str, Any]) -> dict[str, Any]:
    """Only pass fields present on a SQLAlchemy model.

    This keeps the service compatible with lightweight test models and with
    migrations that add optional fields over time.
    """

    try:
        names = set(model.__mapper__.attrs.keys())
    except AttributeError:
        names = set(values)
    return {key: value for key, value in values.items() if key in names}


def persist_import(
    session: Any,
    project: Any,
    chapters: Iterable[ImportChapter],
    *,
    source_hash: str | None = None,
    replace_empty_only: bool = False,
) -> list[Any]:
    """Persist an accepted import as immutable chapter/revision pairs.

    This function does not commit; callers can combine it with other changes in
    one transaction.  Existing chapters are left untouched by default, making a
    retried upload safe.  A unique content hash is used when the model exposes
    it, while the chapter/revision pair itself remains the source of truth.
    """

    from ..models import Chapter, ChapterRevision  # imported lazily for tests

    items = list(chapters)
    existing_count = session.scalar(
        select(func.count(Chapter.id)).where(Chapter.project_id == project.id)
    )
    if replace_empty_only and existing_count:
        return []
    if existing_count:
        # A repeated upload is idempotent by content hash.  A genuinely new
        # upload is appended after the current highest chapter number.
        existing_hashes = set(
            session.scalars(
                select(ChapterRevision.content_hash)
                .join(Chapter, Chapter.id == ChapterRevision.chapter_id)
                .where(Chapter.project_id == project.id)
            ).all()
        )
        if items and all(item.content_hash in existing_hashes for item in items):
            return []
        base_ordinal = (
            session.scalar(
                select(func.max(Chapter.chapter_number)).where(Chapter.project_id == project.id)
            )
            or 0
        )
    else:
        base_ordinal = 0
    created: list[Any] = []
    for chapter_data in sorted(items, key=lambda item: item.ordinal):
        ordinal = int(base_ordinal) + int(chapter_data.ordinal)
        chapter = Chapter(
            **_mapped_kwargs(
                Chapter,
                {
                    "project_id": project.id,
                    "ordinal": ordinal,
                    "chapter_number": ordinal,
                    "sort_order": ordinal,
                    "title": chapter_data.title,
                    "status": "confirmed",
                    "summary": "",
                    "source_type": chapter_data.source_type,
                },
            )
        )
        session.add(chapter)
        session.flush()
        revision = ChapterRevision(
            **_mapped_kwargs(
                ChapterRevision,
                {
                    "chapter_id": chapter.id,
                    "revision_no": 1,
                    "revision_number": 1,
                    "content": chapter_data.content,
                    "content_hash": chapter_data.content_hash,
                    "source_type": chapter_data.source_type,
                    "parent_revision_id": None,
                    "extra": {
                        "source_start": chapter_data.source_start,
                        "source_end": chapter_data.source_end,
                        "source_hash": source_hash,
                    },
                },
            )
        )
        session.add(revision)
        session.flush()
        if hasattr(chapter, "current_revision_id"):
            chapter.current_revision_id = revision.id
        if hasattr(chapter, "accepted_revision_id"):
            chapter.accepted_revision_id = revision.id
        if hasattr(chapter, "confirmed_at"):
            from ..models import utcnow

            chapter.confirmed_at = utcnow()
        created.append(chapter)
    if source_hash is not None and hasattr(project, "source_hash"):
        project.source_hash = source_hash
    if created and hasattr(project, "memory_epoch"):
        project.memory_epoch = int(project.memory_epoch or 0) + 1
    session.flush()
    return created
