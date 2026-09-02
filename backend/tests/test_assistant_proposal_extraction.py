"""Regression coverage for provider replies that mix prose and proposals."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.app import models
from backend.app.db import Base, create_engine_for_url
from backend.app.services import assistant as assistant_service
from backend.tests.helpers import seed_tenant


@pytest.fixture()
def store(tmp_path: Path):
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'assistant.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_mixed_prose_yaml_is_cleaned_and_becomes_character_proposal() -> None:
    reply, proposals = assistant_service._normalise_provider_output(
        """这是星岚的人物待确认草稿，尚未写入项目资料。

如果需要提交变更，可返回以下结构化申请：
proposals:
- operation: create_setting_entry
  content: 星岚的核心动机是寻找失踪的妹妹。
  category: character
  name: 星岚
"""
    )

    assert reply == "这是星岚的人物待确认草稿，尚未写入项目资料。"
    assert len(proposals) == 1
    assert proposals[0]["operation"] == "create_character"
    assert proposals[0]["target_type"] == "character"
    assert proposals[0]["patch"] == {
        "name": "星岚",
        "background": "星岚的核心动机是寻找失踪的妹妹。",
    }
    assert "proposals:" not in reply


def test_project_scope_does_not_become_new_character_target() -> None:
    _reply, proposals = assistant_service._normalise_provider_output(
        {
            "reply": "先给你一张待确认人物卡。",
            "proposals": [
                {
                    "operation": "create_character",
                    "target_type": "character",
                    "patch": {"name": "星岚", "motivation": "寻找妹妹"},
                }
            ],
        },
        target={"type": "project", "id": "project-1"},
    )

    assert proposals[0]["operation"] == "create_character"
    assert proposals[0]["target_id"] is None


def test_raw_legacy_json_envelope_keeps_proposals_when_structured_value_is_missing() -> None:
    reply, proposals = assistant_service._normalise_provider_output(
        None,
        '{"reply":"人物草稿已准备好。","proposals":['
        '{"operation":"create_character","target_type":"character",'
        '"patch":{"name":"云雀"}}]}',
        target={"type": "project", "id": "project-1"},
    )

    assert reply == "人物草稿已准备好。"
    assert proposals[0]["operation"] == "create_character"
    assert proposals[0]["patch"]["name"] == "云雀"
    assert proposals[0]["target_id"] is None


def test_generic_provider_extraction_prompt_names_multi_entity_contract() -> None:
    instruction = assistant_service.ASSISTANT_EXTRACTION_INSTRUCTION

    assert '"proposals"' in instruction
    assert "create_character" in instruction
    assert "upsert_graph_edge" in instruction
    assert "source_name" in instruction
    assert "三条独立提案" in instruction


def test_character_setting_entry_can_emit_explicit_relationship_edge() -> None:
    _reply, proposals = assistant_service._normalise_provider_output(
        """人物草稿
proposals:
- operation: create_setting_entry
  category: character
  name: 星岚
  content: 夜枭成员
  related_to: 陆小凡
  relation_type: 搭档
"""
    )

    assert [item["operation"] for item in proposals] == [
        "create_character",
        "upsert_graph_edge",
    ]
    edge = proposals[1]
    assert edge["target_type"] == "character_relation"
    assert edge["patch"] == {
        "source_name": "星岚",
        "target_name": "陆小凡",
        "relation_type": "搭档",
    }


def test_legacy_chapter_replace_uses_server_context() -> None:
    _reply, proposals = assistant_service._normalise_provider_output(
        """我已经改写了这一段。
proposals:
- operation: replace
  target_type: chapter
  target_id: chapter-1
  base_revision_id: null
  replacement: |
    新的第一段
    新的第二段
""",
        target={"type": "chapter", "chapter_id": "chapter-1"},
        context={
            "chapter_id": "chapter-1",
            "base_revision_id": "revision-1",
            "base_content_hash": "content-hash",
            "selection_start": 0,
            "selection_end": 4,
            "selection_hash": "selection-hash",
        },
    )

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["operation"] == "edit_chapter_selection"
    assert proposal["target_id"] == "chapter-1"
    assert proposal["patch"]["base_revision_id"] == "revision-1"
    assert proposal["patch"]["base_content_hash"] == "content-hash"
    assert proposal["patch"]["replacement"] == "新的第一段\n新的第二段\n"


def test_empty_chapter_baseline_overrides_forged_model_range() -> None:
    _reply, proposals = assistant_service._normalise_provider_output(
        """我已经写好整章正文。
