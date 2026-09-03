"""Provider adapters and the per-user credential boundary.

The application deliberately keeps provider credentials out of SQLAlchemy
rows, generation snapshots and exports.  A profile only describes *where* and
*how* to call a model; :class:`CredentialStore` resolves the user's secret at
request time using the operating-system credential manager.

Three wire protocols are supported: ``chat_completions``, ``responses`` and
native ``anthropic_messages``.  The old demo provider and process-wide API key
fallback are intentionally gone. Tests can inject an ``httpx.AsyncClient`` or
monkeypatch the keyring module without changing production behaviour.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from sqlalchemy import select

try:  # keyring is optional in isolated unit tests, not an env-var fallback
    import keyring
except Exception:  # pragma: no cover - platform-specific import
    keyring = None  # type: ignore[assignment]


PROMPT_VERSION = "workflow-v1"
KEYRING_SERVICE = "novel-auto-write"
SUPPORTED_PROTOCOLS = {"chat_completions", "responses", "anthropic_messages"}
_VISION_ALIASES = ("image_input", "supports_vision", "multimodal")
_TRUE_CAPABILITY_VALUES = frozenset(
    {"1", "true", "yes", "y", "on", "t", "enabled", "enable", "是", "开启"}
)
_FALSE_CAPABILITY_VALUES = frozenset(
    {"", "0", "false", "no", "n", "off", "f", "disabled", "disable", "否", "关闭"}
)
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
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


class ProviderRequired(ProviderError):
    """Raised before a generation entity is created when no provider is set."""

    code = "provider_required"


class StructuredOutputError(ProviderError):
    pass


@dataclass(slots=True)
class ProviderResponse:
    content: str
    raw: dict[str, Any]
    model: str
    usage: dict[str, Any]
    request_hash: str | None = None
    request_id: str | None = None
    stop_reason: str | None = None


def _profile_value(profile: Any, name: str, default: Any = None) -> Any:
    if isinstance(profile, Mapping):
        return profile.get(name, default)
    return getattr(profile, name, default)


def profile_id(profile: Any) -> str:
    return str(_profile_value(profile, "id", _profile_value(profile, "name", "default")))


def profile_owner_id(profile: Any) -> str:
    return str(_profile_value(profile, "owner_id", "legacy") or "legacy")


def credential_key(profile: Any) -> str:
    """Return the tenant-scoped keyring username."""

    return f"{profile_owner_id(profile)}:{profile_id(profile)}"


class CredentialStore:
    """Small OS credential-manager abstraction used by every provider call."""

    service_name = KEYRING_SERVICE

    def get_username(self, username: str, *, require_backend: bool = False) -> str | None:
        """Read one explicit keyring entry without changing its namespace."""

        if keyring is None:
            if require_backend:
                raise ProviderError("系统凭据库不可用，无法安全处理 Provider 凭据")
            return None
        try:
            value = keyring.get_password(self.service_name, username)
        except Exception as exc:  # pragma: no cover - host backend specific
            if (
                not require_backend
                and exc.__class__.__name__.lower() in {"nokeyringerror", "backendnotfound"}
            ):
                return None
            raise ProviderError(f"无法读取系统凭据库：{exc}") from exc
        return value or None

    def get(self, profile: Any) -> str | None:
        return self.get_username(credential_key(profile))

    def set_username(self, username: str, value: str) -> None:
        """Write one explicit keyring entry; the value never leaves memory."""

        if not value:
            raise ValueError("API Key 不能为空")
        if keyring is None:
            raise ProviderError("当前系统没有可用的系统凭据库")
        try:
            keyring.set_password(self.service_name, username, value)
        except Exception as exc:  # pragma: no cover - host backend specific
            raise ProviderError(f"无法写入系统凭据库：{exc}") from exc

    def set(self, profile: Any, value: str) -> None:
        self.set_username(credential_key(profile), value)

    def delete_username(self, username: str) -> None:
        """Delete one explicit keyring entry and tolerate only true absence."""

        if keyring is None:
            return
        try:
            keyring.delete_password(self.service_name, username)
        except Exception as exc:  # keyring raises when the entry is absent
            message = str(exc).lower()
            name = exc.__class__.__name__.lower()
            if not (
                "notfound" in name
                or any(
                    marker in message
                    for marker in (
                        "not found",
                        "no password",
                        "no such password",
                        "does not exist",
                    )
                )
            ):
                raise ProviderError(f"无法删除系统凭据：{exc}") from exc

    def delete(self, profile: Any) -> None:
        if keyring is None:
            if _profile_value(profile, "api_key_ref", None):
                raise ProviderError("系统凭据库不可用，无法确认用户密钥已删除")
            return
        self.delete_username(credential_key(profile))


credential_store = CredentialStore()


@dataclass(slots=True)
class CredentialJournal:
    """In-memory compensation journal for non-transactional keyring writes."""

    originals: dict[str, str | None]

    def restore(self) -> None:
        for username, original in reversed(tuple(self.originals.items())):
            if original is None:
                credential_store.delete_username(username)
            else:
                credential_store.set_username(username, original)

    def finalize(self) -> None:
        # Drop references to secret strings as soon as the matching database
        # transaction is authoritative.  Nothing from this journal is logged.
        self.originals.clear()


def _remember_credential(
    journal: CredentialJournal, username: str, value: str | None
) -> None:
    if username not in journal.originals:
        journal.originals[username] = value


def get_api_key(profile: Any) -> str | None:
    """Read only the tenant-scoped OS credential; never read an env fallback."""

    # Unsaved connection tests may provide a key in memory.  The transient
    # object is never persisted and this override avoids writing a one-request
    # credential into the user's keyring at all.
    override = _profile_value(profile, "_api_key_override", None)
    if override:
        return str(override)
    return credential_store.get(profile)


def set_api_key(profile: Any, value: str) -> None:
    credential_store.set(profile, value)
    if hasattr(profile, "api_key_ref"):
        profile.api_key_ref = credential_key(profile)


def delete_api_key(profile: Any) -> None:
    credential_store.delete(profile)
    if hasattr(profile, "api_key_ref"):
        profile.api_key_ref = None


def migrate_legacy_credentials(
    profiles: list[Any], new_owner_id: str
) -> CredentialJournal:
    """Move pre-account keyring entries into the tenant-scoped namespace.

    The original desktop release stored a Provider secret under its bare
    ``provider_id``.  Claiming legacy data now moves that entry to
    ``user_id:provider_id`` without ever persisting or logging the secret.
    The returned journal must be restored if the ownership transaction fails.
    """

    journal = CredentialJournal({})
    try:
        for profile in profiles:
            legacy_username = profile_id(profile)
            tenant_username = f"{new_owner_id}:{legacy_username}"
            legacy_value = credential_store.get_username(
                legacy_username, require_backend=True
            )
            tenant_value = credential_store.get_username(
                tenant_username, require_backend=True
            )
            if legacy_value and tenant_value and legacy_value != tenant_value:
                raise ProviderError(
                    "Provider 的旧凭据与新租户凭据冲突，已中止认领"
                )
            if legacy_value:
                _remember_credential(journal, legacy_username, legacy_value)
                _remember_credential(journal, tenant_username, tenant_value)
                if tenant_value is None:
                    credential_store.set_username(tenant_username, legacy_value)
                credential_store.delete_username(legacy_username)
                tenant_value = legacy_value
            if tenant_value and hasattr(profile, "api_key_ref"):
                profile.api_key_ref = tenant_username
        return journal
    except Exception:
        journal.restore()
        raise


def delete_user_credentials(session: Any, user_id: str) -> CredentialJournal:
    """Delete every OS credential owned by ``user_id`` before account removal.

    This intentionally receives a SQLAlchemy session rather than a list from a
    caller so account deletion cannot forget a provider row.  Any credential
    manager failure propagates as :class:`ProviderError`; callers must roll
    back and keep the account intact in that case.  Bare legacy Provider keys
    are removed too, and the returned in-memory journal can compensate a later
    database/storage failure.
    """

    from ..models import ProviderProfile

    profiles = session.scalars(
        select(ProviderProfile).where(ProviderProfile.owner_id == str(user_id))
    ).all()
    journal = CredentialJournal({})
    try:
        for profile in profiles:
            usernames = (credential_key(profile), profile_id(profile))
            for username in usernames:
                value = credential_store.get_username(username, require_backend=True)
                _remember_credential(journal, username, value)
            for username in usernames:
                credential_store.delete_username(username)
            profile.api_key_ref = None
        return journal
    except Exception:
        journal.restore()
        raise


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
        configured.get(role)
        or configured.get("default")
        or _profile_value(profile, "model", None)
    )
    if isinstance(value, Mapping):
        value = value.get("model")
    if not value:
        raise ProviderError(f"Provider 未配置 {role} 角色模型")
    return str(value)


def _protocol(profile: Any) -> str:
    protocol = str(_profile_value(profile, "protocol", "") or "").lower().strip()
    if protocol not in SUPPORTED_PROTOCOLS:
        raise ProviderError(f"不支持的 Provider 协议：{protocol or '未指定'}")
    return protocol


def _base_url(profile: Any) -> str:
    protocol = _protocol(profile)
    default = (
        DEFAULT_ANTHROPIC_BASE_URL
        if protocol == "anthropic_messages"
        else "https://api.openai.com/v1"
    )
    base = str(_profile_value(profile, "base_url", default) or "").strip() or default
    validate_provider_url(base)
    return base.rstrip("/") + "/"


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
        return max(1.0, float(_profile_value(profile, "timeout_seconds", 120) or 120))
    except (TypeError, ValueError):
        return 120.0


def _max_output_tokens(profile: Any) -> int:
    try:
        return max(
            1,
            int(
                _profile_value(
                    profile,
                    "max_output_tokens",
                    _profile_value(profile, "max_tokens", 4096),
                )
                or 4096
            ),
        )
    except (TypeError, ValueError):
        return 4096


def parse_capability_bool(value: Any, *, field: str = "capabilities.vision") -> bool:
    """Parse a capability flag without Python's unsafe truthiness coercion.

    Provider capability JSON historically came from a few different clients,
    some of which sent ``"false"`` or ``0`` as strings.  ``bool("false")``
    would incorrectly advertise a capability, so only an explicit finite set
    of boolean spellings is accepted here.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, float) and value in (0.0, 1.0):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in _TRUE_CAPABILITY_VALUES:
            return True
        if normalized in _FALSE_CAPABILITY_VALUES:
            return False
    raise ValueError(
        f"{field} 必须是布尔值或 0/1（可用 true/false、yes/no、on/off）"
    )


