"""OpenAI-compatible Chat Completions request adapter tests."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from google.genai import types

from app.services.gemini_content import _provider_response_schema, _StudyGuideSectionBatch
from app.services.openai_compatible import (
    MAX_PROVIDER_RESPONSE_BYTES,
    PROVIDER_REQUEST_TIMEOUT_SECONDS,
    OpenAICompatibleClient,
    OpenAICompatibleError,
    ProviderConfig,
    ProviderConfigManager,
    ProviderNotConfiguredError,
    _extract_text,
    _fallback_request_body,
    validate_base_url,
)
from app.services.quiz_reasoning import QuizReasoningService


def configured_client(handler, *, asynchronous: bool = False) -> OpenAICompatibleClient:
    manager = ProviderConfigManager(
        ProviderConfig("https://provider.example/v1", "custom-model", "secret-test-key"),
        source="test",
    )
    transport = httpx.MockTransport(handler)
    return OpenAICompatibleClient(
        manager,
        async_transport=transport if asynchronous else None,
        sync_transport=None if asynchronous else transport,
    )


class OpenAICompatibleClientTests(unittest.TestCase):
    def test_sync_provider_request_has_a_bounded_timeout(self) -> None:
        observed = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed.update(request.extensions["timeout"])
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}]},
            )

        result = configured_client(handler).models.generate_content(
            model="ignored", contents="prompt", config=SimpleNamespace()
        )

        self.assertEqual(result.text, "ready")
        self.assertEqual(
            observed,
            {
                "connect": PROVIDER_REQUEST_TIMEOUT_SECONDS,
                "read": PROVIDER_REQUEST_TIMEOUT_SECONDS,
                "write": PROVIDER_REQUEST_TIMEOUT_SECONDS,
                "pool": PROVIDER_REQUEST_TIMEOUT_SECONDS,
            },
        )

    def test_sync_provider_timeout_is_reported_safely(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("private provider request details")

        with self.assertRaises(OpenAICompatibleError) as raised:
            configured_client(handler).models.generate_content(
                model="ignored", contents="private source text", config=SimpleNamespace()
            )

        self.assertEqual(raised.exception.code, 504)
        self.assertEqual(raised.exception.status, "PROVIDER_TIMEOUT")
        self.assertNotIn("private", str(raised.exception))

    def test_openrouter_haiku_uses_native_json_schema_outputs(self) -> None:
        provider = ProviderConfig(
            "https://openrouter.ai/api/v1",
            "anthropic/claude-haiku-5.5",
            "secret",
        )
        schema = {
            "type": "object",
            "properties": {
                "label": {"type": "string", "nullable": True},
                "shade": {"enum": ["dark", "light"], "nullable": True},
                "kind": {"enum": ["paragraph", "formula"]},
            },
            "required": ["kind"],
            "additionalProperties": False,
        }
        config = SimpleNamespace(
            response_mime_type="application/json",
            response_json_schema=schema,
            system_instruction="Return a structured result.",
            max_output_tokens=512,
            temperature=0.2,
        )

        body = OpenAICompatibleClient._provider_request_body(provider, "prompt", config)

        self.assertEqual(
            body["response_format"],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "screen_tutor_output",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "label": {"type": ["string", "null"]},
                            "shade": {
                                "type": ["string", "null"],
                                "enum": ["dark", "light", None],
                            },
                            "kind": {"enum": ["paragraph", "formula"]},
                        },
                        "required": ["kind", "label", "shade"],
                        "additionalProperties": False,
                    },
                },
            },
        )
        self.assertEqual(body["provider"], {"only": ["azure"], "require_parameters": True})
        self.assertEqual(body["reasoning"], {"enabled": False})
        self.assertNotIn("temperature", body)
        self.assertEqual(body["messages"][0]["content"], "Return a structured result.")

    def test_openrouter_study_guide_schema_uses_provider_safe_nullable_types(self) -> None:
        provider = ProviderConfig(
            "https://openrouter.ai/api/v1",
            "anthropic/claude-haiku-5.5",
            "secret",
        )
        body = OpenAICompatibleClient._provider_request_body(
            provider,
            "Create a guide section.",
            SimpleNamespace(
                response_mime_type="application/json",
                response_json_schema=_provider_response_schema(_StudyGuideSectionBatch),
                system_instruction="Return valid JSON.",
            ),
        )
        schema = body["response_format"]["json_schema"]["schema"]
        pending = [schema]
        nullable_count = 0
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                self.assertNotIn("anyOf", node)
                value_type = node.get("type")
                if isinstance(value_type, list) and "null" in value_type:
                    nullable_count += 1
                pending.extend(node.values())
            elif isinstance(node, list):
                pending.extend(node)

        self.assertGreater(nullable_count, 0)
        self.assertEqual(body["response_format"]["type"], "json_schema")

    def test_reasoning_blocks_are_not_added_to_visible_json_text(self) -> None:
        text, finish_reason = _extract_text(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "reasoning", "text": "Let me think through this."},
                                {"type": "text", "text": '{"status":"ok"}'},
                            ]
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        )

        self.assertEqual(text, '{"status":"ok"}')
        self.assertEqual(finish_reason, "stop")

    def test_unsupported_native_schema_falls_back_to_prompt_schema_json_mode(self) -> None:
        provider = ProviderConfig(
            "https://openrouter.ai/api/v1",
            "anthropic/claude-haiku-5.5",
            "secret",
        )
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
        body = OpenAICompatibleClient._provider_request_body(
            provider,
            "prompt",
            SimpleNamespace(
                response_mime_type="application/json",
                response_json_schema=schema,
                system_instruction="Return a result.",
            ),
        )

        fallback = _fallback_request_body(body, {"error": {"code": 400}})

        self.assertIsNotNone(fallback)
        self.assertEqual(fallback["response_format"], {"type": "json_object"})
        self.assertEqual(fallback["provider"], {"only": ["azure"]})
        self.assertIn("trusted output schema", fallback["messages"][0]["content"])
        self.assertIn('"required":["status"]', fallback["messages"][0]["content"])

    def test_openrouter_no_schema_endpoint_falls_back_after_404(self) -> None:
        bodies = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            if body["response_format"]["type"] == "json_schema":
                return httpx.Response(
                    404,
                    json={
                        "error": {
                            "code": 404,
                            "message": "No provider endpoint supports the requested parameter.",
                            "metadata": {},
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '{"status":"ok"}'}, "finish_reason": "stop"}
                    ]
                },
            )

        manager = ProviderConfigManager(
            ProviderConfig(
                "https://openrouter.ai/api/v1",
                "anthropic/claude-haiku-5.5",
                "secret-test-key",
            )
        )
        client = OpenAICompatibleClient(manager, sync_transport=httpx.MockTransport(handler))
        result = client.models.generate_content(
            model="",
            contents="Return the status.",
            config=SimpleNamespace(
                response_mime_type="application/json",
                response_json_schema={
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                    "required": ["status"],
                    "additionalProperties": False,
                },
                system_instruction="Return a status.",
            ),
        )

        self.assertEqual(result.text, '{"status":"ok"}')
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0]["response_format"]["type"], "json_schema")
        self.assertEqual(bodies[1]["response_format"], {"type": "json_object"})
        self.assertEqual(bodies[1]["provider"], {"only": ["azure"]})

    def test_sync_request_uses_configured_route_key_model_and_json_mode(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["authorization"] = request.headers["authorization"]
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '{"status":"sufficient"}'}, "finish_reason": "stop"}
                    ]
                },
            )

        client = configured_client(handler)
        result = client.models.generate_content(
            model="ignored-model",
            contents="A meaningful attempt.",
            config=SimpleNamespace(
                system_instruction="Evaluate engagement.",
                response_mime_type="application/json",
                response_json_schema={"type": "object", "required": ["status"]},
                temperature=0.1,
                max_output_tokens=75,
            ),
        )

        self.assertEqual(result.text, '{"status":"sufficient"}')
        self.assertEqual(captured["url"], "https://provider.example/v1/chat/completions")
        self.assertEqual(captured["authorization"], "Bearer secret-test-key")
        self.assertEqual(captured["body"]["model"], "custom-model")
        self.assertEqual(captured["body"]["max_completion_tokens"], 75)
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})
        self.assertIn('"required":["status"]', captured["body"]["messages"][0]["content"])

    def test_retries_legacy_token_parameter_only_after_explicit_rejection(self) -> None:
        bodies = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            if len(bodies) == 1:
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "code": "unsupported_parameter",
                            "param": "max_completion_tokens",
                            "message": "Use max_tokens instead.",
                        }
                    },
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
            )

        client = configured_client(handler)
        result = client.models.generate_content(
            model="ignored",
            contents="text",
            config=SimpleNamespace(max_output_tokens=32),
        )
        self.assertEqual(result.text, "ok")
        self.assertEqual(bodies[0]["max_completion_tokens"], 32)
        self.assertNotIn("max_tokens", bodies[0])
        self.assertEqual(bodies[1]["max_tokens"], 32)
        self.assertNotIn("max_completion_tokens", bodies[1])

    def test_chat_completions_explicit_url_is_not_double_appended(self) -> None:
        manager = ProviderConfigManager(
            ProviderConfig("https://provider.example/v1/chat/completions", "m", "k")
        )
        self.assertEqual(
            OpenAICompatibleClient._endpoint(manager.get().base_url),
            "https://provider.example/v1/chat/completions",
        )

    def test_high_reasoning_effort_is_scoped_to_the_selected_openrouter_model(self) -> None:
        config = SimpleNamespace(max_output_tokens=96_000)
        selected_model = ProviderConfig(
            "https://openrouter.ai/api/v1", "deepseek/deepseek-v4.1-flash", "secret"
        )
        other_model = ProviderConfig("https://openrouter.ai/api/v1", "openai/gpt-test", "secret")
        other_provider = ProviderConfig(
            "https://provider.example/v1", "deepseek/deepseek-v4.1-flash", "secret"
        )

        selected = OpenAICompatibleClient._provider_request_body(selected_model, "prompt", config)
        self.assertEqual(selected["reasoning_effort"], "high")
        self.assertEqual(selected["max_completion_tokens"], 96_000)
        self.assertNotIn(
            "reasoning_effort",
            OpenAICompatibleClient._provider_request_body(other_model, "prompt", config),
        )
        self.assertNotIn(
            "reasoning_effort",
            OpenAICompatibleClient._provider_request_body(other_provider, "prompt", config),
        )

    def test_study_guide_field_edits_disable_reasoning_tokens(self) -> None:
        provider = ProviderConfig(
            "https://openrouter.ai/api/v1", "deepseek/deepseek-v4.1-flash", "secret"
        )
        config = SimpleNamespace(
            max_output_tokens=8_000,
            system_instruction=(
                "You are applying small, source-grounded corrections to an existing study guide."
            ),
        )

        body = OpenAICompatibleClient._provider_request_body(provider, "one field", config)

        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["max_completion_tokens"], 8_000)

    def test_study_guide_reviews_use_minimal_reasoning_effort(self) -> None:
        provider = ProviderConfig(
            "https://openrouter.ai/api/v1", "deepseek/deepseek-v4.1-flash", "secret"
        )
        config = SimpleNamespace(
            max_output_tokens=24_000,
            system_instruction=(
                "You are an independent course-material verifier. Review selected sections."
            ),
        )

        body = OpenAICompatibleClient._provider_request_body(provider, "bounded review", config)

        self.assertEqual(body["reasoning_effort"], "minimal")
        self.assertEqual(body["max_completion_tokens"], 24_000)

    def test_study_guide_finding_adjudication_disables_reasoning_tokens(self) -> None:
        provider = ProviderConfig(
            "https://openrouter.ai/api/v1", "deepseek/deepseek-v4.1-flash", "secret"
        )
        config = SimpleNamespace(
            max_output_tokens=3_000,
            system_instruction=(
                "You are adjudicating reported unsupported-claim findings. Return quoted evidence."
            ),
        )

        body = OpenAICompatibleClient._provider_request_body(provider, "quoted evidence", config)

        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["max_completion_tokens"], 3_000)

    def test_study_guide_authorship_uses_minimal_reasoning_effort(self) -> None:
        provider = ProviderConfig(
            "https://openrouter.ai/api/v1", "deepseek/deepseek-v4.1-flash", "secret"
        )
        config = SimpleNamespace(
            max_output_tokens=12_000,
            system_instruction=(
                "You are Screen Tutor's course study-guide author. Create a useful guide."
            ),
        )

        body = OpenAICompatibleClient._provider_request_body(provider, "four sections", config)

        self.assertEqual(body["reasoning_effort"], "minimal")
        self.assertEqual(body["max_completion_tokens"], 12_000)

    def test_provider_error_preserves_status_without_exposing_body(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                402,
                json={
                    "error": {
                        "code": "insufficient_quota",
                        "type": "insufficient_quota",
                        "message": "private provider response and prompt content",
                    }
                },
            )

        client = configured_client(handler)
        with self.assertRaises(OpenAICompatibleError) as raised:
            client.models.generate_content(
                model="ignored", contents="private student text", config=SimpleNamespace()
            )
        self.assertEqual(raised.exception.code, 402)
        self.assertEqual(raised.exception.status, "INSUFFICIENT_QUOTA")
        self.assertNotIn("private", str(raised.exception))

    def test_sync_request_retries_rate_limit_using_retry_after(self) -> None:
        request_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                return httpx.Response(
                    429,
                    headers={"retry-after": "60"},
                    json={"error": {"code": 429, "type": "rate_limit_exceeded"}},
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}]},
            )

        client = configured_client(handler)
        with patch("app.services.openai_compatible.time.sleep") as sleep:
            result = client.models.generate_content(
                model="ignored", contents="prompt", config=SimpleNamespace()
            )

        self.assertEqual(result.text, "ready")
        self.assertEqual(request_count, 2)
        sleep.assert_called_once_with(60.0)

    def test_sync_request_retries_gateway_error_with_backoff(self) -> None:
        request_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                return httpx.Response(502, json={"error": {"type": "bad_gateway"}})
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}]},
            )

        client = configured_client(handler)
        with patch("app.services.openai_compatible.time.sleep") as sleep:
            result = client.models.generate_content(
                model="ignored", contents="prompt", config=SimpleNamespace()
            )

        self.assertEqual(result.text, "ready")
        self.assertEqual(request_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_empty_reasoning_response_preserves_token_limit_signal(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": None, "reasoning": "hidden"},
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {
                        "completion_tokens": 100,
                        "completion_tokens_details": {"reasoning_tokens": 100},
                    },
                },
            )

        response = configured_client(handler).models.generate_content(
            model="ignored", contents="source", config=SimpleNamespace()
        )

        self.assertEqual(response.text, "")
        self.assertEqual(response.candidates[0].finish_reason, "MAX_TOKENS")

    def test_empty_non_truncated_response_fails_with_safe_finish_diagnostic(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": None}, "finish_reason": "stop"}]},
            )

        with self.assertRaises(OpenAICompatibleError) as raised:
            configured_client(handler).models.generate_content(
                model="ignored", contents="private source", config=SimpleNamespace()
            )

        self.assertEqual(raised.exception.status, "EMPTY_PROVIDER_RESPONSE_STOP")
        self.assertNotIn("private", str(raised.exception))

    def test_oversized_response_is_rejected_from_header_before_reading_body(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-length": str(MAX_PROVIDER_RESPONSE_BYTES + 1)},
                content=b"",
            )

        client = configured_client(handler)
        with self.assertRaises(OpenAICompatibleError) as raised:
            client.models.generate_content(
                model="ignored", contents="text", config=SimpleNamespace()
            )
        self.assertEqual(raised.exception.status, "RESPONSE_TOO_LARGE")

    def test_extremely_long_content_length_is_rejected_without_integer_parsing(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-length": "9" * 5_000},
                content=b"",
            )

        client = configured_client(handler)
        with self.assertRaises(OpenAICompatibleError) as raised:
            client.models.generate_content(
                model="ignored", contents="text", config=SimpleNamespace()
            )
        self.assertEqual(raised.exception.status, "RESPONSE_TOO_LARGE")

    def test_missing_configuration_fails_closed(self) -> None:
        client = OpenAICompatibleClient(ProviderConfigManager())
        with self.assertRaises(ProviderNotConfiguredError):
            client.models.generate_content(model="m", contents="test", config=SimpleNamespace())

    def test_base_url_validation_blocks_credentials_and_production_http(self) -> None:
        with self.assertRaises(ValueError):
            validate_base_url("https://user:pass@example.com/v1", production=False)
        with self.assertRaises(ValueError):
            validate_base_url("https://provider.example/v1\n/chat", production=False)
        with self.assertRaises(ValueError):
            validate_base_url("http://provider.example/v1", production=True)
        self.assertEqual(
            validate_base_url("https://provider.example/v1/", production=True),
            "https://provider.example/v1",
        )

    def test_converts_existing_multimodal_parts_to_chat_content(self) -> None:
        payload = OpenAICompatibleClient._request_body(
            "selected-model",
            [
                types.Content(
                    role="user",
                    parts=[
                        types.Part(text="Read this diagram."),
                        types.Part.from_bytes(data=b"png-bytes", mime_type="image/png"),
                    ],
                )
            ],
            SimpleNamespace(),
        )
        content = payload["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "Read this diagram."})
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_existing_reasoning_service_runs_through_the_compatible_route(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"status":"sufficient","reason":"Relevant attempt",'
                                    '"follow_up":null}'
                                )
                            },
                            "finish_reason": "stop",
                        }
                    ]
                },
            )

        client = configured_client(handler)
        decision = QuizReasoningService(client, "ignored-model").evaluate(
            {"question": {"id": "q1", "text": "Explain the distinction."}, "reasoning_turns": []}
        )
        self.assertEqual(decision.status, "sufficient")
        self.assertEqual(captured["path"], "/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "custom-model")


class AsyncOpenAICompatibleClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_provider_request_has_a_bounded_timeout(self) -> None:
        observed = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed.update(request.extensions["timeout"])
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}]},
            )

        result = await configured_client(handler, asynchronous=True).aio.models.generate_content(
            model="ignored", contents="prompt", config=SimpleNamespace()
        )

        self.assertEqual(result.text, "ready")
        self.assertEqual(
            observed,
            {
                "connect": PROVIDER_REQUEST_TIMEOUT_SECONDS,
                "read": PROVIDER_REQUEST_TIMEOUT_SECONDS,
                "write": PROVIDER_REQUEST_TIMEOUT_SECONDS,
                "pool": PROVIDER_REQUEST_TIMEOUT_SECONDS,
            },
        )

    async def test_async_provider_timeout_is_reported_safely(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("private provider request details")

        with self.assertRaises(OpenAICompatibleError) as raised:
            await configured_client(handler, asynchronous=True).aio.models.generate_content(
                model="ignored", contents="private source text", config=SimpleNamespace()
            )

        self.assertEqual(raised.exception.code, 504)
        self.assertEqual(raised.exception.status, "PROVIDER_TIMEOUT")
        self.assertNotIn("private", str(raised.exception))

    async def test_connection_check_reserves_reasoning_headroom_without_temperature(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]},
            )

        client = configured_client(handler, asynchronous=True)
        await client.test_connection()
        self.assertEqual(captured["body"]["max_completion_tokens"], 128)
        self.assertNotIn("temperature", captured["body"])

    async def test_async_namespace_uses_configured_chat_route(self) -> None:
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "Ready."}, "finish_reason": "stop"}]},
            )

        client = configured_client(handler, asynchronous=True)
        result = await client.aio.models.generate_content(
            model="ignored",
            contents=[
                types.Content(role="user", parts=[types.Part(text="Hello")]),
                types.Content(role="model", parts=[types.Part(text="Hi")]),
                types.Content(role="user", parts=[types.Part(text="Continue")]),
            ],
            config=SimpleNamespace(system_instruction="Be concise."),
        )

        self.assertEqual(result.text, "Ready.")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.path, "/v1/chat/completions")
        self.assertEqual(requests[0].headers["authorization"], "Bearer secret-test-key")
        messages = json.loads(requests[0].content)["messages"]
        self.assertEqual(
            [item["role"] for item in messages], ["system", "user", "assistant", "user"]
        )

    async def test_async_request_retries_rate_limit(self) -> None:
        request_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                return httpx.Response(
                    429,
                    headers={"retry-after": "0"},
                    json={"error": {"code": 429, "type": "rate_limit_exceeded"}},
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}]},
            )

        client = configured_client(handler, asynchronous=True)
        result = await client.aio.models.generate_content(
            model="ignored", contents="prompt", config=SimpleNamespace()
        )

        self.assertEqual(result.text, "ready")
        self.assertEqual(request_count, 2)

    async def test_async_request_retries_gateway_error_with_backoff(self) -> None:
        request_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                return httpx.Response(502, json={"error": {"type": "bad_gateway"}})
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}]},
            )

        client = configured_client(handler, asynchronous=True)
        with patch("app.services.openai_compatible.asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await client.aio.models.generate_content(
                model="ignored", contents="prompt", config=SimpleNamespace()
            )

        self.assertEqual(result.text, "ready")
        self.assertEqual(request_count, 2)
        sleep.assert_awaited_once_with(1.0)