proposals:
- operation: replace
  target_type: chapter
  target_id: forged-chapter
  base_revision_id: forged-revision
  base_content_hash: forged-hash
  selection_start: 12
  selection_end: 99999
  selection_hash: forged-selection
  replacement: 新的正文
""",
        target={"type": "chapter", "chapter_id": "chapter-1"},
        context={
            "chapter_id": "chapter-1",
            "empty_chapter_baseline": True,
            "base_revision_id": None,
            "base_content_hash": assistant_service._hash_text(""),
            "selection_start": 0,
            "selection_end": 0,
            "selection_hash": assistant_service._hash_text(""),
        },
    )

    proposal = proposals[0]
    patch = proposal["patch"]
    assert proposal["operation"] == "edit_chapter_selection"
    assert proposal["target_id"] == "chapter-1"
    assert patch["empty_chapter_baseline"] is True
    assert patch["base_revision_id"] is None
    assert patch["base_content_hash"] == assistant_service._hash_text("")
    assert patch["selection_start"] == 0
    assert patch["selection_end"] == 0
    assert patch["selection_hash"] == assistant_service._hash_text("")


def test_provider_context_marks_only_verified_empty_chapter_as_baseline(store: Any) -> None:
    with store() as db:
        user, profile = seed_tenant(db)
        assert profile is not None
        project = models.Project(owner_id=user.id, name="空白章节")
        chapter = models.Chapter(
            project=project,
            volume_number=1,
            chapter_number=1,
            sort_order=0,
            title="第一章",
            status="draft",
        )
        db.add_all([project, chapter])
        db.flush()
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            provider_profile_id=profile.id,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        _message, run, _created = assistant_service.create_message_run(
            db,
            conversation,
            user,
            "请生成第一章",
            idempotency_key="empty-chapter-context",
            target={"type": "chapter", "chapter_id": chapter.id},
            context_snapshot={
                "base_revision_id": "forged",
                "base_content_hash": "forged",
                "selection_start": 9,
                "selection_end": 99,
                "selection_hash": "forged",
            },
        )

        assistant_service._provider_messages(db, conversation, run, project, user, profile)

        context = run.input_snapshot["authoritative_context"]
        assert context["chapter_id"] == chapter.id
        assert context["empty_chapter_baseline"] is True
        assert context["base_revision_id"] is None
        assert context["base_content_hash"] == assistant_service._hash_text("")
        assert context["selection_start"] == 0
        assert context["selection_end"] == 0
        assert context["selection_hash"] == assistant_service._hash_text("")


def test_fallback_yaml_parser_handles_provider_body_without_pyyaml(monkeypatch) -> None:
    monkeypatch.setattr(assistant_service, "_yaml", None)
    reply, proposals = assistant_service._normalise_provider_output(
        """普通回复
proposals:
- operation: create_setting_entry
  category: character
  name: 阿禾
  content: 一个新人物
"""
    )

    assert reply == "普通回复"
    assert proposals[0]["operation"] == "create_character"
    assert proposals[0]["patch"]["background"] == "一个新人物"


def test_markdown_character_and_graph_draft_becomes_structured_proposals() -> None:
    reply, proposals = assistant_service._normalise_provider_output(
        """### 待确认人物设定草稿
【主角：林砚】
基础身份：出身江南寒门秀才之家
核心动机：查证父亲真相
核心目标：考中举人
核心冲突：科举与底线之间挣扎

