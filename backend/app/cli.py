"""Operational commands for the single-host deployment.

Run with ``python -m app.cli`` from the repository root (with ``backend`` on
``PYTHONPATH``).  In particular, legacy data is never claimed implicitly by
the first account that registers.
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from . import db as db_module
from .config import AUTH_MODE, LEGACY_OWNER_ID
from .models import AuditLog, Project, ProviderProfile, User
from .security import normalize_email, normalize_username
from .services.providers import migrate_legacy_credentials
from .services.storage import migrate_import_storage


def claim_legacy(
    email: str | None = None, *, username: str | None = None
) -> dict[str, int | str]:
    """Transfer quarantined old rows to an existing verified account."""

    if bool(email) == bool(username):
        raise RuntimeError("必须指定 --email 或 --username，且只能指定一个")
    if AUTH_MODE == "email" and username is not None:
        raise RuntimeError("当前部署使用邮箱认证，请使用 --email")
    if AUTH_MODE == "username" and email is not None:
        raise RuntimeError("当前部署使用用户名认证，请使用 --username")
    normalized_email: str | None = None
    normalized_username: str | None = None
    if username:
        normalized_username = normalize_username(username)
    else:
        normalized_email = normalize_email(email or "")
    db_module.init_db()
    db = db_module.SessionLocal()
    storage_migration = None
    credential_migration = None
    try:
        if normalized_username is not None:
            user = db.scalar(select(User).where(User.username_normalized == normalized_username))
        else:
            user = db.scalar(select(User).where(User.email_normalized == normalized_email))
        if user is None:
            raise RuntimeError(
                "指定用户名尚未注册" if normalized_username is not None else "指定邮箱尚未注册"
            )
        if not user.is_active or (AUTH_MODE == "email" and not user.is_email_verified):
            raise RuntimeError(
                "指定账号必须已激活并完成邮箱验证"
                if AUTH_MODE == "email"
                else "指定账号必须已激活"
            )
        if user.id == LEGACY_OWNER_ID:
            raise RuntimeError("legacy_owner 不可登录或领取自身数据")
        projects = db.scalars(
            select(Project).where(Project.owner_id == LEGACY_OWNER_ID)
        ).all()
        providers = db.scalars(
            select(ProviderProfile).where(ProviderProfile.owner_id == LEGACY_OWNER_ID)
        ).all()
        credential_migration = migrate_legacy_credentials(providers, user.id)
        for project in projects:
            project.owner_id = user.id
        for provider in providers:
            provider.owner_id = user.id
        db.flush()
        storage_migration = migrate_import_storage(db)
        db.add(
            AuditLog(
                actor_user_id=user.id,
                actor="operator",
                action="legacy.claimed",
                entity_type="user",
                entity_id=user.id,
                after_json={
                    "project_count": len(projects),
                    "provider_count": len(providers),
                    "legacy_owner_id": LEGACY_OWNER_ID,
                },
            )
        )
        db.commit()
        storage_migration.finalize()
        storage_migration = None
        credential_migration.finalize()
        credential_migration = None
        if projects:
            try:
                db_module.rebuild_search_index(
                    db_engine=db.get_bind(),
                    owner_id=user.id,
                )
            except Exception:
                # Ownership and source-file relocation are authoritative;
                # search is derived and startup will rebuild it again.
                pass
        return {
            **(
                {"username": normalized_username}
                if normalized_username is not None
                else {"email": normalized_email}
            ),
            "projects": len(projects),
            "providers": len(providers),
        }
    except Exception:
        db.rollback()
        if storage_migration is not None:
            storage_migration.restore()
        if credential_migration is not None:
            credential_migration.restore()
        raise
    finally:
        db.close()


def migrate() -> None:
    """Apply migrations and rebuild all derived/local storage projections."""

    db_module.init_db()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    claim = commands.add_parser("claim-legacy", help="把旧单用户数据交给当前模式的可登录账号")
    identity = claim.add_mutually_exclusive_group(required=True)
    identity.add_argument("--email", help="已注册且已验证的账号邮箱")
    identity.add_argument("--username", help="已注册的用户名账号")
    commands.add_parser("migrate", help="执行数据库迁移/兼容初始化")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "claim-legacy":
            result = claim_legacy(email=args.email, username=args.username)
        else:
            migrate()
            result = {"ok": True}
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["claim_legacy", "main"]
