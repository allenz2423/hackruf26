"""Server-side OpenAI Chat Completions client for configurable compatible routes."""

from __future__ import annotations

import base64
import json
import re
import threading
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import httpx

MAX_PROVIDER_RESPONSE_BYTES = 64 * 1024 * 1024
_SAFE_STATUS = re.compile(r"^[A-Z0-9_]{1,48}$")


class OpenAICompatibleError(RuntimeError):
    """Provider failure with only bounded, non-content diagnostic metadata."""

    def __init__(self, code: int, status: str) -> None:
        super().__init__("The configured AI provider request failed.")
        self.code = code
        self.status = status if _SAFE_STATUS.fullmatch(status) else "PROVIDER_ERROR"


class ProviderNotConfiguredError(OpenAICompatibleError):
    def __init__(self) -> None:
        super().__init__(503, "PROVIDER_NOT_CONFIGURED")


@dataclass(frozen=True, repr=False)
class ProviderConfig:
    base_url: str
    model: str
    api_key: str | None


class ProviderConfigManager:
    """Thread-safe process snapshot so admin changes apply without a restart."""

    def __init__(
        self,
        config: ProviderConfig | None = None,
        *,
        source: str = "unconfigured",
    ) -> None:
        self._lock = threading.RLock()
        self._config = config
        self._source = source

    def get(self) -> ProviderConfig | None:
        with self._lock:
            return self._config

    def set(
        self,
        config: ProviderConfig | None,
        *,
        source: str | None = None,
    ) -> None:
        with self._lock:
            self._config = config
            self._source = source or ("admin" if config is not None else "unconfigured")

    def source(self) -> str:
        with self._lock:
            return self._source


def validate_base_url(value: str, *, production: bool) -> str:
    """Validate a provider base URL without accepting embedded credentials or params."""
    normalized = value.strip().rstrip("/")
    if any(character.isspace() for character in normalized):
        raise ValueError("Enter a valid OpenAI-compatible API base URL without whitespace.")
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Enter a valid OpenAI-compatible API base URL.") from exc
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        raise ValueError(
            "Enter a valid OpenAI-compatible API base URL without credentials or query parameters."
        )
    if production and parsed.scheme != "https":
        raise ValueError("Provider URLs must use HTTPS in production.")
    return normalized


def _safe_provider_status(value: object) -> str:
    if not isinstance(value, str):
        return "PROVIDER_ERROR"
    status = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").upper()[:48]
    return status if _SAFE_STATUS.fullmatch(status) else "PROVIDER_ERROR"


def _is_token_limit_finish_reason(value: str | None) -> bool:
    return isinstance(value, str) and value.casefold() in {
        "length",
        "max_tokens",
        "max_output_tokens",
    }


def _declared_response_too_large(response: httpx.Response) -> bool:
    value = response.headers.get("content-length", "")
    if not value.isdigit():
        return False
    normalized = value.lstrip("0") or "0"
    maximum = str(MAX_PROVIDER_RESPONSE_BYTES)
    return len(normalized) > len(maximum) or (
        len(normalized) == len(maximum) and normalized > maximum
    )


def _messages_from_contents(contents: object) -> list[dict[str, Any]]:
    """Translate the existing provider-neutral text/inline-image inputs to chat messages."""
    if isinstance(contents, str):
        return [{"role": "user", "content": contents}]

    values = list(contents) if isinstance(contents, (tuple, list)) else [contents]
    if values and all(hasattr(item, "parts") for item in values):
        messages: list[dict[str, Any]] = []
        for item in values:
            role = getattr(item, "role", "user")
            role = "assistant" if role in {"model", "assistant"} else "user"
            messages.append({"role": role, "content": _parts_to_chat_content(item.parts or [])})
        return messages

    return [{"role": "user", "content": _parts_to_chat_content(values)}]


def _parts_to_chat_content(parts: object) -> str | list[dict[str, Any]]:
    values = list(parts) if isinstance(parts, (tuple, list)) else [parts]
    content: list[dict[str, Any]] = []
    plain: list[str] = []
    for part in values:
        if isinstance(part, str):
            plain.append(part)
            continue
        text = getattr(part, "text", None)
        if isinstance(text, str):
            plain.append(text)
            continue
        inline = getattr(part, "inline_data", None)
        mime_type = getattr(inline, "mime_type", None) if inline is not None else None
        data = getattr(inline, "data", None) if inline is not None else None
        if (
            isinstance(mime_type, str)
            and mime_type.startswith("image/")
            and isinstance(data, bytes)
        ):
            if plain:
                content.append({"type": "text", "text": "\n".join(plain)})
                plain.clear()
            encoded = base64.b64encode(data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{encoded}", "detail": "auto"},
                }
            )
            continue
        raise OpenAICompatibleError(400, "UNSUPPORTED_INPUT_MODALITY")

    if plain:
        content.append({"type": "text", "text": "\n".join(plain)})
    if len(content) == 1 and content[0]["type"] == "text":
        return content[0]["text"]
    return content