### 待确认关联图谱草稿
节点1：林砚（主角）
- 林砚 --(父子羁绊)--> 林父（已故秀才）
- 林砚 --(科场对立)--> 乡试主考官 周嵩（舞弊案参与者）
- 林砚 --(资助)--> 族学先生 陈老夫子（知情者）
- 林砚 --(情感)--> 表妹 苏晚（商户之女）
"""
    )

    assert reply == "已整理人物与图谱草稿，请确认后写入项目。"
    assert [item["operation"] for item in proposals].count("create_character") == 5
    assert [item["operation"] for item in proposals].count("upsert_graph_edge") == 4
    characters = {
        item["patch"]["name"]: item["patch"]
        for item in proposals
        if item["operation"] == "create_character"
    }
    assert characters["林砚"]["role"] == "主角"
    assert characters["林砚"]["motivation"] == "查证父亲真相"
    assert characters["周嵩"]["background"] == "舞弊案参与者"
    edges = [item["patch"] for item in proposals if item["operation"] == "upsert_graph_edge"]
    assert {edge["target_name"] for edge in edges} == {"林父", "周嵩", "陈老夫子", "苏晚"}


def test_stream_markdown_draft_hides_machine_text_and_publishes_events(
    store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    class MarkdownProvider:
        async def stream(self, _messages: list[dict[str, Any]], **_kwargs: Any):
            for chunk in (
                "已整理如下：\n\n### 待确认人物设定草稿\n",
                "【主角：林砚】\n基础身份：江南寒门出身\n核心动机：查明父亲旧案\n\n",
                "### 待确认关联图谱草稿\n节点1：林砚（主角）\n",
                "- 林砚 --(父子羁绊)--> 林父（已故秀才）\n",
            ):
                yield chunk

        async def structured(self, *_args: Any, **_kwargs: Any):
            raise RuntimeError("structured endpoint unavailable")

    monkeypatch.setattr(assistant_service, "provider_for", lambda _profile: MarkdownProvider())
    with store() as db:
        user, profile = seed_tenant(db)
        assert profile is not None
        project = models.Project(owner_id=user.id, name="Markdown 兜底")
        db.add(project)
        db.flush()
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            provider_profile_id=profile.id,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        _message, run, _created = assistant_service.create_message_run(
            db,
            conversation,
            user,
            "整理人物和关系草稿",
            idempotency_key="markdown-draft-stream",
        )

        assistant_service.execute_agent_run(db, run.id)

        assistant_message = db.scalar(
            select(models.AgentMessage).where(
                models.AgentMessage.run_id == run.id,
                models.AgentMessage.role == "assistant",
            )
        )
        assert assistant_message is not None
        assert assistant_message.content == "已整理如下："
        assert "###" not in assistant_message.content
        proposals = db.scalars(
            select(models.Proposal).where(models.Proposal.project_id == project.id)
        ).all()
        assert len(proposals) == 3
        assert {item.operation for item in proposals} == {"create_character", "upsert_graph_edge"}
        events = db.scalars(
            select(models.AgentEvent)
            .where(models.AgentEvent.run_id == run.id)
            .order_by(models.AgentEvent.sequence)
        ).all()
        proposal_event_rows = [
            event for event in events if event.event_type.startswith("proposal.")
        ]
        proposal_ids = [str(item.id) for item in proposals]
        assert len(proposal_event_rows) == 16
        assert [
            str(event.payload_json["proposal_id"])
            for event in proposal_event_rows
            if event.event_type == "proposal.created"
        ] == proposal_ids
        for proposal_id in proposal_ids:
            related = [
                event.event_type
                for event in proposal_event_rows
                if str(event.payload_json["proposal_id"]) == proposal_id
            ]
            assert related[0] == "proposal.created"
            assert related[-1] == "proposal.ready"
            assert related[1:-1]
            assert all(event_type == "proposal.patch" for event_type in related[1:-1])


def test_stream_yaml_fallback_persists_clean_reply_and_preview_events(
    store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Provider:
        async def stream(self, _messages: list[dict[str, Any]], **_kwargs: Any):
            yield "普通回复\n如果需要提交变更，可返回以下结构化申请："
            yield """
proposals:
- operation: create_setting_entry
  category: character
  name: 阿禾
  content: 一个新人物
