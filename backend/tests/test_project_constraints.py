"""Project generation-constraint persistence and context coverage."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.app import db as db_module
from backend.app import models
from backend.app.db import create_engine_for_url, get_db, init_db
from backend.app.main import app
from backend.app.services.context import build_context
from backend.tests.helpers import authenticate_client


def test_build_context_includes_project_constraints_as_mandatory_sources(tmp_path):
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'constraints.sqlite3').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        user = models.User(
            email="context-owner@example.test",
            email_normalized="context-owner@example.test",
            display_name="上下文作者",
            password_hash="test-only",
            is_email_verified=True,
            is_active=True,
        )
        db.add(user)
        db.flush()
        project = models.Project(
            owner_id=user.id,
            name="约束上下文",
            story_bible="海港故事的背景资料。",
            target_word_count=2400,
            must_happen=["第二轮月必须升起", {"subject": "林澈", "predicate": "抵达", "value": "灯塔"}],
            must_not_happen=["禁止揭示出生年份真相"],
            hard_constraints=["林澈不能离开灯塔"],
        )
        db.add(project)
        db.commit()

        context = build_context(db, project, budget=512)

    # Project requirements are non-droppable even when the optional context
    # budget is exhausted; the source labels make their polarity explicit.
    assert "必须发生：" in context["text"]
    assert "第二轮月必须升起" in context["text"]
    assert '"subject": "林澈"' in context["text"]
    assert "禁止发生：" in context["text"]
    assert "禁止揭示出生年份真相" in context["text"]
    assert "硬约束：" in context["text"]
    assert "林澈不能离开灯塔" in context["text"]

    labels = {source["label"] for source in context["sources"]}
    assert {"必须发生", "禁止发生"}.issubset(labels)
    # Hard constraints stay inside the mandatory story-bible source.
    story_source = next(source for source in context["sources"] if source["label"] == "故事圣经")
    assert "林澈不能离开灯塔" in story_source["excerpt"]
    engine.dispose()


def test_project_constraint_patch_and_read_are_tenant_scoped(tmp_path, monkeypatch):
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'api-constraints.sqlite3').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", factory)

    def override_db():
        with factory() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_db, override_db)
    first = TestClient(app, base_url="http://127.0.0.1")
    second = TestClient(app, base_url="http://127.0.0.1")

    authenticate_client(first, factory, email="constraints-first@example.test", with_provider=False)
    authenticate_client(second, factory, email="constraints-second@example.test", with_provider=False)
    with first, second:
        created = first.post(
            "/api/projects",
            json={
                "title": "持久约束项目",
                "target_word_count": 2100,
                "must_happen": ["必须保留旧钟声"],
                "must_not_happen": ["不得杀死守灯人"],
                "hard_constraints": ["不得改变已确认正典"],
            },
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]
        assert created.json()["target_word_count"] == 2100
        assert created.json()["must_happen"] == ["必须保留旧钟声"]
        assert created.json()["must_not_happen"] == ["不得杀死守灯人"]

        updated = first.patch(
            f"/api/projects/{project_id}",
            json={
                "target_word_count": 2800,
                "must_happen": ["必须出现潮汐倒流"],
                "must_not_happen": ["不得泄露幕后人物"],
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["target_word_count"] == 2800
        assert updated.json()["must_happen"] == ["必须出现潮汐倒流"]
        assert updated.json()["must_not_happen"] == ["不得泄露幕后人物"]

        reread = first.get(f"/api/projects/{project_id}")
        assert reread.status_code == 200, reread.text
        assert reread.json()["target_word_count"] == 2800
        assert reread.json()["must_happen"] == ["必须出现潮汐倒流"]
        assert reread.json()["must_not_happen"] == ["不得泄露幕后人物"]
        assert reread.json()["hard_constraints"] == ["不得改变已确认正典"]

        assert second.get(f"/api/projects/{project_id}").status_code == 404
        assert (
            second.patch(
                f"/api/projects/{project_id}",
                json={"must_happen": ["越权修改"]},
            ).status_code
            == 404
        )

        unchanged = first.get(f"/api/projects/{project_id}")
        assert unchanged.json()["must_happen"] == ["必须出现潮汐倒流"]
        assert unchanged.json()["must_not_happen"] == ["不得泄露幕后人物"]

    engine.dispose()

