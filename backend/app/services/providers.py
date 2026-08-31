"""OpenAI-compatible model adapters and the explicitly labelled demo provider."""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import urljoin

import httpx

try:  # keyring is optional in headless test environments
    import keyring
except Exception:  # pragma: no cover - depends on host keyring backends
    keyring = None  # type: ignore[assignment]


PROMPT_VERSION = "workflow-v1"
KEYRING_SERVICE = "novel-auto-write"
T = TypeVar("T")


class ProviderError(RuntimeError):
    """An actionable model-provider failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        uncertain: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.uncertain = uncertain


class StructuredOutputError(ProviderError):
    pass


@dataclass(slots=True)
class ProviderResponse:
    content: str
    raw: dict[str, Any]
    model: str
    usage: dict[str, Any]
    request_hash: str | None = None


def _profile_value(profile: Any, name: str, default: Any = None) -> Any:
    if isinstance(profile, Mapping):
        return profile.get(name, default)
    return getattr(profile, name, default)


def profile_id(profile: Any) -> str:
    return str(_profile_value(profile, "id", _profile_value(profile, "name", "default")))


def get_api_key(profile: Any) -> str | None:
    """Read a provider key from the operating-system credential store only."""

    if keyring is None:
        return os.environ.get("NOVEL_AUTO_WRITE_API_KEY") or None
    try:
        value = keyring.get_password(KEYRING_SERVICE, profile_id(profile))
        return value or None
    except Exception:  # pragma: no cover - platform-specific keyring failure
        return os.environ.get("NOVEL_AUTO_WRITE_API_KEY") or None


def set_api_key(profile: Any, value: str) -> None:
    if not value:
        raise ValueError("API Key 不能为空")
    if keyring is None:
        raise ProviderError("当前系统没有可用的凭据库")
    keyring.set_password(KEYRING_SERVICE, profile_id(profile), value)


def delete_api_key(profile: Any) -> None:
    if keyring is None:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, profile_id(profile))
    except Exception:
        pass


def _models(profile: Any) -> dict[str, Any]:
    value = _profile_value(profile, "models_json", None)
    if value is None:
        value = _profile_value(profile, "model_role_mapping", {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    return value if isinstance(value, dict) else {}


def model_for(profile: Any, role: str = "writer") -> str:
    configured = _models(profile)
    value = (
        configured.get(role) or configured.get("default") or _profile_value(profile, "model", None)
    )
    if isinstance(value, dict):
        value = value.get("model")
    return str(value or "demo-writer")


def _base_url(profile: Any) -> str:
    base = str(_profile_value(profile, "base_url", "http://127.0.0.1:1234/v1") or "").strip()
    if not base:
        base = "http://127.0.0.1:1234/v1"
    return base.rstrip("/") + "/"


def _protocol(profile: Any) -> str:
    return str(
        _profile_value(profile, "protocol", "chat_completions") or "chat_completions"
    ).lower()


def _context_limit(profile: Any) -> int:
    try:
        return int(
            _profile_value(
                profile, "context_limit", _profile_value(profile, "context_length", 32768)
            )
            or 32768
        )
    except (TypeError, ValueError):
        return 32768


def _timeout(profile: Any) -> float:
    try:
        return float(_profile_value(profile, "timeout_seconds", 120) or 120)
    except (TypeError, ValueError):
        return 120.0


def _json_schema_supported(profile: Any) -> bool:
    direct = _profile_value(profile, "supports_json_schema", None)
    if direct is not None:
        return bool(direct)
    capabilities = _profile_value(profile, "capabilities", {}) or {}
    if isinstance(capabilities, str):
        try:
            capabilities = json.loads(capabilities)
        except json.JSONDecodeError:
            capabilities = {}
    return (
        bool(capabilities.get("json_schema") or capabilities.get("supports_json_schema"))
        if isinstance(capabilities, dict)
        else False
    )


def _extract_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ProviderError("模型返回不是 JSON 对象")
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            return "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in content
            )
        if isinstance(content, str):
            return content
    # Responses API compatibility (and several local servers' variants).
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text
    output = payload.get("output")
    if isinstance(output, list):
        chunks: list[str] = []
        for item in output:
            for content in item.get("content", []) if isinstance(item, dict) else []:
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    chunks.append(content["text"])
        if chunks:
            return "".join(chunks)
    raise ProviderError("模型返回空响应")


def _clean_json_text(value: str) -> str:
    value = value.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", value, re.I | re.S)
    return fenced.group(1).strip() if fenced else value


def parse_structured(value: str, schema: Mapping[str, Any] | None = None) -> Any:
    try:
        parsed = json.loads(_clean_json_text(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise StructuredOutputError("模型返回的结构化 JSON 无法解析") from exc
    if schema:
        _validate_schema(parsed, schema)
    return parsed


def _validate_schema(value: Any, schema: Mapping[str, Any], path: str = "$") -> None:
    """Small JSON-Schema validator for provider responses.

    Pydantic remains the preferred caller-side validator; these checks cover the
    subset needed by workflow artifacts and give a useful error for local models
    that do not support Structured Outputs.
    """

    expected = schema.get("type")
    valid = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if expected in valid and not valid[expected]:
        raise StructuredOutputError(f"结构化输出 {path} 应为 {expected}")
    if isinstance(value, dict):
        required = schema.get("required", [])
        for field in required:
            if field not in value:
                raise StructuredOutputError(f"结构化输出缺少字段 {path}.{field}")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for field, child_schema in properties.items():
                if field in value and isinstance(child_schema, Mapping):
                    _validate_schema(value[field], child_schema, f"{path}.{field}")
    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        for index, item in enumerate(value):
            _validate_schema(item, schema["items"], f"{path}[{index}]")


class OpenAICompatibleProvider:
    """Async adapter for ``/v1/chat/completions`` and optional Responses API."""

    def __init__(self, profile: Any, *, client: httpx.AsyncClient | None = None):
        self.profile = profile
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> OpenAICompatibleProvider:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_timeout(self.profile))
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = get_api_key(self.profile)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def _request(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        close_after = False
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_timeout(self.profile))
            close_after = self._owns_client
        try:
            try:
                response = await self._client.post(
                    urljoin(_base_url(self.profile), endpoint),
                    headers=self._headers(),
                    json=payload,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise ProviderError(
                    f"模型请求超时或网络中断：{exc}", retryable=True, uncertain=True
                ) from exc
            if response.status_code >= 400:
                retryable = response.status_code == 429 or response.status_code >= 500
                raise ProviderError(
                    f"模型服务返回 HTTP {response.status_code}",
                    status_code=response.status_code,
                    retryable=retryable,
                    uncertain=response.status_code >= 500,
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderError(
                    "模型服务返回了错误 JSON", retryable=True, uncertain=True
                ) from exc
            if not isinstance(payload, dict):
                raise ProviderError("模型服务返回格式错误", retryable=True, uncertain=True)
            return payload
        finally:
            if close_after and self._client is not None:
                await self._client.aclose()
                self._client = None

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        role: str = "writer",
        model: str | None = None,
        temperature: float = 0.7,
        response_schema: Mapping[str, Any] | None = None,
    ) -> ProviderResponse:
        model = model or model_for(self.profile, role)
        if _protocol(self.profile) == "responses":
            payload: dict[str, Any] = {
                "model": model,
                "input": messages,
                "temperature": temperature,
            }
            endpoint = "responses"
        else:
            payload = {"model": model, "messages": messages, "temperature": temperature}
            endpoint = "chat/completions"
        if response_schema and _json_schema_supported(self.profile):
            if endpoint == "chat/completions":
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "workflow_output",
                        "schema": response_schema,
                        "strict": True,
                    },
                }
            else:
                payload["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "workflow_output",
                        "schema": response_schema,
                        "strict": True,
                    }
                }
        raw = await self._request(endpoint, payload)
        content = _extract_content(raw)
        return ProviderResponse(
            content, raw, model, raw.get("usage", {}) if isinstance(raw, dict) else {}
        )

    async def structured(
        self,
        messages: list[dict[str, str]],
        schema: Mapping[str, Any],
        *,
        role: str = "extractor",
        model: str | None = None,
    ) -> tuple[Any, ProviderResponse]:
        response = await self.complete(
            messages, role=role, model=model, response_schema=schema, temperature=0
        )
        try:
            return parse_structured(response.content, schema), response
        except StructuredOutputError as first_error:
            # One repair request is safe because it does not mutate project state.
            repair = [
                *messages,
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": f"请仅返回符合以下 JSON Schema 的 JSON，不要 Markdown：{json.dumps(schema, ensure_ascii=False)}",
                },
            ]
            try:
                repaired = await self.complete(
                    repair, role=role, model=model, response_schema=schema, temperature=0
                )
                return parse_structured(repaired.content, schema), repaired
            except (ProviderError, StructuredOutputError) as second_error:
                raise StructuredOutputError(
                    f"结构化输出校验失败：{first_error}; 修复请求也失败：{second_error}"
                ) from second_error

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        role: str = "writer",
        model: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Yield text deltas from an OpenAI-compatible SSE response."""

        model = model or model_for(self.profile, role)
        payload = {"model": model, "messages": messages, "temperature": temperature, "stream": True}
        client = self._client or httpx.AsyncClient(timeout=_timeout(self.profile))
        owns = self._client is None
        try:
            try:
                async with client.stream(
                    "POST",
                    urljoin(_base_url(self.profile), "chat/completions"),
                    headers=self._headers(),
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        raise ProviderError(
                            f"模型服务返回 HTTP {response.status_code}",
                            status_code=response.status_code,
                            retryable=response.status_code in (429, 500, 502, 503, 504),
                            uncertain=response.status_code >= 500,
                        )
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            payload_item = json.loads(data)
                        except json.JSONDecodeError as exc:
                            raise ProviderError(
                                "流式响应包含错误 JSON", retryable=True, uncertain=True
                            ) from exc
                        choices = payload_item.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            if isinstance(delta, dict) and delta.get("content"):
                                yield str(delta["content"])
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise ProviderError(
                    f"流式模型请求中断：{exc}", retryable=True, uncertain=True
                ) from exc
        finally:
            if owns:
                await client.aclose()

    async def test_connection(self) -> dict[str, Any]:
        client = self._client or httpx.AsyncClient(timeout=_timeout(self.profile))
        owns = self._client is None
        try:
            try:
                response = await client.get(
                    urljoin(_base_url(self.profile), "models"), headers=self._headers()
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise ProviderError(f"连接超时：{exc}", retryable=True) from exc
            if response.status_code >= 400:
                raise ProviderError(
                    f"连接测试失败 HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            return {"ok": True, "status_code": response.status_code, "models": response.json()}
        finally:
            if owns:
                await client.aclose()


class DemoProvider:
    """Deterministic local provider for reviewable samples when no key is set."""

    name = "demo"
    explicitly_demo = True

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        role: str = "writer",
        model: str | None = None,
        **_: Any,
    ) -> ProviderResponse:
        user_text = next(
            (item.get("content", "") for item in reversed(messages) if item.get("role") == "user"),
            "",
        )
        if role in {"extractor", "auditor", "style_auditor", "planner"}:
            content = json.dumps(
                {
                    "facts": [],
                    "issues": [],
                    "canon_changes": [],
                    "summary": "演示模型未提取新增事实",
                },
                ensure_ascii=False,
            )
        else:
            content = (
                "【演示样稿】\n\n这是由本地 Demo Provider 生成的可审阅草稿。正式提交前请检查人物、时间线与硬约束。\n\n"
                + user_text[:120]
            )
        return ProviderResponse(content, {"demo": True, "role": role}, model or "demo-writer", {})

    async def structured(
        self,
        messages: list[dict[str, str]],
        schema: Mapping[str, Any],
        *,
        role: str = "extractor",
        model: str | None = None,
    ) -> tuple[Any, ProviderResponse]:
        response = await self.complete(messages, role=role, model=model)
        return parse_structured(response.content, schema), response

    async def test_connection(self) -> dict[str, Any]:
        return {
            "ok": True,
            "demo": True,
            "message": "Demo Provider 仅生成可审阅样稿，不代表真实模型",
        }


def provider_for(profile: Any | None) -> OpenAICompatibleProvider | DemoProvider:
    if profile is None or str(_profile_value(profile, "protocol", "demo")).lower() == "demo":
        return DemoProvider()
    return OpenAICompatibleProvider(profile)