"""

        async def structured(self, *_args: Any, **_kwargs: Any):
            raise RuntimeError("structured endpoint unavailable")

    monkeypatch.setattr(assistant_service, "provider_for", lambda _profile: Provider())
    with store() as db:
        user, profile = seed_tenant(db)
        assert profile is not None
        project = models.Project(owner_id=user.id, name="混合提案")
        db.add(project)
        db.flush()
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            provider_profile_id=profile.id,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        _message, run, _created = assistant_service.create_message_run(
            db,
            conversation,
            user,
            "请创建人物",
            idempotency_key="mixed-yaml-stream",
        )

        assistant_service.execute_agent_run(db, run.id)

        assistant_message = db.scalar(
            select(models.AgentMessage).where(
                models.AgentMessage.run_id == run.id,
                models.AgentMessage.role == "assistant",
            )
        )
        assert assistant_message is not None
        assert assistant_message.content == "普通回复"
        assert "proposals:" not in assistant_message.content
        proposal = db.scalar(
            select(models.Proposal).where(models.Proposal.project_id == project.id)
        )
        assert proposal is not None
        assert proposal.operation == "create_character"
        assert proposal.patch_json["name"] == "阿禾"
        events = db.scalars(
            select(models.AgentEvent)
            .where(models.AgentEvent.run_id == run.id)
            .order_by(models.AgentEvent.sequence)
        ).all()
        proposal_events = [row for row in events if row.event_type.startswith("proposal.")]
        assert [row.event_type for row in proposal_events] == [
            "proposal.created",
            "proposal.patch",
            "proposal.patch",
            "proposal.ready",
        ]
        assert proposal_events[0].payload_json["proposal"]["patches"] == []
        assert proposal_events[-1].payload_json["proposal"]["patches"]
        replace_events = [row for row in events if row.event_type == "message.replace"]
        assert replace_events
        assert replace_events[-1].payload_json["content"] == "普通回复"


def test_empty_chapter_proposal_creates_first_revision_and_review_bundle(store: Any) -> None:
    with store() as db:
        user, _profile = seed_tenant(db, with_provider=False)
        project = models.Project(owner_id=user.id, name="空白章节应用")
        chapter = models.Chapter(
            project=project,
            volume_number=1,
            chapter_number=1,
            sort_order=0,
            title="第一章",
            status="draft",
        )
        db.add_all([project, chapter])
        db.flush()
        change_set = models.ChangeSet(
            project_id=project.id,
            source_type="assistant",
            status="proposed",
            created_by_user_id=user.id,
        )
        db.add(change_set)
        db.flush()
        empty_hash = assistant_service._hash_text("")
        proposal = models.Proposal(
            project_id=project.id,
            change_set_id=change_set.id,
            operation="edit_chapter_selection",
            target_type="chapter",
            target_id=chapter.id,
            patch_json={
                "empty_chapter_baseline": True,
                "base_revision_id": None,
                "base_content_hash": empty_hash,
                "selection_start": 0,
                "selection_end": 0,
                "selection_hash": empty_hash,
                "replacement": "新的第一章正文",
            },
            base_memory_epoch=project.memory_epoch,
            status="proposed",
            reason="生成第一章",
            created_by_user_id=user.id,
        )
        db.add(proposal)
        db.commit()

        applied = assistant_service.apply_proposal(db, proposal, user)

        assert applied.status == "applied"
        db.refresh(chapter)
        revision = db.get(models.ChapterRevision, chapter.current_revision_id)
        assert revision is not None
        assert revision.revision_number == 1
        assert revision.content == "新的第一章正文"
        assert revision.parent_revision_id is None
        assert chapter.status == "needs_review"
        bundle = db.scalar(
            select(models.ReviewBundle).where(
                models.ReviewBundle.chapter_id == chapter.id,
                models.ReviewBundle.draft_revision_id == revision.id,
            )
        )
        assert bundle is not None
        assert bundle.status == "pending"
        assert bundle.source_context[0]["empty_chapter_baseline"] is True


def test_empty_baseline_rejects_non_empty_orphan_revision(store: Any) -> None:
    with store() as db:
        user, _profile = seed_tenant(db, with_provider=False)
        project = models.Project(owner_id=user.id, name="防覆盖")
        chapter = models.Chapter(
            project=project,
            volume_number=1,
            chapter_number=1,
            sort_order=0,
            title="第一章",
            status="draft",
        )
        db.add_all([project, chapter])
        db.flush()
        orphan = models.ChapterRevision(
            chapter_id=chapter.id,
            revision_number=1,
            content="服务器已有但未指向的正文",
            content_hash=models.ChapterRevision.hash_content("服务器已有但未指向的正文"),
            source_type="manual",
        )
        db.add(orphan)
        db.flush()
        change_set = models.ChangeSet(
            project_id=project.id,
            source_type="assistant",
            status="proposed",
            created_by_user_id=user.id,
        )
        db.add(change_set)
        db.flush()
        empty_hash = assistant_service._hash_text("")
        proposal = models.Proposal(
            project_id=project.id,
            change_set_id=change_set.id,
            operation="edit_chapter",
            target_type="chapter",
            target_id=chapter.id,
            patch_json={
                "empty_chapter_baseline": True,
                "base_revision_id": None,
                "base_content_hash": empty_hash,
                "selection_start": 0,
                "selection_end": 0,
                "selection_hash": empty_hash,
                "replacement": "不应覆盖",
            },
            base_memory_epoch=project.memory_epoch,
            status="proposed",
            reason="伪造空白基线",
            created_by_user_id=user.id,
        )
        db.add(proposal)
        db.commit()

        with pytest.raises(RuntimeError, match="重新生成提案"):
            assistant_service.apply_proposal(db, proposal, user)

        db.refresh(proposal)
        db.refresh(chapter)
        assert proposal.status == "conflict"
        assert chapter.current_revision_id is None
