# -*- coding: utf-8 -*-
"""llm.py 三协议单测：monkeypatch httpx.post，验证 URL/payload/headers 与响应解析。"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import llm  # noqa: E402

JSON_BODY = json.dumps({"title": "测试", "summary": "摘要"}, ensure_ascii=False)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload, ensure_ascii=False)


class Recorder:
    """记录最后一次 post 调用，并返回固定 payload。"""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        return FakeResponse(self._payload)


class TestLLMProviders(unittest.TestCase):
    def _cfg(self, base, provider, key="sk-x"):
        return {"api_key": key, "base_url": base, "model": "m", "provider": provider}

    def test_detect(self):
        self.assertEqual(llm._detect_provider("https://api.anthropic.com", "auto"), "anthropic")
        self.assertEqual(llm._detect_provider("https://api.openai.com/v1", "auto"), "openai")
        self.assertEqual(llm._detect_provider("https://x.test/v1/responses", "auto"), "responses")
        self.assertEqual(llm._detect_provider("https://api.openai.com/v1", "anthropic"), "anthropic")

    def test_openai_chat(self):
        rec = Recorder({"choices": [{"message": {"content": JSON_BODY}}]})
        with mock.patch.object(llm.httpx, "post", rec):
            out = llm._call_openai_chat(self._cfg("https://api.openai.com/v1", "openai"), "sys", "user", 0.3)
        self.assertEqual(out["title"], "测试")
        url, payload, headers = rec.calls[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual(payload["messages"][1]["content"], "user")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(headers["Authorization"], "Bearer sk-x")

    def test_openai_responses(self):
        rec = Recorder({"output_text": JSON_BODY})
        with mock.patch.object(llm.httpx, "post", rec):
            out = llm._call_openai_responses(self._cfg("https://api.openai.com/v1", "responses"), "sys", "user", 0.3)
        self.assertEqual(out["summary"], "摘要")
        url, payload, headers = rec.calls[0]
        self.assertTrue(url.endswith("/responses"))
        self.assertEqual(payload["instructions"], "sys")
        self.assertEqual(payload["input"], "user")
        self.assertEqual(payload["text"], {"format": {"type": "json_object"}})

    def test_anthropic(self):
        rec = Recorder({"content": [{"type": "text", "text": JSON_BODY}]})
        with mock.patch.object(llm.httpx, "post", rec):
            out = llm._call_anthropic(self._cfg("https://api.anthropic.com", "anthropic"), "sys", "user", 0.3)
        self.assertEqual(out["title"], "测试")
        url, payload, headers = rec.calls[0]
        self.assertTrue(url.endswith("/v1/messages"))
        self.assertEqual(payload["system"], "sys")
        self.assertEqual(payload["messages"], [{"role": "user", "content": "user"}])
        self.assertTrue(payload["max_tokens"] > 0)
        self.assertEqual(headers["x-api-key"], "sk-x")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")

    def test_anthropic_via_chat_json_autodetect(self):
        rec = Recorder({"content": [{"type": "text", "text": JSON_BODY}]})
        with mock.patch.object(llm.httpx, "post", rec):
            with mock.patch.object(llm, "get_llm_config",
                                   return_value=self._cfg("https://api.anthropic.com", "auto")):
                out = llm.chat_json("sys", "user")
        self.assertEqual(out["title"], "测试")
        self.assertTrue(rec.calls[0][0].endswith("/v1/messages"))

    def test_error_detail(self):
        resp = FakeResponse({"error": {"message": "invalid api key"}}, status=401)
        with self.assertRaises(RuntimeError) as ctx:
            llm._raise_for(resp)
        self.assertIn("401", str(ctx.exception))
        self.assertIn("invalid api key", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)