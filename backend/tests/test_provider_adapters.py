"""Protocol and tenant-provider regression tests with an in-memory fake service."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from backend.app import models
from backend.app.db import Base, create_engine_for_url
from backend.app.routers import providers as provider_router
from backend.app.services import providers
from backend.app.services.generation import create_generation_run


def test_parse_structured_accepts_json_schema_union_types() -> None:
    schema = {
        "type": "object",
        "properties": {"target_id": {"type": ["string", "null"]}},
        "required": ["target_id"],
    }

    assert providers.parse_structured('{"target_id":null}', schema) == {
        "target_id": None
    }
    assert providers.parse_structured('{"target_id":"character-1"}', schema) == {
        "target_id": "character-1"
    }


def test_parse_structured_rejects_values_outside_union_types() -> None:
    schema = {
        "type": "object",
        "properties": {"target_id": {"type": ["string", "null"]}},
        "required": ["target_id"],
    }

    with pytest.raises(providers.StructuredOutputError, match="string.*null"):
        providers.parse_structured('{"target_id":42}', schema)


def _profile(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": "provider-1",
        "owner_id": "user-1",
        "name": "Anthropic test",
        "base_url": "https://api.anthropic.com/v1",
        "protocol": "anthropic_messages",
        "api_version": None,
        "anthropic_workspace_id": None,
        "max_output_tokens": 321,
        "model_role_mapping": {"writer": "claude-test", "extractor": "claude-test"},
        "context_length": 32_000,
        "timeout_seconds": 5,
        "capabilities": {"json_schema": True},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        self.values[(service, username)] = value

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


def test_credentials_are_tenant_scoped_and_never_use_env(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeKeyring()
    monkeypatch.setattr(providers, "keyring", fake)
    monkeypatch.setenv("NOVEL_AUTO_WRITE_API_KEY", "must-not-be-read")
    first = _profile()
    second = _profile(owner_id="user-2")
    providers.set_api_key(first, "secret-1")
    assert providers.credential_key(first) == "user-1:provider-1"
    assert providers.get_api_key(first) == "secret-1"
    assert providers.get_api_key(second) is None
    monkeypatch.setattr(providers, "keyring", None)
    assert providers.get_api_key(first) is None


def test_account_credential_cleanup_includes_legacy_key_and_is_recoverable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeKeyring()
    monkeypatch.setattr(providers, "keyring", fake)
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'key-cleanup.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)() as db:
        user = models.User(
            email="cleanup@example.test",
            email_normalized="cleanup@example.test",
            password_hash="test",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        profile = models.ProviderProfile(
            owner_id=user.id,
            name="private",
            base_url="http://127.0.0.1:1234/v1",
            protocol="chat_completions",
            model_role_mapping={"default": "model"},
        )
        db.add(profile)
        db.commit()
        tenant_username = providers.credential_key(profile)
        legacy_username = profile.id
        fake.set_password(providers.KEYRING_SERVICE, tenant_username, "tenant-key")
        fake.set_password(providers.KEYRING_SERVICE, legacy_username, "legacy-key")

        journal = providers.delete_user_credentials(db, user.id)
        assert fake.get_password(providers.KEYRING_SERVICE, tenant_username) is None
        assert fake.get_password(providers.KEYRING_SERVICE, legacy_username) is None
        journal.restore()
        assert fake.get_password(providers.KEYRING_SERVICE, tenant_username) == "tenant-key"
        assert fake.get_password(providers.KEYRING_SERVICE, legacy_username) == "legacy-key"
    engine.dispose()


def test_provider_update_restores_old_key_when_database_commit_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeKeyring()
    monkeypatch.setattr(providers, "keyring", fake)
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'key-compensation.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    db: Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        user = models.User(
            email="key-owner@example.test",
            email_normalized="key-owner@example.test",
            password_hash="test",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        profile = models.ProviderProfile(
            owner_id=user.id,
            name="private",
            base_url="http://127.0.0.1:1234/v1",
            protocol="chat_completions",
            model_role_mapping={"default": "model"},
        )
        db.add(profile)
        db.commit()
        providers.set_api_key(profile, "old-private-key")
        db.commit()

        def fail_commit() -> None:
            raise RuntimeError("simulated commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            provider_router.update_provider(
                profile.id,
                provider_router.ProviderPatch(api_key="new-private-key"),
                user,
                db,
            )
        assert providers.get_api_key(profile) == "old-private-key"
    finally:
        db.close()
        engine.dispose()


def test_provider_create_keeps_discoverable_row_if_key_metadata_commit_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeKeyring()
    monkeypatch.setattr(providers, "keyring", fake)
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'key-journal.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        user = models.User(
            email="journal@example.test",
            email_normalized="journal@example.test",
            password_hash="test",
            is_email_verified=True,
        )
        db.add(user)
        db.commit()
        original_commit = db.commit
        calls = 0

        def fail_second_commit() -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated metadata commit failure")
            original_commit()

        monkeypatch.setattr(db, "commit", fail_second_commit)
        payload = provider_router.ProviderPayload(
            name="discoverable",
            base_url="http://127.0.0.1:1234/v1",
            default_model="model",
            api_key="new-private-key",
        )
        with pytest.raises(RuntimeError, match="metadata commit failure"):
            provider_router.create_provider(payload, user, db)
        owner_id = user.id

    with factory() as verification:
        profile = verification.query(models.ProviderProfile).filter_by(owner_id=owner_id).one()
        assert providers.get_api_key(profile) == "new-private-key"
        assert providers.credential_key(profile) in {username for _service, username in fake.values}
    engine.dispose()


def test_anthropic_payload_promotes_system_and_emits_structured_output() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"request-id": "req-test"},
            json={
                "id": "msg-test",
                "content": [{"type": "thinking", "thinking": "ignore"}, {"type": "text", "text": "{}"}],
                "usage": {"input_tokens": 2, "output_tokens": 3},
                "stop_reason": "end_turn",
            },
        )

    async def call() -> providers.ProviderResponse:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            profile = _profile()
            return await providers.AnthropicMessagesProvider(profile, client=client).complete(
                [
                    {"role": "system", "content": "system rule"},
                    {"role": "user", "content": "one"},
                    {"role": "user", "content": "two"},
                    {"role": "assistant", "content": "answer"},
                ],
                response_schema={"type": "object"},
            )
        finally:
            await client.aclose()

    response = asyncio.run(call())
    body = json.loads(seen[0].content)
    assert response.content == "{}"
    assert response.request_id == "req-test"
    assert response.usage["input_tokens"] == 2
    assert body["system"] == "system rule"
    assert body["messages"] == [
        {"role": "user", "content": "one\n\ntwo"},
        {"role": "assistant", "content": "answer"},
    ]
    assert body["max_tokens"] == 321
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in body


def test_anthropic_stream_yields_text_and_ignores_thinking() -> None:
    stream = (
        "event: ping\n\ndata: {\"type\":\"ping\"}\n\n"
        "event: content_block_delta\n\ndata: {\"type\":\"content_block_delta\",\"delta\":{\"type\":\"thinking_delta\",\"thinking\":\"x\"}}\n\n"
        "event: content_block_delta\n\ndata: {\"type\":\"content_block_delta\",\"delta\":{\"type\":\"text_delta\",\"text\":\"甲\"}}\n\n"
        "event: content_block_delta\n\ndata: {\"type\":\"content_block_delta\",\"delta\":{\"type\":\"text_delta\",\"text\":\"乙\"}}\n\n"
    ).encode()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream)

    async def call() -> list[str]:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            adapter = providers.AnthropicMessagesProvider(_profile(capabilities={}), client=client)
            return [item async for item in adapter.stream([{"role": "user", "content": "x"}])]
        finally:
            await client.aclose()

    assert asyncio.run(call()) == ["甲", "乙"]


def test_provider_errors_include_anthropic_overload_status() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(529, json={"error": {"type": "overloaded_error", "message": "busy"}})

    async def call() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            adapter = providers.AnthropicMessagesProvider(_profile(), client=client)
            with pytest.raises(providers.ProviderError) as error:
                await adapter.complete([{"role": "user", "content": "x"}])
            assert error.value.status_code == 529
            assert error.value.retryable is True
        finally:
            await client.aclose()

    asyncio.run(call())


def test_provider_error_cannot_persist_an_echoed_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-test-echo-must-be-redacted"
    fake = _FakeKeyring()
    monkeypatch.setattr(providers, "keyring", fake)
    profile = _profile()
    providers.set_api_key(profile, secret)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": f"invalid credential {secret}"}},
        )

    async def call() -> providers.ProviderError:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            adapter = providers.AnthropicMessagesProvider(profile, client=client)
            with pytest.raises(providers.ProviderError) as error:
                await adapter.complete([{"role": "user", "content": "x"}])
            return error.value
        finally:
            await client.aclose()

    error = asyncio.run(call())
    assert secret not in str(error)
    assert "[REDACTED]" in str(error)


@pytest.mark.parametrize(
    ("status_code", "retryable", "uncertain"),
    [
        (401, True, False),
        (403, True, False),
        (429, True, False),
        (500, True, True),
        (504, True, True),
        (529, True, True),
    ],
)
def test_anthropic_http_failures_are_mapped(
    status_code: int, retryable: bool, uncertain: bool
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"error": {"type": "test_error", "message": "provider failed"}},
        )

    async def call() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            adapter = providers.AnthropicMessagesProvider(_profile(), client=client)
            with pytest.raises(providers.ProviderError) as error:
                await adapter.complete([{"role": "user", "content": "x"}])
            assert error.value.status_code == status_code
            assert error.value.retryable is retryable
            assert error.value.uncertain is uncertain
        finally:
            await client.aclose()

    asyncio.run(call())


def test_anthropic_stream_error_event_is_not_silently_ignored() -> None:
    body = (
        b'event: error\n'
        b'data: {"type":"error","error":{"type":"overloaded_error","message":"busy"}}\n\n'
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async def call() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            adapter = providers.AnthropicMessagesProvider(_profile(), client=client)
            with pytest.raises(providers.ProviderError) as error:
                _ = [item async for item in adapter.stream([{"role": "user", "content": "x"}])]
            assert error.value.status_code == 529
            assert error.value.retryable is True
        finally:
            await client.aclose()

    asyncio.run(call())


def test_anthropic_plain_json_fallback_repairs_once() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        text = "not-json" if len(requests) == 1 else '{"answer":"ok"}'
        return httpx.Response(
            200,
            json={
                "id": f"msg-{len(requests)}",
                "content": [{"type": "text", "text": text}],
                "usage": {},
            },
        )

    async def call() -> dict[str, str]:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            adapter = providers.AnthropicMessagesProvider(
                _profile(capabilities={"json_schema": False}), client=client
            )
            result, _response = await adapter.structured(
                [{"role": "user", "content": "return json"}],
                {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                },
            )
            return result
        finally:
            await client.aclose()

    assert asyncio.run(call()) == {"answer": "ok"}
    assert len(requests) == 2
    assert all("output_config" not in payload for payload in requests)


def test_anthropic_unsupported_structured_output_falls_back_to_plain_json() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if "output_config" in payload:
            return httpx.Response(
                400,
                json={"error": {"type": "invalid_request_error", "message": "unsupported"}},
            )
        return httpx.Response(
            200,
            json={
                "id": "msg-fallback",
                "content": [{"type": "text", "text": '{"answer":"ok"}'}],
                "usage": {},
            },
        )

    async def call() -> dict[str, str]:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            adapter = providers.AnthropicMessagesProvider(
                _profile(capabilities={}), client=client
            )
            result, _response = await adapter.structured(
                [{"role": "user", "content": "return json"}],
                {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                },
            )
            return result
        finally:
            await client.aclose()

    assert asyncio.run(call()) == {"answer": "ok"}
    assert len(requests) == 2
    assert "output_config" in requests[0]
    assert "output_config" not in requests[1]


def test_anthropic_bad_json_and_empty_content_are_failures() -> None:
    responses = [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"content": [], "usage": {}}),
    ]

    async def call(response: httpx.Response) -> providers.ProviderError:
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: response))
        try:
            adapter = providers.AnthropicMessagesProvider(_profile(), client=client)
            with pytest.raises(providers.ProviderError) as error:
                await adapter.complete([{"role": "user", "content": "x"}])
            return error.value
        finally:
            await client.aclose()

    malformed = asyncio.run(call(responses[0]))
    assert malformed.retryable is True
    assert malformed.uncertain is True
    empty = asyncio.run(call(responses[1]))
    assert "空响应" in str(empty)


def test_openai_responses_uses_input_and_text_format() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "resp-test",
                "output": [{"content": [{"type": "output_text", "text": "{}"}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    async def call() -> None:
        profile = _profile(
            protocol="responses",
            base_url="https://api.openai.com/v1",
            model_role_mapping={"writer": "gpt-test"},
        )
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await providers.OpenAICompatibleProvider(profile, client=client).complete(
                [{"role": "user", "content": "x"}],
                response_schema={"type": "object"},
            )
        finally:
            await client.aclose()

    asyncio.run(call())
    assert seen[0].url.path == "/v1/responses"
    payload = json.loads(seen[0].content)
    assert payload["input"] == [{"role": "user", "content": "x"}]
    assert payload["text"]["format"]["type"] == "json_schema"
    assert "messages" not in payload


def test_no_demo_fallback_and_missing_provider_does_not_create_entities(tmp_path) -> None:
    with pytest.raises(providers.ProviderRequired):
        providers.provider_for(None)
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'provider.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    db: Session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        user = models.User(
            email="writer@example.com",
            email_normalized="writer@example.com",
            password_hash="not-used",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        project = models.Project(owner_id=user.id, name="No model yet")
        db.add(project)
        db.commit()
        with pytest.raises(providers.ProviderRequired):
            create_generation_run(db, project, {"idempotency_key": "no-provider"})
        assert db.query(models.Chapter).count() == 0
        assert db.query(models.Job).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_public_provider_urls_are_allowlist_first() -> None:
    with pytest.raises(providers.ProviderError):
        providers.validate_provider_url("https://evil.example/v1", public_mode=True)
    with pytest.raises(providers.ProviderError):
        providers.validate_provider_url("http://api.openai.com/v1", public_mode=True)


def test_public_requests_pin_dns_and_reject_a_rebinding_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(providers, "_public_mode", lambda: True)
    answers = iter(["8.8.8.8", "127.0.0.1"])

    def changing_dns(*_args: object, **_kwargs: object):
        address = next(answers)
        return [(2, 1, 6, "", (address, 443))]

    monkeypatch.setattr(providers.socket, "getaddrinfo", changing_dns)
    adapter = providers.AnthropicMessagesProvider(_profile())
    validated_url = adapter._url("messages")
    with pytest.raises(providers.ProviderError, match="私网或本机"):
        providers._pinned_request_target(validated_url, {"x-api-key": "test"})

    monkeypatch.setattr(
        providers.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("8.8.8.8", 443))],
    )
    validated_url = adapter._url("messages")
    pinned, headers, extensions = providers._pinned_request_target(
        validated_url, {"x-api-key": "test"}
    )
    assert pinned == "https://8.8.8.8/v1/messages"
    assert headers["Host"] == "api.anthropic.com"
    assert extensions == {"sni_hostname": "api.anthropic.com"}
