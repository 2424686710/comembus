"""Tests for the remote OpenAI-compatible LLM client."""

from __future__ import annotations

import http.server
import json
import os
import socketserver
import tempfile
import threading
import unittest

from comembus.llm.adapter import LLMMessage, build_llm_client
from comembus.llm.openai_compatible_client import (
    OpenAICompatibleChatClient,
    normalize_chat_endpoint,
)
from examples.incident_diagnosis_mock.run_llm_multiagent_smoke import (
    judge_root_cause_semantic,
    parse_llm_agents,
    run_llm_multiagent_smoke,
)


class _ThreadedTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


class OpenAICompatibleClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="comembus-openai-test-")
        self.db_path = os.path.join(self.tempdir.name, "multiagent.sqlite")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_missing_endpoint_or_key_falls_back_to_mock(self) -> None:
        client = OpenAICompatibleChatClient(endpoint=None, model="remote-model", api_key=None)
        response = client.generate([LLMMessage(role="user", content="database timeout")])

        self.assertEqual(response.provider, "mock")
        self.assertTrue(response.used_fallback)
        self.assertEqual(response.model, "remote-model")

    def test_normalize_chat_endpoint_variants(self) -> None:
        self.assertEqual(
            normalize_chat_endpoint("https://api.deepseek.com"),
            "https://api.deepseek.com/chat/completions",
        )
        self.assertEqual(
            normalize_chat_endpoint("https://api.deepseek.com/chat/completions"),
            "https://api.deepseek.com/chat/completions",
        )
        self.assertEqual(
            normalize_chat_endpoint("https://xxx/v1"),
            "https://xxx/v1/chat/completions",
        )
        self.assertEqual(
            normalize_chat_endpoint("https://xxx/compatible-mode/v1/chat/completions"),
            "https://xxx/compatible-mode/v1/chat/completions",
        )

    def test_fake_server_response_parses_content_and_usage_tokens(self) -> None:
        captured: dict[str, object] = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # type: ignore[override]
                length = int(self.headers.get("Content-Length", "0"))
                captured["authorization"] = self.headers.get("Authorization")
                captured["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
                payload = {
                    "choices": [{"message": {"content": "root_cause: remote ok\nreport: remote report"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
                }
                encoded = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args) -> None:  # type: ignore[override]
                del format, args

        with _ThreadedTCPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
            client = OpenAICompatibleChatClient(
                endpoint=endpoint,
                model="qwen-test",
                api_key="secret-key",
            )
            response = client.generate([LLMMessage(role="user", content="wrong database port")])
            server.shutdown()
            thread.join(timeout=2.0)

        self.assertEqual(response.provider, "openai_compatible")
        self.assertFalse(response.used_fallback)
        self.assertEqual(response.model, "qwen-test")
        self.assertEqual(response.total_tokens, 19)
        self.assertEqual(response.prompt_tokens, 12)
        self.assertEqual(response.completion_tokens, 7)
        self.assertEqual(captured["authorization"], "Bearer secret-key")
        self.assertEqual(captured["body"]["model"], "qwen-test")

    def test_invalid_json_or_missing_choices_falls_back(self) -> None:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # type: ignore[override]
                encoded = b'{"unexpected": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args) -> None:  # type: ignore[override]
                del format, args

        with _ThreadedTCPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
            client = OpenAICompatibleChatClient(
                endpoint=endpoint,
                model="deepseek-test",
                api_key="secret-key",
            )
            response = client.generate([LLMMessage(role="user", content="permission denied")])
            server.shutdown()
            thread.join(timeout=2.0)

        self.assertEqual(response.provider, "mock")
        self.assertTrue(response.used_fallback)

    def test_build_llm_client_openai_compatible(self) -> None:
        client = build_llm_client(
            "openai_compatible",
            endpoint="http://127.0.0.1:8000/v1/chat/completions",
            model="remote-model",
            api_key_env="COMEMBUS_LLM_API_KEY",
        )

        self.assertIsInstance(client, OpenAICompatibleChatClient)
        self.assertEqual(client.model, "remote-model")
        self.assertEqual(client.api_key_env, "COMEMBUS_LLM_API_KEY")

    def test_root_cause_semantic_judge_accepts_close_real_llm_output(self) -> None:
        self.assertTrue(
            judge_root_cause_semantic(
                predicted="wrong database port (15432) pointing to obsolete replica",
                expected="wrong database port caused database timeout",
                scenario_tags=["database", "timeout", "port"],
            )
        )

    def test_root_cause_semantic_judge_rejects_irrelevant_output(self) -> None:
        self.assertFalse(
            judge_root_cause_semantic(
                predicted="cpu throttling due to noisy neighbor",
                expected="wrong database port caused database timeout",
                scenario_tags=["database", "timeout", "port"],
            )
        )

    def test_multiagent_smoke_core_function_runs_under_mock(self) -> None:
        result = run_llm_multiagent_smoke(
            provider="mock",
            llm_agents="planner,review",
            db_path=self.db_path,
        )

        self.assertEqual(parse_llm_agents("all"), {"planner", "log", "config", "review"})
        self.assertEqual(result["llm_call_count"], 2)
        self.assertEqual(result["used_fallback_count"], 0)
        self.assertEqual(result["root_cause_judge"], "semantic")
        self.assertTrue(result["root_cause_correct"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