def normalize_capabilities(
    value: Any,
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """Return one canonical capability object.

    ``vision`` is the authoritative key.  The three keys used by older
    clients are accepted as input and removed from the persisted/public form.
    An explicit canonical value, including ``False``, always wins over legacy
    aliases.  When no value is supplied, capabilities fail closed to
    ``vision=False`` while unrelated capability fields are preserved.

    ``strict=False`` is used only when reading legacy rows that predate this
    contract; malformed flags then fail closed instead of making a provider
    row unreadable.  API writes use the strict default and return a 422.
    """

    parsed = value
    if parsed is None:
        parsed = {}
    elif isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except (TypeError, json.JSONDecodeError) as exc:
            if strict:
                raise ValueError("capabilities 必须是 JSON 对象") from exc
            parsed = {}
    if not isinstance(parsed, Mapping):
        if strict:
            raise ValueError("capabilities 必须是 JSON 对象")
        parsed = {}

    result = dict(parsed)
    if "vision" in parsed:
        try:
            vision = parse_capability_bool(parsed["vision"])
        except ValueError:
            if strict:
                raise
            vision = False
    else:
        vision = False
        for alias in _VISION_ALIASES:
            if alias not in parsed:
                continue
            try:
                vision = parse_capability_bool(parsed[alias], field=f"capabilities.{alias}")
                break
            except ValueError:
                if strict:
                    raise

    for alias in _VISION_ALIASES:
        result.pop(alias, None)
    result["vision"] = vision
    return result


def _capabilities(profile: Any) -> dict[str, Any]:
    value = _profile_value(profile, "capabilities", {}) or {}
    return normalize_capabilities(value, strict=False)


def _json_schema_supported(profile: Any) -> bool:
    direct = _profile_value(profile, "supports_json_schema", None)
    if direct is not None:
        return bool(direct)
    capabilities = _capabilities(profile)
    for key in ("json_schema", "supports_json_schema", "structured_outputs"):
        if key in capabilities:
            return bool(capabilities[key])
    # The two native APIs in this release advertise a formal schema field.
    # Attempt it by default and fall back on a clear 4xx unsupported response;
    # generic Chat Completions gateways stay conservative unless enabled.
    return _protocol(profile) in {"responses", "anthropic_messages"}


def _temperature_supported(profile: Any) -> bool:
    capabilities = _capabilities(profile)
    direct = _profile_value(profile, "supports_temperature", None)
    if direct is not None:
        return bool(direct)
    return bool(capabilities.get("temperature") or capabilities.get("supports_temperature"))


def _anthropic_version(profile: Any) -> str:
    return str(
        _profile_value(profile, "api_version", DEFAULT_ANTHROPIC_VERSION)
        or DEFAULT_ANTHROPIC_VERSION
    )


def _public_mode() -> bool:
    configured = os.getenv("NOVEL_PUBLIC_MODE", "")
    if configured:
        return configured.lower() in {"1", "true", "yes", "on", "production", "public"}
    return os.getenv("NOVEL_ENV", "local").lower() in {"production", "prod", "public"}


def _allowed_hosts() -> set[str]:
    configured = os.getenv(
        "NOVEL_ALLOWED_PROVIDER_HOSTS",
        os.getenv("NOVEL_PROVIDER_ALLOWED_HOSTS", ""),
    )
    hosts = {item.strip().lower().rstrip(".") for item in configured.split(",") if item.strip()}
    hosts.update({"api.openai.com", "api.anthropic.com"})
    return hosts


def _hostname_is_private(hostname: str) -> bool:
    lowered = hostname.lower().rstrip(".")
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(lowered)
        return bool(
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_unspecified
            or address.is_multicast
        )
    except ValueError:
        pass
    try:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(lowered, None, type=socket.SOCK_STREAM)
        }
    except OSError as exc:
        raise ProviderError(f"无法解析 Provider 地址：{hostname}") from exc
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_reserved
            or parsed.is_unspecified
            or parsed.is_multicast
        ):
            return True
    return False


