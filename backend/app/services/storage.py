"""Recoverable removal of tenant-owned filesystem data."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..config import DATA_DIR

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


def _safe_id(value: str) -> str:
    value = str(value)
    if not _SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError("存储标识无效")
    return value


def _under(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved_root not in resolved.parents:
        raise ValueError("租户存储路径越过数据目录")
    return resolved


@dataclass(slots=True)
class StorageQuarantine:
    """Directories moved aside until the matching database commit succeeds."""

    root: Path | None = None
    moves: list[tuple[Path, Path]] = field(default_factory=list)

    def restore(self) -> None:
        for original, staged in reversed(self.moves):
            if not staged.exists() or original.exists():
                continue
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged), str(original))
        if self.root and self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def finalize(self) -> None:
        if self.root and self.root.exists():
            # The database commit is already authoritative at this point.
            # Keep the HTTP operation idempotent even if antivirus software
            # briefly holds a file; the quarantined path is not user-addressable.
            shutil.rmtree(self.root, ignore_errors=True)


@dataclass(slots=True)
class ImportStorageMigration:
    """Filesystem side of a recoverable legacy-import relocation."""

    created: list[Path] = field(default_factory=list)
    legacy_sources: set[Path] = field(default_factory=set)
    upload_root: Path | None = None

    def restore(self) -> None:
        for destination in reversed(self.created):
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        if self.upload_root is None:
            return
        for destination in sorted(
            self.created, key=lambda item: len(item.parts), reverse=True
        ):
            parent = destination.parent
            while parent != self.upload_root and self.upload_root in parent.parents:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

    def finalize(self) -> None:
        for source in self.legacy_sources:
            try:
                source.unlink(missing_ok=True)
            except OSError:
                continue
        if self.upload_root is None:
            return
        # Remove only empty legacy directories below uploads; tenant content
        # and unrelated operator files are never recursively deleted here.
        for source in sorted(self.legacy_sources, key=lambda item: len(item.parts), reverse=True):
            parent = source.parent
            while parent != self.upload_root and self.upload_root in parent.parents:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def migrate_import_storage(session: object) -> ImportStorageMigration:
    """Copy old flat imports into ``uploads/<owner>/<project>/<hash>``.

    Database paths are updated in the caller's transaction.  The caller must
    call ``restore`` after rollback or ``finalize`` after commit, mirroring the
    account-deletion quarantine contract.
    """

    from sqlalchemy import select

    from ..models import ImportSource, Project

    upload_root = (DATA_DIR / "uploads").resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    migration = ImportStorageMigration(upload_root=upload_root)
    try:
        rows = session.execute(
            select(ImportSource, Project.owner_id)
            .join(Project, Project.id == ImportSource.project_id)
            .order_by(ImportSource.created_at, ImportSource.id)
        ).all()
        tenant_destinations: set[Path] = set()
        for source, owner_id in rows:
            owner = _safe_id(str(owner_id))
            project = _safe_id(str(source.project_id))
            source_hash = str(source.source_hash or "").lower()
            if not _SHA256.fullmatch(source_hash):
                raise ValueError("导入来源哈希无效，无法迁移原始文件")
            target_name = f"{owner}/{project}/{source_hash}.source"
            destination = _under(upload_root, upload_root / target_name)
            tenant_destinations.add(destination)

            candidates: list[Path] = []
            stored = Path(str(source.stored_name or ""))
            if str(stored) and not stored.is_absolute():
                candidate = (upload_root / stored).resolve()
                if upload_root in candidate.parents:
                    candidates.append(candidate)
            flat_candidate = _under(upload_root, upload_root / f"{source_hash}.source")
            if flat_candidate not in candidates:
                candidates.append(flat_candidate)

            if destination.is_file():
                if _sha256_file(destination) != source_hash:
                    raise ValueError("租户导入文件与数据库哈希不一致")
            else:
                legacy = next((item for item in candidates if item.is_file()), None)
                if legacy is None:
                    # Keep the legacy reference so an operator can restore a
                    # missing source from backup; story rows remain usable.
                    continue
                if _sha256_file(legacy) != source_hash:
                    raise ValueError("旧导入文件与数据库哈希不一致")
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(
                    f".{destination.name}.{uuid.uuid4().hex}.tmp"
                )
                try:
                    shutil.copyfile(legacy, temporary)
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
                migration.created.append(destination)

            for candidate in candidates:
                if candidate != destination and candidate.is_file():
                    migration.legacy_sources.add(candidate)
            source.stored_name = target_name
            source.byte_size = destination.stat().st_size
        migration.legacy_sources.difference_update(tenant_destinations)
        return migration
    except Exception:
        # The caller cannot receive a journal from a function that raises.
        # Undo already-copied targets here; its database transaction remains
        # responsible for rolling back ``stored_name``/``byte_size`` changes.
        migration.restore()
        raise


def stage_storage_deletion(
    *, owner_id: str, project_id: str | None = None
) -> StorageQuarantine:
    """Atomically hide user/project uploads and MySQL ZIP backups.

    The caller must invoke ``restore`` when its database transaction fails and
    ``finalize`` after it commits.  Global SQLite snapshots are intentionally
    not touched because one snapshot can contain several users.
    """

    owner = _safe_id(owner_id)
    project = _safe_id(project_id) if project_id is not None else None
    data_root = DATA_DIR.resolve()
    targets: list[tuple[str, Path]] = []
    for category in ("uploads", "backups"):
        category_root = (data_root / category).resolve()
        candidate = category_root / owner
        if project is not None:
            candidate = candidate / project
        targets.append((category, _under(category_root, candidate)))

    existing = [(name, path) for name, path in targets if path.is_dir()]
    if not existing:
        return StorageQuarantine()

    quarantine_root = _under(
        data_root,
        data_root / ".account-deletions" / str(uuid.uuid4()),
    )
    quarantine_root.mkdir(parents=True, exist_ok=False)
    quarantine = StorageQuarantine(root=quarantine_root)
    try:
        for category, original in existing:
            staged = quarantine_root / category
            shutil.move(str(original), str(staged))
            quarantine.moves.append((original, staged))
    except Exception:
        quarantine.restore()
        raise
    return quarantine


__all__ = [
    "ImportStorageMigration",
    "StorageQuarantine",
    "migrate_import_storage",
    "stage_storage_deletion",
]