def _read_json_limited(response: httpx.Response) -> dict[str, Any]:
    raw = bytearray()
    if _declared_response_too_large(response):
        raise OpenAICompatibleError(502, "RESPONSE_TOO_LARGE")
    for chunk in response.iter_bytes():
        raw.extend(chunk)
        if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
            raise OpenAICompatibleError(502, "RESPONSE_TOO_LARGE")
    if response.status_code >= 400:
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise OpenAICompatibleError(502, "INVALID_PROVIDER_RESPONSE") from None
    if not isinstance(value, dict):
        raise OpenAICompatibleError(502, "INVALID_PROVIDER_RESPONSE")
    return value


def _fallback_request_body(
    body: dict[str, Any], error_payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Retry only when the server explicitly identifies a compatibility parameter."""
    error = error_payload.get("error")
    parameter = error.get("param") if isinstance(error, dict) else None
    if parameter == "max_completion_tokens" and parameter in body:
        fallback = dict(body)
        fallback["max_tokens"] = fallback.pop("max_completion_tokens")
        return fallback
    if parameter == "temperature" and parameter in body:
        fallback = dict(body)
        fallback.pop("temperature")
        return fallback
    return None


def _extract_text(payload: dict[str, Any]) -> tuple[str, str | None]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise OpenAICompatibleError(502, "INVALID_PROVIDER_RESPONSE")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise OpenAICompatibleError(502, "INVALID_PROVIDER_RESPONSE")
    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str):
        finish_reason = None
    content = message.get("content")
    if isinstance(content, str):
        return content, finish_reason
    if isinstance(content, list):
        text_parts = [item.get("text") for item in content if isinstance(item, dict)]
        if text_parts and all(isinstance(item, str) for item in text_parts):
            return "".join(text_parts), finish_reason
    # Reasoning models can spend the entire completion budget on internal
    # reasoning and return no visible content with finish_reason="length".
    # Preserve that signal so the content service can split/retry the request
    # instead of misclassifying it as an ordinary empty provider response.
    if _is_token_limit_finish_reason(finish_reason) and content in (None, "", []):
        return "", finish_reason
    status = _safe_provider_status(finish_reason) if finish_reason else "NO_FINISH_REASON"
    raise OpenAICompatibleError(502, f"EMPTY_PROVIDER_RESPONSE_{status}")


@dataclass
class _Candidate:
    finish_reason: str | None


@dataclass
class _Response:
    text: str
    candidates: list[_Candidate]


class _Models:
    def __init__(self, client: OpenAICompatibleClient, *, asynchronous: bool) -> None:
        self.client = client
        self.asynchronous = asynchronous

    def generate_content(self, *, model: str, contents: object, config: object) -> _Response:
        if self.asynchronous:
            raise RuntimeError("Use the asynchronous client interface.")
        return self.client.generate_content(model=model, contents=contents, config=config)

    async def generate_content_async(
        self, *, model: str, contents: object, config: object
    ) -> _Response:
        if not self.asynchronous:
            raise RuntimeError("Use the synchronous client interface.")
        return await self.client.generate_content_async(
            model=model, contents=contents, config=config
        )


class _AsyncModels:
    def __init__(self, client: OpenAICompatibleClient) -> None:
        self._client = client

    async def generate_content(self, *, model: str, contents: object, config: object) -> _Response:
        return await self._client.generate_content_async(
            model=model, contents=contents, config=config
        )


class _AsyncNamespace:
    def __init__(self, client: OpenAICompatibleClient) -> None:
        self.models = _AsyncModels(client)


class OpenAICompatibleClient:
    """Small Chat Completions adapter compatible with existing server-side services."""

    def __init__(
        self,
        config: ProviderConfigManager,
        *,
        sync_transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._sync_transport = sync_transport
        self._async_transport = async_transport
        self.models = _Models(self, asynchronous=False)
        self.aio = _AsyncNamespace(self)

    @staticmethod
    def _endpoint(base_url: str) -> str:
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    @staticmethod
    def _request_body(model: str, contents: object, config: object) -> dict[str, Any]:
        messages = _messages_from_contents(contents)
        instruction = getattr(config, "system_instruction", None)
        response_schema = getattr(config, "response_json_schema", None)
        if isinstance(response_schema, dict):
            schema_instruction = (
                "Return a JSON object that conforms to this trusted output schema. "
                "Do not add keys that the schema does not allow.\n"
                + json.dumps(response_schema, ensure_ascii=False, separators=(",", ":"))
            )
            instruction = (
                f"{instruction}\n\n{schema_instruction}"
                if isinstance(instruction, str) and instruction.strip()
                else schema_instruction
            )
        if isinstance(instruction, str) and instruction.strip():
            messages.insert(0, {"role": "system", "content": instruction})
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        temperature = getattr(config, "temperature", None)
        if isinstance(temperature, (int, float)):
            body["temperature"] = temperature
        max_tokens = getattr(config, "max_output_tokens", None)
        if isinstance(max_tokens, int) and max_tokens > 0:
            body["max_completion_tokens"] = max_tokens
        if getattr(config, "response_mime_type", None) == "application/json":
            body["response_format"] = {"type": "json_object"}
        return body

    @staticmethod
    def _provider_request_body(
        provider: ProviderConfig, contents: object, config: object
    ) -> dict[str, Any]:
        body = OpenAICompatibleClient._request_body(provider.model, contents, config)
        hostname = urlsplit(provider.base_url).hostname
        if (
            hostname is not None
            and (hostname == "openrouter.ai" or hostname.endswith(".openrouter.ai"))
            and provider.model == "deepseek/deepseek-v4.1-flash"
        ):
            # Keep this model's reasoning enabled at the requested high level.
            body["reasoning_effort"] = "high"
        return body

    def _current_config(self) -> ProviderConfig:
        config = self.config.get()
        if config is None or not config.api_key or not config.base_url or not config.model:
            raise ProviderNotConfiguredError()
        return config

    def generate_content(self, *, model: str, contents: object, config: object) -> _Response:
        del model  # The administrator-selected model is authoritative.
        provider = self._current_config()
        body = self._provider_request_body(provider, contents, config)
        try:
            with httpx.Client(
                timeout=None,
                follow_redirects=False,
                transport=self._sync_transport,
            ) as client:
                for attempt in range(3):
                    with client.stream(
                        "POST",
                        self._endpoint(provider.base_url),
                        headers={
                            "Authorization": f"Bearer {provider.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=body,
                    ) as response:
                        payload = _read_json_limited(response)
                        if response.status_code < 400:
                            break
                        fallback = (
                            _fallback_request_body(body, payload)
                            if response.status_code == 400 and attempt < 2
                            else None
                        )
                        if fallback is None:
                            raise _provider_error(response.status_code, payload)
                        body = fallback
        except httpx.HTTPError:
            raise OpenAICompatibleError(502, "PROVIDER_CONNECTION_FAILED") from None
        text, finish_reason = _extract_text(payload)
        candidate_reason = (
            "MAX_TOKENS" if _is_token_limit_finish_reason(finish_reason) else finish_reason
        )
        return _Response(text, [_Candidate(candidate_reason)])

    async def generate_content_async(
        self, *, model: str, contents: object, config: object
    ) -> _Response:
        del model
        provider = self._current_config()
        body = self._provider_request_body(provider, contents, config)
        try:
            async with httpx.AsyncClient(
                timeout=None,
                follow_redirects=False,
                transport=self._async_transport,
            ) as client:
                for attempt in range(3):
                    async with client.stream(
                        "POST",
                        self._endpoint(provider.base_url),
                        headers={
                            "Authorization": f"Bearer {provider.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=body,
                    ) as response:
                        payload = await _read_json_limited_async(response)
                        if response.status_code < 400:
                            break
                        fallback = (
                            _fallback_request_body(body, payload)
                            if response.status_code == 400 and attempt < 2
                            else None
                        )
                        if fallback is None:
                            raise _provider_error(response.status_code, payload)
                        body = fallback
        except httpx.HTTPError:
            raise OpenAICompatibleError(502, "PROVIDER_CONNECTION_FAILED") from None
        text, finish_reason = _extract_text(payload)
        candidate_reason = (
            "MAX_TOKENS" if _is_token_limit_finish_reason(finish_reason) else finish_reason
        )
        return _Response(text, [_Candidate(candidate_reason)])

    async def test_connection(self) -> None:
        response = await self.generate_content_async(
            model="",
            contents="Reply with the single word OK.",
            # Some reasoning models spend part of their completion budget on
            # hidden reasoning before returning the visible connection reply.
            config=SimpleNamespace(max_output_tokens=128),
        )
        if not response.text.strip():
            raise OpenAICompatibleError(502, "EMPTY_PROVIDER_RESPONSE")


def _provider_error(status_code: int, payload: dict[str, Any]) -> OpenAICompatibleError:
    error = payload.get("error")
    status = None
    if isinstance(error, dict):
        status = error.get("code") or error.get("type")
    return OpenAICompatibleError(status_code, _safe_provider_status(status))


async def _read_json_limited_async(response: httpx.Response) -> dict[str, Any]:
    raw = bytearray()
    if _declared_response_too_large(response):
        raise OpenAICompatibleError(502, "RESPONSE_TOO_LARGE")
    async for chunk in response.aiter_bytes():
        raw.extend(chunk)
        if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
            raise OpenAICompatibleError(502, "RESPONSE_TOO_LARGE")
    if response.status_code >= 400:
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise OpenAICompatibleError(502, "INVALID_PROVIDER_RESPONSE") from None
    if not isinstance(value, dict):
        raise OpenAICompatibleError(502, "INVALID_PROVIDER_RESPONSE")
    return value