def validate_provider_url(value: str, *, public_mode: bool | None = None) -> str:
    """Validate an outbound Provider URL and reject unsafe public endpoints."""

    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ProviderError("Provider Base URL 必须使用 http 或 https")
    if parsed.username or parsed.password:
        raise ProviderError("Provider Base URL 不得包含用户名或密码")
    if not parsed.hostname:
        raise ProviderError("Provider Base URL 缺少主机名")
    if parsed.query or parsed.fragment:
        raise ProviderError("Provider Base URL 不得包含 query 或 fragment")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ProviderError("Provider Base URL 端口无效") from exc
    if public_mode is None:
        public_mode = _public_mode()
    hostname = parsed.hostname.lower().rstrip(".")
    if public_mode:
        if parsed.scheme != "https":
            raise ProviderError("公网 Provider 必须使用 HTTPS")
        if hostname not in _allowed_hosts():
            raise ProviderError("该 Provider 主机未加入公网允许列表")
        if _hostname_is_private(hostname):
            raise ProviderError("Provider 地址解析到了私网或本机地址")
    # Local mode deliberately permits localhost and RFC1918 model servers.
    return raw.rstrip("/")


def _pinned_request_target(
    url: str,
    headers: Mapping[str, str],
) -> tuple[str, dict[str, str], dict[str, Any] | None]:
    """Pin public requests to a validated DNS answer to stop rebinding.

    TLS still validates the configured hostname through httpcore's
    ``sni_hostname`` request extension, while the TCP connection uses the
    already-checked literal address and therefore performs no second DNS
    lookup after the security decision.
    """

    request_headers = dict(headers)
    if not _public_mode():
        return url, request_headers, None
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ProviderError("Provider 请求缺少主机名")
    try:
        resolved = {
            result[4][0]
            for result in socket.getaddrinfo(hostname, parsed.port, type=socket.SOCK_STREAM)
        }
    except OSError as exc:
        raise ProviderError(f"无法解析 Provider 地址：{hostname}") from exc
    public_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for value in resolved:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ProviderError("Provider DNS 返回了无效地址") from exc
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_unspecified
            or address.is_multicast
        ):
            raise ProviderError("Provider 地址解析到了私网或本机地址")
        public_addresses.append(address)
    if not public_addresses:
        raise ProviderError("Provider DNS 没有返回可用地址")
    address = sorted(public_addresses, key=lambda item: (item.version, str(item)))[0]
    literal = f"[{address}]" if address.version == 6 else str(address)
    if parsed.port is not None:
        literal = f"{literal}:{parsed.port}"
    pinned_url = urlunparse(parsed._replace(netloc=literal))
    request_headers.setdefault("Host", parsed.netloc)
    return pinned_url, request_headers, {"sni_hostname": hostname}


