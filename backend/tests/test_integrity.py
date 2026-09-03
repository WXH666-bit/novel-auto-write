"""Regression tests for the story-memory trust boundary."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.app import cli as cli_module
from backend.app import db as db_module
from backend.app import models
from backend.app.cli import claim_legacy
from backend.app.config import LEGACY_OWNER_ID
from backend.app.db import create_engine_for_url, init_db, rebuild_search_index, run_migrations
from backend.app.routers.canon import confirm_canon, mark_canon_needs_review
from backend.app.routers.chapters import confirm_chapter
from backend.app.services import providers as provider_service
from backend.app.services import storage
from backend.app.services.context import build_context
from backend.app.services.generation import (
    create_generation_run,
    execute_generation,
    recover_incomplete_runs,
)
from backend.app.services.reviews import (
    BlockerError,
    ReviewValidationError,
    accept_review,
    edit_review_draft,
    reaudit_review_bundle,
)
from backend.tests.helpers import FakeProvider, install_fake_provider, seed_tenant


class _MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        self.values[(service, username)] = value

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


def _session(tmp_path, monkeypatch):
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'integrity.sqlite3').as_posix()}")
    init_db(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    user, _ = seed_tenant(db)
    db.info["test_user_id"] = user.id
    install_fake_provider(monkeypatch)
    return engine, db


def test_unaccepted_draft_is_excluded_from_context_and_fts(tmp_path, monkeypatch):
    engine, db = _session(tmp_path, monkeypatch)
    try:
        project = models.Project(owner_id=db.info["test_user_id"], name="记忆边界")
        db.add(project)
        db.flush()
        chapter = models.Chapter(
            project_id=project.id,
            chapter_number=1,
            sort_order=1,
            title="旧港",
            status="confirmed",
        )
        target = models.Chapter(
            project_id=project.id,
            chapter_number=2,
            sort_order=2,
            title="下一章",
            status="draft",
        )
        db.add_all([chapter, target])
        db.flush()
        accepted_text = "这是已经接受的旧港事实。"
        draft_text = "这是绝不能进入记忆的未审核草稿。"
        accepted = models.ChapterRevision(
            chapter_id=chapter.id,
            revision_number=1,
            content=accepted_text,
            content_hash=models.ChapterRevision.hash_content(accepted_text),
        )
        draft = models.ChapterRevision(
            chapter_id=chapter.id,
            revision_number=2,
            content=draft_text,
            content_hash=models.ChapterRevision.hash_content(draft_text),
        )
        db.add_all([accepted, draft])
        db.flush()
        chapter.accepted_revision_id = accepted.id
        chapter.current_revision_id = draft.id
        db.add(
            models.CanonItem(
                project_id=project.id,
                category="secret",
                key="待确认秘密",
                value="不能泄漏",
                value_text="不能泄漏",
                status="pending",
            )
        )
        db.commit()
        rebuild_search_index(db_engine=engine)

        context = build_context(db, project, target, query="旧港")
        assert "已经接受的旧港事实" in context["text"]
        assert "绝不能进入记忆" not in context["text"]
        assert "待确认秘密" not in context["text"]
        indexed = db.execute(text("SELECT revision_id, content FROM chapter_fts")).all()
        assert indexed == [(accepted.id, accepted.content)]
    finally:
        db.close()
        engine.dispose()


def test_manual_confirmation_and_quarantine_refresh_search(tmp_path, monkeypatch):
    engine, db = _session(tmp_path, monkeypatch)
    try:
        user = db.get(models.User, db.info["test_user_id"])
        assert user is not None
        project = models.Project(owner_id=user.id, name="派生索引闸门")
        db.add(project)
        db.flush()
        chapter = models.Chapter(
            project_id=project.id,
            chapter_number=1,
            sort_order=1,
            title="旧港",
            status="draft",
        )
        db.add(chapter)
        db.flush()
        revision = models.ChapterRevision(
            chapter_id=chapter.id,
            revision_number=1,
            content="林渡在旧港确认潮汐倒流。",
            content_hash=models.ChapterRevision.hash_content("林渡在旧港确认潮汐倒流。"),
        )
        db.add(revision)
        db.flush()
        chapter.current_revision_id = revision.id
        canon = models.CanonItem(
            project_id=project.id,
            category="rule",
            key="潮汐倒流",
            value="每七日一次",
            value_text="每七日一次",
            status="pending",
            source_chapter_id=chapter.id,
            source_revision_id=revision.id,
            source_start=6,
            source_end=12,
            source_excerpt="确认潮汐倒流",
        )
        db.add(canon)
        db.commit()

        confirm_chapter(chapter.id, current_user=user, db=db)
        assert db.execute(
            text("SELECT revision_id FROM chapter_fts WHERE chapter_id = :chapter_id"),
            {"chapter_id": chapter.id},
        ).scalar_one() == revision.id

        confirm_canon(canon.id, current_user=user, db=db)
        assert db.execute(
            text("SELECT canon_item_id FROM canon_fts WHERE canon_item_id = :item_id"),
            {"item_id": canon.id},
        ).scalar_one() == canon.id

        mark_canon_needs_review(canon.id, reason="旧章已修改", current_user=user, db=db)
        assert db.execute(
            text("SELECT COUNT(*) FROM canon_fts WHERE canon_item_id = :item_id"),
            {"item_id": canon.id},
        ).scalar_one() == 0
    finally:
        db.close()
        engine.dispose()


def test_idempotency_is_scoped_per_project(tmp_path, monkeypatch):
    engine, db = _session(tmp_path, monkeypatch)
    try:
        first = models.Project(owner_id=db.info["test_user_id"], name="甲")
        second = models.Project(owner_id=db.info["test_user_id"], name="乙")
        db.add_all([first, second])
        db.commit()
        run_a = create_generation_run(db, first, {"idempotency_key": "same-key"})
        run_b = create_generation_run(db, second, {"idempotency_key": "same-key"})
        assert run_a.created is True
        assert run_b.created is True
        assert run_a.run.project_id != run_b.run.project_id
    finally:
        db.close()
        engine.dispose()


def test_edited_review_requires_server_reaudit_and_high_severity_blocks(tmp_path, monkeypatch):
    engine, db = _session(tmp_path, monkeypatch)
    try:
        project = models.Project(owner_id=db.info["test_user_id"], name="审核边界")
        db.add(project)
        db.commit()
        run = create_generation_run(db, project, {"idempotency_key": "audit-edit"}).run
        execute_generation(db, run.id)
        bundle = db.query(models.ReviewBundle).one()
        edit_review_draft(db, bundle.id, "人工改写后的正文。")
        try:
            accept_review(db, bundle.id)
            raise AssertionError("编辑后的审核稿不应直接接受")
        except ReviewValidationError:
            pass
        reaudit_review_bundle(db, bundle.id)
        bundle.audit_issues = [{"severity": "high", "message": "高风险矛盾"}]
        db.commit()
        try:
            accept_review(db, bundle.id)
            raise AssertionError("high 严重度必须阻止普通接受")
        except BlockerError:
            pass
    finally:
        db.close()
        engine.dispose()


def test_genuine_legacy_sqlite_migrates_owner_constraints_and_flat_uploads(
    tmp_path, monkeypatch
):
    database = tmp_path / "legacy-original.sqlite3"
    engine = create_engine_for_url(f"sqlite:///{database.as_posix()}")
    project_id = "11111111-1111-1111-1111-111111111111"
    chapter_id = "44444444-4444-4444-4444-444444444444"
    revision_id = "55555555-5555-5555-5555-555555555555"
    provider_id = "22222222-2222-2222-2222-222222222222"
    provider_explicit_false_id = "66666666-6666-6666-6666-666666666666"
    import_id = "33333333-3333-3333-3333-333333333333"
    raw_source = "第一章\n雾港的旧稿。".encode()
    source_hash = hashlib.sha256(raw_source).hexdigest()
    chapter_content = "林渡在旧港拾到潮纹铜币。"
    chapter_hash = hashlib.sha256(chapter_content.encode()).hexdigest()
    timestamp = "2026-08-31 12:00:00"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE projects (
                id VARCHAR(36) PRIMARY KEY, name VARCHAR(255) NOT NULL,
                description TEXT, story_bible TEXT, source_hash VARCHAR(64),
                source_filename VARCHAR(255), source_encoding VARCHAR(40),
                genre VARCHAR(120), viewpoint VARCHAR(120), style TEXT,
                target_word_count INTEGER, must_happen JSON NOT NULL,
                must_not_happen JSON NOT NULL, hard_constraints JSON NOT NULL,
                outline JSON NOT NULL, canon_version INTEGER NOT NULL,
                memory_epoch INTEGER NOT NULL, needs_rebuild BOOLEAN NOT NULL,
                current_chapter_id VARCHAR(36), created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE chapters (
                id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL,
                volume_number INTEGER NOT NULL, chapter_number INTEGER NOT NULL,
                sort_order INTEGER NOT NULL, title VARCHAR(255) NOT NULL,
                status VARCHAR(40) NOT NULL, summary TEXT, source_type VARCHAR(40),
                summary_status VARCHAR(40) NOT NULL, current_revision_id VARCHAR(36),
                accepted_revision_id VARCHAR(36), confirmed_at DATETIME,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                CONSTRAINT uq_chapter_project_number UNIQUE (project_id, chapter_number),
                FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE chapter_revisions (
                id VARCHAR(36) PRIMARY KEY, chapter_id VARCHAR(36) NOT NULL,
                revision_number INTEGER NOT NULL, content TEXT NOT NULL,
                content_hash VARCHAR(64) NOT NULL, source_type VARCHAR(40) NOT NULL,
                prompt_version VARCHAR(80), model_name VARCHAR(160),
                parent_revision_id VARCHAR(36), is_generated BOOLEAN NOT NULL,
                extra JSON NOT NULL, created_at DATETIME NOT NULL,
                CONSTRAINT uq_revision_chapter_number UNIQUE (chapter_id, revision_number),
                FOREIGN KEY(chapter_id) REFERENCES chapters (id) ON DELETE CASCADE
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE provider_profiles (
                id VARCHAR(36) PRIMARY KEY, name VARCHAR(120) NOT NULL,
                base_url VARCHAR(500) NOT NULL, protocol VARCHAR(40) NOT NULL,
                model_role_mapping JSON NOT NULL, context_length INTEGER NOT NULL,
                timeout_seconds INTEGER NOT NULL, capabilities JSON NOT NULL,
                api_key_ref VARCHAR(255), enabled BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE import_sources (
                id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL,
                filename VARCHAR(255) NOT NULL, source_hash VARCHAR(64) NOT NULL,
                encoding VARCHAR(40), stored_name VARCHAR(100) NOT NULL,
                byte_size INTEGER NOT NULL, created_at DATETIME NOT NULL,
                CONSTRAINT uq_import_project_hash UNIQUE (project_id, source_hash),
                FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            text(
                "INSERT INTO projects VALUES "
                "(:id,:name,NULL,:bible,:hash,:filename,'utf-8','悬疑',NULL,'克制',"
                "800,'[]','[]','[]','{}',4,7,0,NULL,:created,:updated)"
            ),
            {
                "id": project_id,
                "name": "旧雾港",
                "bible": "潮汐每七日倒流。",
                "hash": source_hash,
                "filename": "old.txt",
                "created": timestamp,
                "updated": timestamp,
            },
        )
        connection.execute(
            text(
                "INSERT INTO chapters VALUES "
                "(:id,:project,1,1,1,'旧港','confirmed',NULL,'import','current',"
                ":revision,:revision,:confirmed,:created,:updated)"
            ),
            {
                "id": chapter_id,
                "project": project_id,
                "revision": revision_id,
                "confirmed": timestamp,
                "created": timestamp,
                "updated": timestamp,
            },
        )
        connection.execute(
            text(
                "INSERT INTO chapter_revisions VALUES "
                "(:id,:chapter,1,:content,:hash,'import',NULL,NULL,NULL,0,'{}',:created)"
            ),
            {
                "id": revision_id,
                "chapter": chapter_id,
                "content": chapter_content,
                "hash": chapter_hash,
                "created": timestamp,
            },
        )
        connection.execute(
            text(
                "INSERT INTO provider_profiles VALUES "
                "(:id,'旧本地模型','http://127.0.0.1:1234/v1','chat_completions',"
                "'{\"writer\":\"legacy-model\"}',8192,120,"
                ":capabilities,NULL,1,:created,:updated)"
            ),
            {
                "id": provider_id,
                "capabilities": (
                    '{"image_input":"not-a-flag","supports_vision":"true",'
                    '"multimodal":"also-invalid","json_schema":true,"custom":"kept"}'
                ),
                "created": timestamp,
                "updated": timestamp,
            },
        )
        connection.execute(
            text(
                "INSERT INTO provider_profiles VALUES "
                "(:id,'显式关闭模型','http://127.0.0.1:1231/v1','chat_completions',"
                "'{\"writer\":\"legacy-model\"}',8192,120,"
                ":capabilities,NULL,1,:created,:updated)"
            ),
            {
                "id": provider_explicit_false_id,
                "capabilities": '{"vision":false,"supports_vision":"true","custom":"kept"}',
                "created": timestamp,
                "updated": timestamp,
            },
        )
        connection.execute(
            text(
                "INSERT INTO import_sources VALUES "
                "(:id,:project,'old.txt',:hash,'utf-8',:stored,:size,:created)"
            ),
            {
                "id": import_id,
                "project": project_id,
                "hash": source_hash,
                "stored": f"{source_hash}.source",
                "size": len(raw_source),
                "created": timestamp,
            },
        )

    data_dir = tmp_path / "legacy-data"
    upload_root = data_dir / "uploads"
    upload_root.mkdir(parents=True)
    (upload_root / f"{source_hash}.source").write_bytes(raw_source)
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    init_db(engine)

    inspector = inspect(engine)
    for table in ("projects", "provider_profiles"):
        owner = next(column for column in inspector.get_columns(table) if column["name"] == "owner_id")
        assert owner["nullable"] is False
        assert any(
            foreign_key["constrained_columns"] == ["owner_id"]
            and foreign_key["referred_table"] == "users"
            for foreign_key in inspector.get_foreign_keys(table)
        )
    user_columns = {column["name"]: column for column in inspector.get_columns("users")}
    assert user_columns["email"]["nullable"] is True
    assert user_columns["email_normalized"]["nullable"] is True
    assert user_columns["username"]["nullable"] is True
    assert user_columns["username_normalized"]["nullable"] is True
    assert "ix_users_username_normalized" in {
        index["name"] for index in inspector.get_indexes("users")
    }
    assert "ck_users_identity_present" in {
        constraint["name"] for constraint in inspector.get_check_constraints("users")
    }
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "20260903_0008"
        )
        migrated = connection.execute(
            text("SELECT owner_id,name,story_bible,canon_version,memory_epoch FROM projects")
        ).one()
        assert tuple(migrated) == (
            LEGACY_OWNER_ID,
            "旧雾港",
            "潮汐每七日倒流。",
            4,
            7,
        )
        migrated_revision = connection.execute(
            text("SELECT content,content_hash FROM chapter_revisions WHERE id=:id"),
            {"id": revision_id},
        ).one()
        assert tuple(migrated_revision) == (chapter_content, chapter_hash)
        migrated_provider = connection.execute(
            text("SELECT capabilities FROM provider_profiles WHERE id=:id"),
            {"id": provider_id},
        ).scalar_one()
        assert json.loads(migrated_provider) == {
            "custom": "kept",
            "json_schema": True,
            "vision": True,
        }
        migrated_explicit_false = connection.execute(
            text("SELECT capabilities FROM provider_profiles WHERE id=:id"),
            {"id": provider_explicit_false_id},
        ).scalar_one()
        assert json.loads(migrated_explicit_false) == {
            "custom": "kept",
            "vision": False,
        }
        stored_name = connection.execute(
            text("SELECT stored_name FROM import_sources WHERE id=:id"), {"id": import_id}
        ).scalar_one()
    expected = upload_root / LEGACY_OWNER_ID / project_id / f"{source_hash}.source"
    assert stored_name == f"{LEGACY_OWNER_ID}/{project_id}/{source_hash}.source"
    assert expected.read_bytes() == raw_source
    assert not (upload_root / f"{source_hash}.source").exists()
    assert list((tmp_path / "backups").glob("*before-alembic-migration.sqlite3"))
    engine.dispose()


def test_existing_0002_sqlite_adds_nullable_username_identity(tmp_path) -> None:
    database = tmp_path / "existing-0002.sqlite3"
    engine = create_engine_for_url(f"sqlite:///{database.as_posix()}")
    timestamp = "2026-08-31 12:00:00"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE users (
                id VARCHAR(36) PRIMARY KEY,
                email VARCHAR(320) NOT NULL,
                email_normalized VARCHAR(320) NOT NULL,
                display_name VARCHAR(120),
                password_hash VARCHAR(512),
                is_email_verified BOOLEAN NOT NULL,
                is_active BOOLEAN NOT NULL,
                default_provider_id VARCHAR(36),
                failed_login_attempts INTEGER NOT NULL,
                locked_until DATETIME,
                last_login_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX ix_users_email_normalized "
            "ON users (email_normalized)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.execute(
            text("INSERT INTO alembic_version VALUES ('20260901_0002')")
        )
        connection.execute(
            text(
                "INSERT INTO users "
                "(id,email,email_normalized,password_hash,is_email_verified,is_active,"
                "failed_login_attempts,created_at,updated_at) VALUES "
                "('old-email','Old@Example.test','old@example.test','hash',1,1,0,"
                ":created,:updated)"
            ),
            {"created": timestamp, "updated": timestamp},
        )

    run_migrations(engine)
    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("users")}
    assert columns["email"]["nullable"] is True
    assert columns["email_normalized"]["nullable"] is True
    assert columns["username"]["nullable"] is True
    assert columns["username_normalized"]["nullable"] is True
    assert "ix_users_username_normalized" in {
        index["name"] for index in inspector.get_indexes("users")
    }
    assert "ck_users_identity_present" in {
        constraint["name"] for constraint in inspector.get_check_constraints("users")
    }
    with engine.begin() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "20260903_0008"
        )
        assert connection.execute(
            text("SELECT email_normalized FROM users WHERE id='old-email'")
        ).scalar_one() == "old@example.test"
        for account_id, username in (("username-1", "writer"), ("username-2", "作者01")):
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id,email,email_normalized,username,username_normalized,password_hash,"
                    "is_email_verified,is_active,failed_login_attempts,created_at,updated_at) "
                    "VALUES (:id,NULL,NULL,:username,:username,'hash',1,1,0,:created,:updated)"
                ),
                {
                    "id": account_id,
                    "username": username,
                    "created": timestamp,
                    "updated": timestamp,
                },
            )
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id,email,email_normalized,username,username_normalized,password_hash,"
                    "is_email_verified,is_active,failed_login_attempts,created_at,updated_at) "
                    "VALUES ('duplicate',NULL,NULL,'Writer','writer','hash',1,1,0,"
                    ":created,:updated)"
                ),
                {"created": timestamp, "updated": timestamp},
            )
    engine.dispose()


def test_claim_legacy_preserves_story_hash_and_rehomes_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_module, "AUTH_MODE", "email")
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'legacy.sqlite3').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    data_dir = tmp_path / "claim-data"
    fake_keyring = _MemoryKeyring()
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(provider_service, "keyring", fake_keyring)
    with factory() as db:
        user = models.User(
            email="owner@example.test",
            email_normalized="owner@example.test",
            password_hash="test-only",
            is_email_verified=True,
            is_active=True,
        )
        project = models.Project(
            owner_id=LEGACY_OWNER_ID,
            name="唯一旧项目",
            story_bible="潮汐每七日倒流一次。",
            canon_version=4,
        )
        provider = models.ProviderProfile(
            owner_id=LEGACY_OWNER_ID,
            name="旧版私有模型",
            base_url="http://127.0.0.1:1234/v1",
            protocol="chat_completions",
            model_role_mapping={"default": "legacy-model"},
        )
        db.add_all([user, project, provider])
        db.flush()
        fake_keyring.set_password(
            provider_service.KEYRING_SERVICE,
            provider.id,
            "legacy-private-key",
        )
        chapter = models.Chapter(
            project_id=project.id,
            chapter_number=1,
            sort_order=1,
            title="旧港",
            status="confirmed",
        )
        db.add(chapter)
        db.flush()
        content = "林渡在旧港捡到一枚刻着潮纹的铜币。"
        revision = models.ChapterRevision(
            chapter_id=chapter.id,
            revision_number=1,
            content=content,
            content_hash=models.ChapterRevision.hash_content(content),
        )
        db.add(revision)
        db.flush()
        chapter.current_revision_id = revision.id
        chapter.accepted_revision_id = revision.id
        db.add(
            models.CanonItem(
                project_id=project.id,
                category="物品",
                key="潮纹铜币",
                value="林渡持有",
                value_text="林渡持有",
                status="confirmed",
                source_chapter_id=chapter.id,
                source_revision_id=revision.id,
                source_start=8,
                source_end=12,
                source_excerpt="一枚刻着",
                canon_version=4,
            )
        )
        imported_bytes = b"legacy import owned by disabled user"
        imported_hash = hashlib.sha256(imported_bytes).hexdigest()
        legacy_source = (
            data_dir
            / "uploads"
            / LEGACY_OWNER_ID
            / project.id
            / f"{imported_hash}.source"
        )
        legacy_source.parent.mkdir(parents=True)
        legacy_source.write_bytes(imported_bytes)
        imported = models.ImportSource(
            project_id=project.id,
            filename="legacy.txt",
            source_hash=imported_hash,
            stored_name=f"{LEGACY_OWNER_ID}/{project.id}/{imported_hash}.source",
            byte_size=len(imported_bytes),
        )
        db.add(imported)
        db.commit()
        project_id = project.id
        provider_id = provider.id
        user_id = user.id
        import_id = imported.id
        before = hashlib.sha256(
            json.dumps(
                {
                    "project": [project.name, project.story_bible, project.canon_version],
                    "chapter": [chapter.title, chapter.status],
                    "revision": [revision.content, revision.content_hash],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(db_module, "init_db", lambda *_args, **_kwargs: engine)
    result = claim_legacy("OWNER@example.test")
    assert result["projects"] == 1
    assert result["providers"] == 1

    with factory() as db:
        project = db.get(models.Project, project_id)
        assert project is not None
        chapter = db.query(models.Chapter).filter_by(project_id=project_id).one()
        revision = db.get(models.ChapterRevision, chapter.accepted_revision_id)
        assert revision is not None
        after = hashlib.sha256(
            json.dumps(
                {
                    "project": [project.name, project.story_bible, project.canon_version],
                    "chapter": [chapter.title, chapter.status],
                    "revision": [revision.content, revision.content_hash],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        imported = db.get(models.ImportSource, import_id)
        assert imported is not None
        expected_name = f"{user_id}/{project_id}/{imported_hash}.source"
        assert imported.stored_name == expected_name
        assert (data_dir / "uploads" / expected_name).read_bytes() == imported_bytes
        assert not legacy_source.exists()
        assert project.owner_id == user_id
        assert after == before
        provider = db.get(models.ProviderProfile, provider_id)
        assert provider is not None
        assert provider.owner_id == user_id
        assert provider.api_key_ref == f"{user_id}:{provider_id}"
        assert provider_service.get_api_key(provider) == "legacy-private-key"
        assert fake_keyring.get_password(provider_service.KEYRING_SERVICE, provider_id) is None
    engine.dispose()


def test_claim_legacy_uses_the_deployment_identity_mode(tmp_path, monkeypatch) -> None:
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'username-claim.sqlite3').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        user = models.User(
            username="Owner_01",
            username_normalized="owner_01",
            password_hash="test-only",
            is_email_verified=True,
            is_active=True,
        )
        project = models.Project(owner_id=LEGACY_OWNER_ID, name="待认领旧项目")
        db.add_all([user, project])
        db.commit()
        user_id = user.id
        project_id = project.id

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(db_module, "init_db", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(cli_module, "AUTH_MODE", "username")
    with pytest.raises(RuntimeError, match="--username"):
        claim_legacy("owner@example.test")

    result = claim_legacy(username=" OWNER_01 ")
    assert result == {"username": "owner_01", "projects": 1, "providers": 0}
    with factory() as db:
        assert db.get(models.Project, project_id).owner_id == user_id
    engine.dispose()


class _CrashProvider(FakeProvider):
    def __init__(self, crash_at: int, *, force_revision: bool = False) -> None:
        self.crash_at = crash_at
        self.force_revision = force_revision
        self.calls = 0

    def _tick(self) -> None:
        self.calls += 1
        if self.calls == self.crash_at:
            # SystemExit deliberately bypasses the workflow's Exception
            # handler, closely matching a process termination mid-request.
            raise SystemExit("simulated process exit")

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        role: str = "writer",
        **kwargs: Any,
    ):
        self._tick()
        return await super().complete(messages, role=role, **kwargs)

    async def structured(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        *,
        role: str = "extractor",
        **kwargs: Any,
    ):
        self._tick()
        payload, response = await super().structured(messages, schema, role=role, **kwargs)
        if self.force_revision and role == "auditor":
            payload = {
                "issues": [
                    {
                        "code": "forced-revision",
                        "severity": "blocker",
                        "message": "测试定向修订恢复",
                    }
                ],
                "summary": "需要修订",
            }
        return payload, response


@pytest.mark.parametrize(
    ("crash_at", "force_revision"),
    [(1, False), (2, False), (3, False), (4, False), (5, False), (6, True)],
)
def test_generation_recovers_after_process_exit_without_duplicate_artifacts(
    tmp_path,
    monkeypatch,
    crash_at: int,
    force_revision: bool,
):
    from backend.app.services import generation as generation_service

    database = tmp_path / f"crash-{crash_at}.sqlite3"
    engine = create_engine_for_url(f"sqlite:///{database.as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        user, _provider = seed_tenant(db, email=f"crash-{crash_at}@example.test")
        project = models.Project(owner_id=user.id, name=f"阶段中断 {crash_at}")
        db.add(project)
        db.commit()
        run = create_generation_run(
            db,
            project,
            {"idempotency_key": f"crash-stage-{crash_at}"},
        ).run
        run_id = run.id

    crashing = _CrashProvider(crash_at, force_revision=force_revision)
    monkeypatch.setattr(generation_service, "provider_for", lambda _profile: crashing)
    with factory() as db, pytest.raises(SystemExit):
        execute_generation(db, run_id)

    with factory() as db:
        assert recover_incomplete_runs(db) == 1
        recovered = db.get(models.GenerationRun, run_id)
        assert recovered is not None
        assert recovered.status == "needs_retry"
        job = db.query(models.Job).filter_by(idempotency_key=f"crash-stage-{crash_at}").one()
        assert job.lease_owner is None

    install_fake_provider(monkeypatch)
    with factory() as db:
        execute_generation(db, run_id)
        run = db.get(models.GenerationRun, run_id)
        assert run is not None
        assert run.status == "awaiting_review"
        assert db.query(models.Job).filter_by(project_id=run.project_id).count() == 1
        assert db.query(models.ReviewBundle).filter_by(generation_run_id=run.id).count() == 1
        artifacts = db.query(models.GenerationArtifact).filter_by(generation_run_id=run.id).all()
        artifact_keys = {(item.stage, item.artifact_type, item.content_hash) for item in artifacts}
        assert len(artifact_keys) == len(artifacts)
        revision_count = db.query(models.ChapterRevision).filter_by(chapter_id=run.chapter_id).count()
        execute_generation(db, run_id)
        assert (
            db.query(models.ChapterRevision).filter_by(chapter_id=run.chapter_id).count()
            == revision_count
        )
        assert db.query(models.ReviewBundle).filter_by(generation_run_id=run.id).count() == 1
    engine.dispose()


def test_temporary_provider_is_frozen_and_deleted_provider_never_falls_back(
    tmp_path, monkeypatch
):
    engine, db = _session(tmp_path, monkeypatch)
    try:
        user = db.get(models.User, db.info["test_user_id"])
        assert user is not None and user.default_provider_id
        default_provider_id = user.default_provider_id
        temporary = models.ProviderProfile(
            owner_id=user.id,
            name="本次 Claude",
            base_url="https://api.anthropic.com/v1",
            protocol="anthropic_messages",
            api_version="2023-06-01",
            model_role_mapping={"default": "claude-frozen", "writer": "claude-frozen"},
            context_length=64000,
            config_version=7,
            enabled=True,
        )
        project = models.Project(owner_id=user.id, name="临时 Provider")
        db.add_all([temporary, project])
        db.commit()
        run = create_generation_run(
            db,
            project,
            {
                "idempotency_key": "temporary-provider",
                "provider_id": temporary.id,
            },
        ).run
        assert run.provider_profile_id == temporary.id
        assert run.provider_profile_id != default_provider_id
        assert run.provider_protocol == "anthropic_messages"
        assert run.provider_config_version == 7
        assert run.provider_snapshot["model_role_mapping"]["writer"] == "claude-frozen"

        temporary.model_role_mapping = {"default": "claude-edited", "writer": "claude-edited"}
        temporary.config_version = 8
        temporary.enabled = False
        temporary.deleted_at = models.utcnow()
        db.commit()
        execute_generation(db, run.id)
        db.refresh(run)
        assert run.status == "needs_retry"
        assert run.provider_profile_id == temporary.id
        assert run.provider_snapshot["model_role_mapping"]["writer"] == "claude-frozen"
        assert db.query(models.GenerationArtifact).filter_by(generation_run_id=run.id).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_invalid_provider_credential_parks_run_for_manual_retry(tmp_path, monkeypatch):
    from backend.app.services import generation as generation_service
    from backend.app.services.providers import ProviderError

    class InvalidCredentialProvider(FakeProvider):
        async def complete(self, *_args: Any, **_kwargs: Any):
            raise ProviderError(
                "模型服务返回错误 HTTP 401：invalid api key",
                status_code=401,
                retryable=True,
            )

    engine, db = _session(tmp_path, monkeypatch)
    try:
        project = models.Project(owner_id=db.info["test_user_id"], name="密钥失效")
        db.add(project)
        db.commit()
        run = create_generation_run(db, project, {"idempotency_key": "invalid-key"}).run
        monkeypatch.setattr(
            generation_service,
            "provider_for",
            lambda _profile: InvalidCredentialProvider(),
        )
        execute_generation(db, run.id)
        db.refresh(run)
        assert run.status == "needs_retry"
        assert "401" in str(run.error)
        assert db.query(models.ReviewBundle).filter_by(generation_run_id=run.id).count() == 0
    finally:
        db.close()
        engine.dispose()
