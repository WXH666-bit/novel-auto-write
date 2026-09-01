"""Tenant-scoped, explainable full-text search backends.

SQLite keeps its compact FTS5 projection for the Windows desktop build.  The
production MySQL build stores the same accepted/confirmed projection in a
normal table and lets the migration add an ngram FULLTEXT index.  Neither
backend ever indexes pending drafts or unconfirmed canon.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


def _sqlite_fts_tables(connection: Any) -> None:
    chapter_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(chapter_fts)").fetchall()
    }
    if chapter_columns and ({"revision_id", "owner_id"} - chapter_columns):
        connection.exec_driver_sql("DROP TABLE chapter_fts")
    canon_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(canon_fts)").fetchall()
    }
    if canon_columns and "owner_id" not in canon_columns:
        connection.exec_driver_sql("DROP TABLE canon_fts")
    connection.exec_driver_sql(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS chapter_fts USING fts5(
            owner_id UNINDEXED,
            chapter_id UNINDEXED,
            revision_id UNINDEXED,
            project_id UNINDEXED,
            title,
            content,
            summary,
            tokenize='unicode61'
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS canon_fts USING fts5(
            owner_id UNINDEXED,
            canon_item_id UNINDEXED,
            project_id UNINDEXED,
            item_key,
            value,
            source_excerpt,
            tokenize='unicode61'
        )
        """
    )


def ensure_search_schema(engine: Engine) -> None:
    """Create dialect-specific derived search structures idempotently."""

    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            _sqlite_fts_tables(connection)
        return
    if engine.dialect.name == "mysql":
        # MySQL does not expose the ngram parser through SQLAlchemy's portable
        # Index API.  Duplicate-index error 1061 is harmless on a restart.
        with engine.begin() as connection:
            try:
                connection.execute(
                    text(
                        "CREATE FULLTEXT INDEX ft_search_documents_ngram "
                        "ON search_documents (title, content) WITH PARSER ngram"
                    )
                )
            except SQLAlchemyError as exc:
                original = getattr(exc, "orig", None)
                code = original.args[0] if original and getattr(original, "args", None) else None
                if code != 1061:
                    raise


def rebuild_search_index(
    session: Session | None = None,
    *,
    engine: Engine | None = None,
    owner_id: str | None = None,
    project_id: str | None = None,
) -> None:
    """Rebuild derived search rows, optionally for one tenant or project."""

    from ..db import SessionLocal
    from ..db import engine as default_engine
    from ..models import CanonItem, Chapter, ChapterRevision, Project, SearchDocument

    active_engine = engine or (session.get_bind() if session is not None else default_engine)
    own_session = session is None
    if session is None:
        session = SessionLocal(bind=active_engine)
    assert session is not None
    ensure_search_schema(active_engine)

    project_stmt = select(Project)
    if owner_id is not None:
        project_stmt = project_stmt.where(Project.owner_id == owner_id)
    if project_id is not None:
        project_stmt = project_stmt.where(Project.id == project_id)
    projects = session.scalars(project_stmt).all()
    project_ids = [str(project.id) for project in projects]
    if not project_ids:
        if own_session:
            session.close()
        return
    owners = {str(project.id): str(project.owner_id) for project in projects}

    try:
        if active_engine.dialect.name == "sqlite":
            with active_engine.begin() as connection:
                for pid in project_ids:
                    connection.exec_driver_sql("DELETE FROM chapter_fts WHERE project_id = ?", (pid,))
                    connection.exec_driver_sql("DELETE FROM canon_fts WHERE project_id = ?", (pid,))
                rows = session.execute(
                    select(Chapter, ChapterRevision)
                    .join(ChapterRevision, ChapterRevision.id == Chapter.accepted_revision_id)
                    .where(Chapter.project_id.in_(project_ids))
                ).all()
                for chapter, revision in rows:
                    connection.exec_driver_sql(
                        "INSERT INTO chapter_fts(owner_id, chapter_id, revision_id, project_id, title, content, summary) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            owners[str(chapter.project_id)],
                            str(chapter.id),
                            str(revision.id),
                            str(chapter.project_id),
                            chapter.title or "",
                            revision.content or "",
                            chapter.summary or "",
                        ),
                    )
                canon_rows = session.scalars(
                    select(CanonItem).where(
                        CanonItem.project_id.in_(project_ids),
                        CanonItem.status.in_(("confirmed", "active", "已确认")),
                    )
                ).all()
                for item in canon_rows:
                    connection.exec_driver_sql(
                        "INSERT INTO canon_fts(owner_id, canon_item_id, project_id, item_key, value, source_excerpt) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            owners[str(item.project_id)],
                            str(item.id),
                            str(item.project_id),
                            item.key or "",
                            item.value_text or "",
                            item.source_excerpt or "",
                        ),
                    )
            return

        session.execute(delete(SearchDocument).where(SearchDocument.project_id.in_(project_ids)))
        chapter_rows = session.execute(
            select(Chapter, ChapterRevision)
            .join(ChapterRevision, ChapterRevision.id == Chapter.accepted_revision_id)
            .where(Chapter.project_id.in_(project_ids))
        ).all()
        for chapter, revision in chapter_rows:
            session.add(
                SearchDocument(
                    owner_id=owners[str(chapter.project_id)],
                    project_id=chapter.project_id,
                    source_type="chapter",
                    source_id=chapter.id,
                    revision_id=revision.id,
                    title=chapter.title or "",
                    content="\n".join(filter(None, (revision.content, chapter.summary))),
                )
            )
        canon_rows = session.scalars(
            select(CanonItem).where(
                CanonItem.project_id.in_(project_ids),
                CanonItem.status.in_(("confirmed", "active", "已确认")),
            )
        ).all()
        for item in canon_rows:
            session.add(
                SearchDocument(
                    owner_id=owners[str(item.project_id)],
                    project_id=item.project_id,
                    source_type="canon",
                    source_id=item.id,
                    title=item.key or "",
                    content="\n".join(filter(None, (item.value_text, item.source_excerpt))),
                )
            )
        session.commit()
    finally:
        if own_session:
            session.close()


