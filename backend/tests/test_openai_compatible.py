"""OpenAI-compatible Chat Completions request adapter tests."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

import httpx
from google.genai import types

from app.services.openai_compatible import (
    MAX_PROVIDER_RESPONSE_BYTES,
    OpenAICompatibleClient,
    OpenAICompatibleError,
    ProviderConfig,
    ProviderConfigManager,
    ProviderNotConfiguredError,
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
        other_model = ProviderConfig(
            "https://openrouter.ai/api/v1", "openai/gpt-test", "secret"
        )
        other_provider = ProviderConfig(
            "https://provider.example/v1", "deepseek/deepseek-v4.1-flash", "secret"
        )

        selected = OpenAICompatibleClient._provider_request_body(
            selected_model, "prompt", config
        )
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

    def test_provider_error_preserves_status_without_exposing_body(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                402,
                json={
                    "error": {
                        "code": "insufficient_quota",
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
