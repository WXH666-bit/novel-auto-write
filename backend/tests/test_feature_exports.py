"""Acceptance tests for the portable 2.1 story-workspace backup.

These tests intentionally exercise the service boundary rather than the HTTP
transport.  The export is a user-owned artifact, so the important assertions
are tenant scoping, reference remapping, binary integrity, and the ability to
read a 2.0 archive after the feature tables have been installed.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from backend.app import models
from backend.app.db import Base, create_engine_for_url
from backend.app.services import exports
from backend.app.services.exports import export_project_zip, restore_project_zip
from backend.app.services.importer import content_hash
from backend.tests.helpers import seed_tenant


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'feature.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    user, _ = seed_tenant(session, email="feature-owner@example.test", with_provider=False)
    session.info["test_user_id"] = user.id
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(exports, "DATA_DIR", data_dir)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _project(db: Session, name: str) -> models.Project:
    project = models.Project(owner_id=db.info["test_user_id"], name=name)
    db.add(project)
    db.flush()
    return project


def _chapter(db: Session, project: models.Project) -> tuple[models.Chapter, models.ChapterRevision]:
    chapter = models.Chapter(
        project_id=project.id,
        chapter_number=1,
        sort_order=1,
        title="潮汐",
        status="confirmed",
        summary_status="current",
    )
    db.add(chapter)
    db.flush()
    content = "林渡把铜钥匙交给守灯人。"
    revision = models.ChapterRevision(
        chapter_id=chapter.id,
        revision_number=1,
        content=content,
        content_hash=models.ChapterRevision.hash_content(content),
        source_type="manual",
    )
    db.add(revision)
    db.flush()
    chapter.current_revision_id = revision.id
    chapter.accepted_revision_id = revision.id
    return chapter, revision


def _seed_feature_workspace(db: Session) -> dict[str, object]:
    owner_id = db.info["test_user_id"]
    project = _project(db, "可携带故事")
    chapter, revision = _chapter(db, project)

    summary = models.StorySummary(
        project_id=project.id,
        scope="chapter",
        chapter_id=chapter.id,
        current_revision_id=revision.id,
        status="current",
        summary_text="钥匙交接推进灯塔线索。",
        structured_json={
            "storylines": [{"name": "灯塔线", "status": "open"}],
            "character_relations": [{"from": "林渡", "to": "守灯人"}],
        },
        memory_epoch=1,
    )
    db.add(summary)
    db.flush()
    summary_revision = models.StorySummaryRevision(
        story_summary_id=summary.id,
        source_revision_id=revision.id,
        summary_text=summary.summary_text,
        structured_json=summary.structured_json,
        provider_profile_id="source-provider-id-must-not-survive",
        model_name="test-model",
        prompt_version="test-prompt",
        memory_epoch=1,
    )

    character = models.Character(
        project_id=project.id,
        name="林渡",
        aliases=["渡哥"],
        role="主角",
        appearance="黑发，旧风衣",
        personality="谨慎",
        goals="查明灯塔秘密",
        custom_fields={"favorite_color": "墨蓝"},
    )
    db.add(character)
    db.flush()
    character_revision = models.CharacterRevision(
        character_id=character.id,
        revision_number=1,
        name=character.name,
        aliases=character.aliases,
        role=character.role,
        appearance=character.appearance,
        personality=character.personality,
        goals=character.goals,
        custom_fields=character.custom_fields,
        source_type="manual",
        source_revision_id=revision.id,
        created_by_user_id=owner_id,
    )
    db.add(character_revision)
    db.flush()
    character.current_revision_id = character_revision.id

    chapter_node = models.StoryGraphNode(
        project_id=project.id,
        node_type="chapter",
        ref_id=chapter.id,
        chapter_id=chapter.id,
        label="潮汐",
        position_x=120,
        position_y=80,
        data={"summary_id": summary.id},
    )
    character_node = models.StoryGraphNode(
        project_id=project.id,
        node_type="character",
        ref_id=character.id,
        character_id=character.id,
        label="林渡",
        position_x=360,
        position_y=80,
    )
    db.add_all([chapter_node, character_node])
    db.flush()
    edge = models.StoryGraphEdge(
        project_id=project.id,
        source_node_id=character_node.id,
        target_node_id=chapter_node.id,
        relation_type="appears_in",
        label="出场",
        data={"source_character_id": character.id},
    )
    layout = models.StoryGraphLayout(
        project_id=project.id,
        layout_json={
            "zoom": 1.1,
            "nodes": {character_node.id: {"x": 360, "y": 80}},
        },
    )
    db.add_all([edge, layout])

    # Binary data lives outside the database.  The exporter must only resolve
    # paths below DATA_DIR and the restore must relocate them to the new
    # tenant/project directory.
    image = b"\x89PNG\r\n\x1a\nfeature-image"
    image_path = exports.DATA_DIR / "assets" / owner_id / project.id / "portrait.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(image)
    asset = models.MediaAsset(
        owner_id=owner_id,
        project_id=project.id,
        kind="character",
        original_name="portrait.png",
        mime_type="image/png",
        extension=".png",
        byte_size=len(image),
        checksum=content_hash(image),
        storage_key=f"assets/{owner_id}/{project.id}/portrait.png",
        width=1,
        height=1,
        alt_text="林渡",
    )
    db.add(asset)
    db.flush()
    character.image_media_id = asset.id
    missing_asset = models.MediaAsset(
        owner_id=owner_id,
        project_id=project.id,
        kind="character",
        original_name="missing.png",
        mime_type="image/png",
        extension=".png",
        byte_size=99,
        checksum=content_hash(b"missing-sidecar"),
        storage_key=f"assets/{owner_id}/{project.id}/missing.png",
        width=1,
        height=1,
    )
    db.add(missing_asset)

    missing_source = models.ImportSource(
        project_id=project.id,
        filename="missing-source.txt",
        source_hash=content_hash(b"missing-source"),
        encoding="utf-8",
        stored_name=f"uploads/{owner_id}/{project.id}/missing-source.txt",
        byte_size=len(b"missing-source"),
    )
    db.add(missing_source)

    memory_run = models.MemoryBuildRun(
        project_id=project.id,
        chapter_id=chapter.id,
        scope="chapter",
        status="completed",
        idempotency_key="memory-run-1",
        stage="completed",
    )
    db.add(memory_run)
    db.flush()
    memory_artifact = models.MemoryBuildArtifact(
        run_id=memory_run.id,
        stage="chapter:aggregate",
        content_hash=content_hash("memory checkpoint input"),
        content="memory checkpoint output",
        metadata_json={"checkpoint": True},
    )
    db.add(memory_artifact)

    conversation = models.AgentConversation(
        project_id=project.id,
        created_by_user_id=owner_id,
        title="设定助手",
        purpose="setup",
        apply_mode="preview",
        provider_profile_id="source-provider-id-must-not-survive",
        provider_snapshot={"model": "test-model", "api_key": "secret-must-not-survive"},
        context_snapshot={"character_id": character.id},
    )
    db.add(conversation)
    db.flush()
    run = models.AgentRun(
        project_id=project.id,
        conversation_id=conversation.id,
        idempotency_key="agent-run-1",
        status="completed",
        stage="completed",
        provider_profile_id="source-provider-id-must-not-survive",
        provider_snapshot={"api_key": "secret-must-not-survive"},
        input_snapshot={"character_id": character.id},
    )
    db.add(run)
    db.flush()
    message = models.AgentMessage(
        project_id=project.id,
        conversation_id=conversation.id,
        run_id=run.id,
        sequence=1,
        role="assistant",
        content="我建议把林渡的动机写成查明灯塔秘密。",
        target_json={"character_id": character.id},
        authorized_asset_ids=[asset.id],
    )
    db.add(message)
    db.flush()
    run.message_id = message.id
    event = models.AgentEvent(
        project_id=project.id,
        conversation_id=conversation.id,
        run_id=run.id,
        sequence=1,
        event_type="proposal.created",
        payload_json={"character_id": character.id},
    )
    stream_event = models.AgentEvent(
        project_id=project.id,
        conversation_id=conversation.id,
        run_id=run.id,
        sequence=2,
        event_type="message_delta",
        payload_json={"delta": "临时片段"},
    )
    tool_call = models.AgentToolCall(
        project_id=project.id,
        conversation_id=conversation.id,
        run_id=run.id,
        tool_name="read_character",
        arguments_json={"character_id": character.id},
        result_json={"ok": True},
    )
    db.add_all([event, stream_event, tool_call])

    change_set = models.ChangeSet(
        project_id=project.id,
        source_type="assistant",
        source_id=conversation.id,
        base_memory_epoch=1,
        status="proposed",
        summary="补充人物动机",
        changes_json=[{"target_type": "character", "target_id": character.id}],
        created_by_user_id=owner_id,
    )
    db.add(change_set)
    db.flush()
    proposal = models.Proposal(
        project_id=project.id,
        change_set_id=change_set.id,
        operation="update",
        target_type="character",
        target_id=character.id,
        patch_json={"goals": "查明灯塔秘密", "api_key": "secret-must-not-survive"},
        base_version=character.version,
        base_memory_epoch=1,
        status="proposed",
        created_by_user_id=owner_id,
    )
    db.add_all([summary_revision, edge, layout, proposal])
    db.commit()

    # A second project proves every optional query is project-scoped.
    other = _project(db, "另一个故事")
    other_character = models.Character(project_id=other.id, name="不应导出")
    other_conversation = models.AgentConversation(
        project_id=other.id,
        created_by_user_id=owner_id,
        title="另一会话",
    )
    db.add_all([other_character, other_conversation])
    db.commit()
    return {
        "project": project,
        "other": other,
        "chapter": chapter,
        "revision": revision,
        "summary": summary,
        "summary_revision": summary_revision,
        "character": character,
        "character_revision": character_revision,
        "chapter_node": chapter_node,
        "character_node": character_node,
        "edge": edge,
        "layout": layout,
        "asset": asset,
        "missing_asset": missing_asset,
        "missing_source": missing_source,
        "image": image,
        "conversation": conversation,
        "run": run,
        "message": message,
        "event": event,
        "stream_event": stream_event,
        "tool_call": tool_call,
        "memory_run": memory_run,
        "memory_artifact": memory_artifact,
        "change_set": change_set,
        "proposal": proposal,
    }


def _rewrite_archive(raw: bytes, *, schema_version: str, drop_prefixes: tuple[str, ...] = ()) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED
    ) as target:
        for name in source.namelist():
            if any(name == prefix or name.startswith(prefix) for prefix in drop_prefixes):
                continue
            value = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(value)
                manifest["schema_version"] = schema_version
                value = json.dumps(manifest, ensure_ascii=False).encode()
            target.writestr(name, value)
    return output.getvalue()


def test_export_21_is_project_scoped_and_contains_portable_feature_state(
    db: Session,
) -> None:
    seeded = _seed_feature_workspace(db)
    project = seeded["project"]
    archive = export_project_zip(db, project.id, owner_id=db.info["test_user_id"])

    with zipfile.ZipFile(io.BytesIO(archive)) as opened:
        names = set(opened.namelist())
        manifest = json.loads(opened.read("manifest.json"))
        assert manifest["schema_version"] == "2.1"
        for member in (
            "summaries.json",
            "summary_revisions.json",
            "memory_build_runs.json",
            "memory_build_artifacts.json",
            "characters.json",
            "character_revisions.json",
            "graph_nodes.json",
            "graph_edges.json",
            "graph_layout.json",
            "assets.json",
            "assets_manifest.json",
            "assistant_conversations.json",
            "assistant_messages.json",
            "assistant_runs.json",
            "assistant_events.json",
            "assistant_tool_calls.json",
            "assistant_change_sets.json",
            "assistant_proposals.json",
        ):
            assert member in names
        assert str(seeded["character"].id) in opened.read("characters.json").decode()
        assert "不应导出" not in opened.read("characters.json").decode()
        assert b"secret-must-not-survive" not in archive
        asset_entries = json.loads(opened.read("assets_manifest.json"))
        assert len(asset_entries) == 2
        entry = next(item for item in asset_entries if not item["missing"])
        missing_entry = next(item for item in asset_entries if item["missing"])
        assert entry["sha256"] == content_hash(seeded["image"])
        assert opened.read(entry["archive_name"]) == seeded["image"]
        assert missing_entry["asset_id"] == str(seeded["missing_asset"].id)
        assert missing_entry["missing_reason"] == "file_not_found"
        assert json.loads(opened.read("original_imports_manifest.json"))[0]["missing"] is True
        assert manifest["missing_asset_files"] == 1
        assert manifest["missing_original_import_files"] == 1
        assert "临时片段" not in archive.decode("utf-8", errors="ignore")
        assert "memory checkpoint output" in opened.read("memory_build_artifacts.json").decode()


def test_restore_21_remaps_all_feature_references_and_relocates_image(
    db: Session,
) -> None:
    seeded = _seed_feature_workspace(db)
    owner_id = db.info["test_user_id"]
    archive = export_project_zip(db, seeded["project"].id, owner_id=owner_id)
    restored = restore_project_zip(db, archive, owner_id=owner_id)

    restored_character = (
        db.query(models.Character)
        .filter_by(project_id=restored.id, name="林渡")
        .one()
    )
    restored_revision = db.query(models.CharacterRevision).filter_by(
        character_id=restored_character.id
    ).one()
    assert restored_character.current_revision_id == restored_revision.id
    restored_chapter_revision = (
        db.query(models.ChapterRevision)
        .join(models.Chapter, models.Chapter.id == models.ChapterRevision.chapter_id)
        .filter(models.Chapter.project_id == restored.id)
        .one()
    )
    assert restored_revision.source_revision_id == restored_chapter_revision.id
    restored_summary = db.query(models.StorySummary).filter_by(project_id=restored.id).one()
    restored_summary_revision = db.query(models.StorySummaryRevision).filter_by(
        story_summary_id=restored_summary.id
    ).one()
    assert restored_summary_revision.provider_profile_id is None
    assert restored_summary.current_revision_id is not None

    restored_nodes = db.query(models.StoryGraphNode).filter_by(project_id=restored.id).all()
    restored_node_ids = {item.id for item in restored_nodes}
    restored_edge = db.query(models.StoryGraphEdge).filter_by(project_id=restored.id).one()
    assert restored_edge.source_node_id in restored_node_ids
    assert restored_edge.target_node_id in restored_node_ids
    restored_layout = db.query(models.StoryGraphLayout).filter_by(project_id=restored.id).one()
    assert str(seeded["character_node"].id) not in json.dumps(restored_layout.layout_json)

    restored_asset = db.query(models.MediaAsset).filter_by(project_id=restored.id).one()
    assert restored_asset.owner_id == owner_id
    assert restored_asset.storage_key.startswith(f"uploads/{owner_id}/{restored.id}/assets/")
    restored_path = exports.DATA_DIR / restored_asset.storage_key
    assert restored_path.is_file()
    assert restored_path.read_bytes() == seeded["image"]
    assert restored_character.image_media_id == restored_asset.id
    restored_memory_run = db.query(models.MemoryBuildRun).filter_by(
        project_id=restored.id
    ).one()
    restored_memory_artifact = db.query(models.MemoryBuildArtifact).filter_by(
        run_id=restored_memory_run.id
    ).one()
    assert restored_memory_artifact.content == "memory checkpoint output"
    assert restored_memory_artifact.run_id == restored_memory_run.id

    restored_conversation = db.query(models.AgentConversation).filter_by(
        project_id=restored.id
    ).one()
    assert restored_conversation.created_by_user_id == owner_id
    assert restored_conversation.provider_profile_id is None
    restored_run = db.query(models.AgentRun).filter_by(project_id=restored.id).one()
    restored_message = db.query(models.AgentMessage).filter_by(project_id=restored.id).one()
    assert restored_run.conversation_id == restored_conversation.id
    assert restored_run.message_id == restored_message.id
    assert restored_message.conversation_id == restored_conversation.id
    assert db.query(models.AgentEvent).filter_by(project_id=restored.id).one().run_id == restored_run.id
    assert db.query(models.AgentEvent).filter_by(
        project_id=restored.id, event_type="message_delta"
    ).count() == 0
    assert db.query(models.AgentToolCall).filter_by(project_id=restored.id).one().run_id == restored_run.id

    restored_change_set = db.query(models.ChangeSet).filter_by(project_id=restored.id).one()
    restored_proposal = db.query(models.Proposal).filter_by(project_id=restored.id).one()
    assert restored_proposal.change_set_id == restored_change_set.id
    assert restored_proposal.target_id == restored_character.id

    assert db.query(models.Character).filter_by(project_id=seeded["other"].id).count() == 1


def test_restore_rejects_asset_traversal_and_cross_owner_export(db: Session) -> None:
    seeded = _seed_feature_workspace(db)
    owner_id = db.info["test_user_id"]
    with pytest.raises(LookupError):
        export_project_zip(db, seeded["project"].id, owner_id="another-owner")

    archive = export_project_zip(db, seeded["project"].id, owner_id=owner_id)
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as source, zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED
    ) as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        target.writestr("assets/../escape.bin", b"not allowed")
    with pytest.raises(ValueError, match="不安全路径"):
        restore_project_zip(db, output.getvalue(), owner_id=owner_id)
    assert not (exports.DATA_DIR / "escape.bin").exists()


def test_restore_accepts_2_0_archive_without_feature_members(db: Session) -> None:
    project = _project(db, "旧格式故事")
    _chapter(db, project)
    db.commit()
    archive = export_project_zip(db, project.id, owner_id=db.info["test_user_id"])
    old_archive = _rewrite_archive(
        archive,
        schema_version="2.0",
        drop_prefixes=(
            "summaries.json",
            "summary_revisions.json",
            "characters.json",
            "character_revisions.json",
            "graph_nodes.json",
            "graph_edges.json",
            "graph_layout.json",
            "assets.json",
            "assets_manifest.json",
            "assets/",
            "assistant_",
        ),
    )
    restored = restore_project_zip(db, old_archive, owner_id=db.info["test_user_id"])
    assert db.query(models.Chapter).filter_by(project_id=restored.id).count() == 1
    assert db.query(models.Character).filter_by(project_id=restored.id).count() == 0


def test_restore_failure_cleans_new_upload_sidecars(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = _seed_feature_workspace(db)
    owner_id = db.info["test_user_id"]
    source_bytes = b"portable source"
    source = models.ImportSource(
        project_id=seeded["project"].id,
        filename="source.txt",
        source_hash=content_hash(source_bytes),
        encoding="utf-8",
        stored_name=f"{owner_id}/{seeded['project'].id}/{content_hash(source_bytes)}.source",
        byte_size=len(source_bytes),
    )
    source_path = exports.DATA_DIR / "uploads" / source.stored_name
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(source_bytes)
    db.add(source)
    db.commit()

    archive = export_project_zip(db, seeded["project"].id, owner_id=owner_id)
    before = {
        path
        for path in (exports.DATA_DIR / "uploads" / owner_id).glob("*")
        if path.is_dir()
    }

    def fail_commit() -> None:
        raise RuntimeError("simulated restore commit failure")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="simulated restore commit failure"):
        restore_project_zip(db, archive, owner_id=owner_id)

    after = {
        path
        for path in (exports.DATA_DIR / "uploads" / owner_id).glob("*")
        if path.is_dir()
    }
    assert after == before
    assert source_path.is_file()