def _request_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _response_request_id(response: httpx.Response) -> str | None:
    return response.headers.get("x-request-id") or response.headers.get("request-id")


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, Mapping):
            error = body.get("error")
            if isinstance(error, Mapping):
                return str(error.get("message") or error.get("type") or body)
            return str(body.get("message") or body)
    except (ValueError, TypeError):
        pass
    text = response.text.strip()
    return text[:500] if text else "模型服务未提供错误详情"


def _secret_values(headers: Mapping[str, str]) -> tuple[str, ...]:
    values: list[str] = []
    for name, value in headers.items():
        if name.lower() not in {"authorization", "x-api-key"} or not value:
            continue
        values.append(value)
        if value.lower().startswith("bearer "):
            values.append(value[7:])
    return tuple(sorted(set(values), key=len, reverse=True))


def _redact(value: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def _raise_http_error(
    response: httpx.Response,
    *,
    connection: bool = False,
    secrets: tuple[str, ...] = (),
) -> None:
    status = response.status_code
    # Authentication failures are retryable only after the user repairs their
    # private credential; the generation state machine therefore parks them in
    # ``needs_retry`` instead of discarding the frozen task as a hard failure.
    retryable = status in {401, 403, 408, 409, 429} or status >= 500
    uncertain = status >= 500
    label = "连接测试失败" if connection else "模型服务返回错误"
    raise ProviderError(
        _redact(f"{label} HTTP {status}：{_error_detail(response)}", secrets),
        status_code=status,
        retryable=retryable,
        uncertain=uncertain,
    )


def _extract_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ProviderError("模型返回不是 JSON 对象")
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            chunks = []
            for part in content:
                if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
                elif isinstance(part, str):
                    chunks.append(part)
            if chunks:
                return "".join(chunks)
        if isinstance(content, str):
            return content
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
    raise ProviderError("模型服务返回空响应")


def _extract_anthropic_content(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        raise ProviderError("Anthropic 返回不是 JSON 对象")
    chunks: list[str] = []
    content = payload.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    if not chunks:
        raise ProviderError("Anthropic 模型返回空响应")
    return "".join(chunks)


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
    """Validate the JSON-Schema subset used by workflow artifacts."""

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
    if isinstance(expected, list):
        allowed = [item for item in expected if isinstance(item, str) and item in valid]
        if allowed and not any(valid[item] for item in allowed):
            raise StructuredOutputError(
                f"结构化输出 {path} 应为 {' 或 '.join(allowed)}"
            )
    elif isinstance(expected, str) and expected in valid and not valid[expected]:
        raise StructuredOutputError(f"结构化输出 {path} 应为 {expected}")
    if isinstance(value, dict):
        for field in schema.get("required", []):
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


def _responses_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate OpenAI Chat-style image blocks to Responses input blocks."""

    converted: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            converted.append(dict(message))
            continue
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, Mapping):
                continue
            block_type = str(block.get("type") or "")
            if block_type in {"text", "input_text"}:
                text = block.get("text")
                if isinstance(text, str) and text:
                    blocks.append({"type": "input_text", "text": text})
            elif block_type in {"image_url", "input_image"}:
                image = block.get("image_url")
                if isinstance(image, Mapping):
                    image = image.get("url")
                if isinstance(image, str) and image:
                    blocks.append({"type": "input_image", "image_url": image})
        converted.append({**message, "content": blocks})
    return converted


class _ProviderBase:
    """Shared lifecycle and HTTP error handling for protocol adapters."""

    def __init__(
        self,
        profile: Any,
        *,
        client: httpx.AsyncClient | None = None,
        request_timeout_seconds: float | None = None,
    ):
        self.profile = profile
        self._client = client
        self._owns_client = client is None
        self._request_timeout_seconds = (
            max(1.0, float(request_timeout_seconds))
            if request_timeout_seconds is not None
            else None
        )

    def _request_timeout(self) -> float:
        return self._request_timeout_seconds or _timeout(self.profile)

    async def __aenter__(self) -> _ProviderBase:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._request_timeout(), follow_redirects=False
            )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _client_or_new(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(timeout=self._request_timeout(), follow_redirects=False), True

    async def _close_if_owned(self, client: httpx.AsyncClient, owns: bool) -> None:
        if owns:
            await client.aclose()

    def _url(self, endpoint: str) -> str:
        return urljoin(_base_url(self.profile), endpoint.lstrip("/"))

    async def _request_json(
        self,
        method: str,
        endpoint: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any] | None = None,
        connection: bool = False,
    ) -> tuple[dict[str, Any], httpx.Response]:
        client, owns = self._client_or_new()
        try:
            try:
                target_url, request_headers, extensions = _pinned_request_target(
                    self._url(endpoint), headers
                )
                response = await client.request(
                    method,
                    target_url,
                    headers=request_headers,
                    json=dict(payload) if payload is not None else None,
                    extensions=extensions,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise ProviderError(
                    f"模型请求超时或网络中断：{exc}", retryable=True, uncertain=True
                ) from exc
            if response.status_code >= 400:
                _raise_http_error(
                    response,
                    connection=connection,
                    secrets=_secret_values(headers),
                )
            try:
                decoded = response.json()
            except ValueError as exc:
                raise ProviderError("模型服务返回了错误 JSON", retryable=True, uncertain=True) from exc
            if not isinstance(decoded, dict):
                raise ProviderError("模型服务返回格式错误", retryable=True, uncertain=True)
            return decoded, response
        finally:
            await self._close_if_owned(client, owns)


class OpenAICompatibleProvider(_ProviderBase):
    """Adapter for OpenAI Chat Completions and Responses-compatible gateways."""

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = get_api_key(self.profile)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _payload(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str,
        model: str | None,
        temperature: float,
        response_schema: Mapping[str, Any] | None,
    ) -> tuple[str, dict[str, Any], str]:
        chosen_model = model or model_for(self.profile, role)
        protocol = _protocol(self.profile)
        if protocol == "responses":
            payload: dict[str, Any] = {
                "model": chosen_model,
                "input": _responses_messages(messages),
                "temperature": temperature,
            }
            endpoint = "responses"
        else:
            payload = {
                "model": chosen_model,
                "messages": messages,
                "temperature": temperature,
            }
            endpoint = "chat/completions"
        if response_schema and _json_schema_supported(self.profile):
            if protocol == "responses":
                payload["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "workflow_output",
                        "schema": dict(response_schema),
                        "strict": True,
                    }
                }
            else:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "workflow_output",
                        "schema": dict(response_schema),
                        "strict": True,
                    },
                }
        return chosen_model, payload, endpoint

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str = "writer",
        model: str | None = None,
        temperature: float = 0.7,
        response_schema: Mapping[str, Any] | None = None,
    ) -> ProviderResponse:
        chosen_model, payload, endpoint = self._payload(
            messages,
            role=role,
            model=model,
            temperature=temperature,
            response_schema=response_schema,
        )
        raw, response = await self._request_json(
            "POST", endpoint, headers=self._headers(), payload=payload
        )
        content = _extract_content(raw)
        usage = raw.get("usage", {}) if isinstance(raw.get("usage", {}), dict) else {}
        return ProviderResponse(
            content,
            raw,
            chosen_model,
            usage,
            request_hash=_request_hash(payload),
            request_id=_response_request_id(response),
            stop_reason=(
                raw.get("choices", [{}])[0].get("finish_reason")
                if isinstance(raw.get("choices"), list) and raw["choices"]
                else raw.get("stop_reason")
            ),
        )

    async def structured(
        self,
        messages: list[dict[str, Any]],
        schema: Mapping[str, Any],
        *,
        role: str = "extractor",
        model: str | None = None,
    ) -> tuple[Any, ProviderResponse]:
        fell_back_to_plain = False
        try:
            response = await self.complete(
                messages, role=role, model=model, response_schema=schema, temperature=0
            )
        except ProviderError as exc:
            if not _json_schema_supported(self.profile) or exc.status_code not in {
                400,
                404,
                422,
            }:
                raise
            fell_back_to_plain = True
            # Some compatible gateways expose the protocol but not Structured
            # Outputs.  Retry once as ordinary JSON before the normal parse /
            # repair path; never mask authentication or uncertain failures.
            plain_messages = [
                *messages,
                {
                    "role": "user",
                    "content": f"请仅返回符合以下 JSON Schema 的 JSON，不要 Markdown：{json.dumps(schema, ensure_ascii=False)}",
                },
            ]
            response = await self.complete(
                plain_messages,
                role=role,
                model=model,
                response_schema=None,
                temperature=0,
            )
        try:
            return parse_structured(response.content, schema), response
        except StructuredOutputError as first_error:
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
                    repair,
                    role=role,
                    model=model,
                    response_schema=None if fell_back_to_plain else schema,
                    temperature=0,
                )
                return parse_structured(repaired.content, schema), repaired
            except (ProviderError, StructuredOutputError) as second_error:
                raise StructuredOutputError(
                    f"结构化输出校验失败：{first_error}; 修复请求也失败：{second_error}"
                ) from second_error

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str = "writer",
        model: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        chosen_model = model or model_for(self.profile, role)
        if _protocol(self.profile) != "chat_completions":
            raise ProviderError("当前流式接口只支持 chat_completions 协议")
        payload = {
            "model": chosen_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        client, owns = self._client_or_new()
        headers = self._headers()
        try:
            try:
                target_url, request_headers, extensions = _pinned_request_target(
                    self._url("chat/completions"), headers
                )
                async with client.stream(
                    "POST",
                    target_url,
                    headers=request_headers,
                    json=payload,
                    extensions=extensions,
                ) as response:
                    if response.status_code >= 400:
                        _raise_http_error(response, secrets=_secret_values(headers))
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            item = json.loads(data)
                        except json.JSONDecodeError as exc:
                            raise ProviderError(
                                "流式响应包含错误 JSON", retryable=True, uncertain=True
                            ) from exc
                        choices = item.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            if isinstance(delta, dict) and delta.get("content"):
                                yield str(delta["content"])
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise ProviderError(
                    f"流式模型请求中断：{exc}", retryable=True, uncertain=True
                ) from exc
        finally:
            await self._close_if_owned(client, owns)

    async def test_connection(self) -> dict[str, Any]:
        raw, response = await self._request_json(
            "GET", "models", headers=self._headers(), connection=True
        )
        return {
            "ok": True,
            "status_code": response.status_code,
            "models": raw,
            "request_id": _response_request_id(response),
            "context_length": _context_limit(self.profile),
        }


def _anthropic_content(content: Any) -> str | list[dict[str, Any]]:
    if not isinstance(content, list):
        return str(content or "")
    blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, Mapping):
            continue
        block_type = str(block.get("type") or "")
        if block_type in {"text", "input_text"}:
            value = block.get("text")
            if isinstance(value, str) and value:
                blocks.append({"type": "text", "text": value})
            continue
        if block_type in {"image_url", "input_image"}:
            image = block.get("image_url")
            if isinstance(image, Mapping):
                image = image.get("url")
            if not isinstance(image, str) or not image:
                continue
            if image.startswith("data:") and ";base64," in image:
                header, encoded = image.split(";base64,", 1)
                media_type = header[5:] or "image/jpeg"
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": encoded,
                        },
                    }
                )
            else:
                blocks.append(
                    {
                        "type": "image",
                        "source": {"type": "url", "url": image},
                    }
                )
    return blocks


def _anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Move system messages to top-level and merge adjacent non-system roles."""

    system_parts: list[str] = []
    normalized: list[dict[str, Any]] = []
    for item in messages:
        role = str(item.get("role") or "user").lower()
        content = _anthropic_content(item.get("content"))
        if role == "system":
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue
        role = "assistant" if role == "assistant" else "user"
        if normalized and normalized[-1]["role"] == role:
            previous = normalized[-1]["content"]
            if isinstance(previous, str) and isinstance(content, str):
                normalized[-1]["content"] = previous + "\n\n" + content
            else:
                previous_blocks = previous if isinstance(previous, list) else [{"type": "text", "text": previous}]
                current_blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
                normalized[-1]["content"] = [*previous_blocks, *current_blocks]
        else:
            normalized.append({"role": role, "content": content or ""})
    return ("\n\n".join(system_parts) or None), normalized


class AnthropicMessagesProvider(_ProviderBase):
    """Native Anthropic ``/v1/messages`` adapter."""

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": _anthropic_version(self.profile),
        }
        key = get_api_key(self.profile)
        if key:
            headers["x-api-key"] = key
        workspace = _profile_value(self.profile, "anthropic_workspace_id", None)
        if workspace:
            headers["anthropic-workspace-id"] = str(workspace)
        return headers

    def _payload(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str,
        model: str | None,
        temperature: float,
        response_schema: Mapping[str, Any] | None,
        stream: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        system, anthropic_messages = _anthropic_messages(messages)
        if not anthropic_messages:
            raise ProviderError("Anthropic 请求至少需要一条 user 或 assistant 消息")
        chosen_model = model or model_for(self.profile, role)
        payload: dict[str, Any] = {
            "model": chosen_model,
            "messages": anthropic_messages,
            "max_tokens": _max_output_tokens(self.profile),
        }
        if system:
            payload["system"] = system
        if _temperature_supported(self.profile):
            payload["temperature"] = temperature
        if stream:
            payload["stream"] = True
        if response_schema and _json_schema_supported(self.profile):
            payload["output_config"] = {
                "format": {"type": "json_schema", "schema": dict(response_schema)}
            }
        return chosen_model, payload

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str = "writer",
        model: str | None = None,
        temperature: float = 0.7,
        response_schema: Mapping[str, Any] | None = None,
    ) -> ProviderResponse:
        chosen_model, payload = self._payload(
            messages,
            role=role,
            model=model,
            temperature=temperature,
            response_schema=response_schema,
        )
        raw, response = await self._request_json(
            "POST", "messages", headers=self._headers(), payload=payload
        )
        content = _extract_anthropic_content(raw)
        usage = raw.get("usage", {}) if isinstance(raw.get("usage", {}), dict) else {}
        if isinstance(usage, dict):
            usage = {
                **usage,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
            }
        return ProviderResponse(
            content,
            raw,
            chosen_model,
            usage,
            request_hash=_request_hash(payload),
            request_id=_response_request_id(response) or raw.get("id"),
            stop_reason=str(raw.get("stop_reason")) if raw.get("stop_reason") else None,
        )

    async def structured(
        self,
        messages: list[dict[str, Any]],
        schema: Mapping[str, Any],
        *,
        role: str = "extractor",
        model: str | None = None,
    ) -> tuple[Any, ProviderResponse]:
        fell_back_to_plain = False
        try:
            response = await self.complete(
                messages, role=role, model=model, response_schema=schema, temperature=0
            )
        except ProviderError as exc:
            if not _json_schema_supported(self.profile) or exc.status_code not in {
                400,
                404,
                422,
            }:
                raise
            fell_back_to_plain = True
            plain_messages = [
                *messages,
                {
                    "role": "user",
                    "content": f"请仅返回符合以下 JSON Schema 的 JSON，不要 Markdown：{json.dumps(schema, ensure_ascii=False)}",
                },
            ]
            response = await self.complete(
                plain_messages,
                role=role,
                model=model,
                response_schema=None,
                temperature=0,
            )
        try:
            return parse_structured(response.content, schema), response
        except StructuredOutputError as first_error:
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
                    repair,
                    role=role,
                    model=model,
                    response_schema=None if fell_back_to_plain else schema,
                    temperature=0,
                )
                return parse_structured(repaired.content, schema), repaired
            except (ProviderError, StructuredOutputError) as second_error:
                raise StructuredOutputError(
                    f"结构化输出校验失败：{first_error}; 修复请求也失败：{second_error}"
                ) from second_error

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str = "writer",
        model: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        _, payload = self._payload(
            messages,
            role=role,
            model=model,
            temperature=temperature,
            response_schema=None,
            stream=True,
        )
        client, owns = self._client_or_new()
        headers = self._headers()
        event_name: str | None = None
        try:
            try:
                target_url, request_headers, extensions = _pinned_request_target(
                    self._url("messages"), headers
                )
                async with client.stream(
                    "POST",
                    target_url,
                    headers=request_headers,
                    json=payload,
                    extensions=extensions,
                ) as response:
                    if response.status_code >= 400:
                        _raise_http_error(response, secrets=_secret_values(headers))
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                            continue
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        try:
                            item = json.loads(data)
                        except json.JSONDecodeError as exc:
                            raise ProviderError(
                                "Anthropic 流式响应包含错误 JSON", retryable=True, uncertain=True
                            ) from exc
                        kind = event_name or item.get("type")
                        if kind == "error" or item.get("type") == "error":
                            error = item.get("error", item)
                            status = error.get("status") if isinstance(error, Mapping) else None
                            error_type = str(error.get("type", "")) if isinstance(error, Mapping) else ""
                            try:
                                status_code = int(status) if status is not None else None
                            except (TypeError, ValueError):
                                status_code = None
                            if status_code is None and "overloaded" in error_type.lower():
                                status_code = 529
                            retryable = status_code in {429, 529} or (
                                status_code is not None and status_code >= 500
                            )
                            raise ProviderError(
                                _redact(
                                    str(
                                        error.get("message")
                                        if isinstance(error, Mapping)
                                        else error
                                    ),
                                    _secret_values(headers),
                                ),
                                status_code=status_code,
                                retryable=retryable,
                                uncertain=bool(status_code and status_code >= 500),
                            )
                        if kind == "content_block_delta":
                            delta = item.get("delta", {})
                            if isinstance(delta, Mapping) and delta.get("type") == "text_delta":
                                text = delta.get("text")
                                if isinstance(text, str) and text:
                                    yield text
                        event_name = None
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise ProviderError(
                    f"Anthropic 流式模型请求中断：{exc}", retryable=True, uncertain=True
                ) from exc
        finally:
            await self._close_if_owned(client, owns)

    async def test_connection(self) -> dict[str, Any]:
        raw, response = await self._request_json(
            "GET", "models", headers=self._headers(), connection=True
        )
        models = raw.get("data", raw)
        model_rows: list[dict[str, Any]] = []
        if isinstance(models, list):
            for item in models:
                if isinstance(item, Mapping):
                    model_rows.append(
                        {
                            "id": item.get("id"),
                            "display_name": item.get("display_name"),
                            "created_at": item.get("created_at"),
                            "context_length": item.get("context_length"),
                        }
                    )
        return {
            "ok": True,
            "status_code": response.status_code,
            "models": model_rows,
            "raw": raw,
            "request_id": _response_request_id(response),
        }