def purge_project_search(session: Session, *, owner_id: str, project_id: str) -> None:
    """Remove every derived search row for one tenant project.

    SQLite FTS virtual tables do not participate in the relational foreign-key
    cascade, so deleting a project without this explicit purge would leave its
    accepted prose in the database file.  The caller owns the transaction;
    this helper intentionally does not commit.
    """

    active_engine = session.get_bind()
    if active_engine.dialect.name == "sqlite":
        session.execute(
            text(
                "DELETE FROM chapter_fts "
                "WHERE owner_id = :owner_id AND project_id = :project_id"
            ),
            {"owner_id": owner_id, "project_id": project_id},
        )
        session.execute(
            text(
                "DELETE FROM canon_fts "
                "WHERE owner_id = :owner_id AND project_id = :project_id"
            ),
            {"owner_id": owner_id, "project_id": project_id},
        )
        return

    from ..models import SearchDocument

    session.execute(
        delete(SearchDocument).where(
            SearchDocument.owner_id == owner_id,
            SearchDocument.project_id == project_id,
        )
    )


def search_accepted_chapters(
    session: Session,
    *,
    owner_id: str,
    project_id: str,
    query: str,
    limit: int = 8,
) -> list[tuple[str, str, str]]:
    """Return accepted chapter excerpts with mandatory tenant predicates."""

    query = " ".join(query.split())[:256]
    if not query:
        return []
    try:
        if session.get_bind().dialect.name == "sqlite":
            rows = session.execute(
                text(
                    "SELECT chapter_id, revision_id, content FROM chapter_fts "
                    "WHERE chapter_fts MATCH :query AND owner_id = :owner_id "
                    "AND project_id = :project_id LIMIT :limit"
                ),
                {
                    "query": query,
                    "owner_id": owner_id,
                    "project_id": project_id,
                    "limit": limit,
                },
            ).all()
        else:
            rows = session.execute(
                text(
                    "SELECT source_id, revision_id, content FROM search_documents "
                    "WHERE owner_id = :owner_id AND project_id = :project_id "
                    "AND source_type = 'chapter' "
                    "AND MATCH(title, content) AGAINST (:query IN BOOLEAN MODE) "
                    "ORDER BY MATCH(title, content) AGAINST (:query IN BOOLEAN MODE) DESC "
                    "LIMIT :limit"
                ),
                {
                    "query": query,
                    "owner_id": owner_id,
                    "project_id": project_id,
                    "limit": limit,
                },
            ).all()
        return [(str(row[0]), str(row[1]), str(row[2] or "")) for row in rows]
    except SQLAlchemyError:
        return []


__all__ = [
    "ensure_search_schema",
    "purge_project_search",
    "rebuild_search_index",
    "search_accepted_chapters",
]