def provider_for(
    profile: Any | None,
    *,
    request_timeout_seconds: float | None = None,
) -> OpenAICompatibleProvider | AnthropicMessagesProvider:
    """Construct exactly the configured provider; never silently use a demo."""

    if profile is None:
        raise ProviderRequired("尚未配置模型 Provider")
    protocol = _protocol(profile)
    if protocol == "anthropic_messages":
        return AnthropicMessagesProvider(
            profile,
            request_timeout_seconds=request_timeout_seconds,
        )
    return OpenAICompatibleProvider(
        profile,
        request_timeout_seconds=request_timeout_seconds,
    )


def provider_config_snapshot(profile: Any) -> dict[str, Any]:
    """Return a secret-free, deterministic Provider snapshot for a generation."""

    values = {
        "provider_id": profile_id(profile),
        "owner_id": profile_owner_id(profile),
        "name": _profile_value(profile, "name", ""),
        "base_url": _profile_value(profile, "base_url", ""),
        "protocol": _protocol(profile),
        "config_version": _profile_value(profile, "config_version", None),
        "api_version": _profile_value(profile, "api_version", None),
        "anthropic_workspace_id": _profile_value(profile, "anthropic_workspace_id", None),
        "model_role_mapping": _models(profile),
        "context_length": _context_limit(profile),
        "max_output_tokens": _max_output_tokens(profile),
        "timeout_seconds": _timeout(profile),
        "capabilities": _capabilities(profile),
        "updated_at": str(_profile_value(profile, "updated_at", "")),
    }
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    values["config_hash"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return values


__all__ = [
    "AnthropicMessagesProvider",
    "CredentialJournal",
    "CredentialStore",
    "DEFAULT_ANTHROPIC_BASE_URL",
    "DEFAULT_ANTHROPIC_VERSION",
    "OpenAICompatibleProvider",
    "PROMPT_VERSION",
    "ProviderError",
    "ProviderRequired",
    "ProviderResponse",
    "SUPPORTED_PROTOCOLS",
    "StructuredOutputError",
    "credential_key",
    "credential_store",
    "delete_api_key",
    "delete_user_credentials",
    "get_api_key",
    "migrate_legacy_credentials",
    "model_for",
    "parse_structured",
    "profile_id",
    "provider_config_snapshot",
    "provider_for",
    "set_api_key",
    "validate_provider_url",
]
