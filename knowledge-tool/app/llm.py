"""通用 LLM 客户端，支持三种协议：

1. OpenAI Chat Completions（默认）   POST {base}/chat/completions
2. OpenAI Responses API               POST {base}/responses
3. Anthropic Messages API             POST {base}/v1/messages

配置来源（优先级从高到低）：
1. 环境变量 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / LLM_PROVIDER
2. data/settings.json 中的 {"llm": {...}}（可在「设置」页保存）

provider 取值：auto（按 base_url 自动判断）/ openai / responses / anthropic。
不配置 Key 时：调用方应给出可理解的中文提示，本模块只抛网络/协议类错误。
"""
from __future__ import annotations

import json

import httpx

from . import config
from .store import load_json, save_json


def _settings_llm() -> dict:
    d = load_json(config.DATA_ROOT / "settings.json", default={})
    return d.get("llm", {}) or {}


def get_llm_config() -> dict:
    import os
    s = _settings_llm()
    return {
        "api_key": os.environ.get("LLM_API_KEY", s.get("api_key", "")),
        "base_url": os.environ.get("LLM_BASE_URL", s.get("base_url", "https://api.openai.com/v1")),
        "model": os.environ.get("LLM_MODEL", s.get("model", "gpt-4o-mini")),
        "provider": os.environ.get("LLM_PROVIDER", s.get("provider", "auto")),
    }


def save_llm_config(cfg: dict) -> dict:
    """把 LLM 配置持久化到 data/settings.json（web UI 保存）。"""
    path = config.DATA_ROOT / "settings.json"
    d = load_json(path, default={})
    d["llm"] = {
        "api_key": (cfg.get("api_key") or "").strip(),
        "base_url": (cfg.get("base_url") or "https://api.openai.com/v1").strip(),
        "model": (cfg.get("model") or "gpt-4o-mini").strip(),
        "provider": (cfg.get("provider") or "auto").strip(),
    }
    save_json(path, d)
    return d["llm"]


def is_configured() -> bool:
    return bool(get_llm_config()["api_key"])


def _detect_provider(base_url: str, provider: str) -> str:
    """解析最终协议：显式指定优先，否则按 base_url 启发。"""
    p = (provider or "auto").strip().lower()
    if p in ("openai", "responses", "anthropic"):
        return p
    b = base_url.rstrip("/").lower()
    if "anthropic" in b:
        return "anthropic"
    if b.endswith("/responses"):
        return "responses"
    return "openai"


def _call_openai_chat(cfg, system, user, temperature):
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    base = {
        "model": cfg["model"],
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
    # 先试 response_format=json_object；部分模型（如某些 deepseek 版本）不支持→降级重试
    try:
        resp = httpx.post(url, json={**base, "response_format": {"type": "json_object"}},
                          headers=headers, timeout=90)
        _raise_for(resp)
        return _safe_jsonify(resp.json()["choices"][0]["message"]["content"])
    except RuntimeError as e:
        if _is_json_obj_error(e):
            resp = httpx.post(url, json=base, headers=headers, timeout=90)
            _raise_for(resp)
            return _safe_jsonify(resp.json()["choices"][0]["message"]["content"])
        raise


def _is_json_obj_error(e: Exception) -> bool:
    """判断错误是否因 response_format/json_object 不被模型支持（400 类）。"""
    s = str(e).lower()
    return ("json" in s or "response_format" in s or "structured output" in s or "schema" in s) and "400" in s


def _call_openai_responses(cfg, system, user, temperature):
    base = cfg["base_url"].rstrip("/")
    url = base + "/responses" if not base.endswith("/responses") else base
    payload_json_fmt = {
        "model": cfg["model"],
        "temperature": temperature,
        "instructions": system,
        "input": user,
        "text": {"format": {"type": "json_object"}},
    }
    payload_plain = {
        "model": cfg["model"],
        "temperature": temperature,
        "instructions": system,
        "input": user,
    }
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
    try:
        resp = httpx.post(url, json=payload_json_fmt, headers=headers, timeout=90)
        _raise_for(resp)
        return _extract_responses_text(resp.json())
    except RuntimeError as e:
        if _is_json_obj_error(e):
            resp = httpx.post(url, json=payload_plain, headers=headers, timeout=90)
            _raise_for(resp)
            return _extract_responses_text(resp.json())
        raise


def _extract_responses_text(data: dict) -> dict:
    # Responses API：文本在 output[].content[].text，或便捷字段 output_text
    if data.get("output_text"):
        return _safe_jsonify(data["output_text"])
    for block in data.get("output", []):
        for c in (block.get("content") or []):
            if c.get("type") == "output_text" or c.get("type") == "text":
                return _safe_jsonify(c.get("text", ""))
    raise RuntimeError("LLM(Responses) 响应无文本内容")


def _call_anthropic(cfg, system, user, temperature):
    base = cfg["base_url"].rstrip("/")
    url = base + "/v1/messages" if not base.endswith("/messages") else base
    payload = {
        "model": cfg["model"],
        "max_tokens": 4096,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    headers = {
        "x-api-key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    resp = httpx.post(url, json=payload, headers=headers, timeout=90)
    _raise_for(resp)
    data = resp.json()
    texts = [b.get("text", "") for b in (data.get("content") or []) if b.get("type") == "text"]
    if not texts:
        raise RuntimeError("LLM(Anthropic) 响应无文本内容")
    return _safe_jsonify("\n".join(texts))


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code == 200:
        return
    detail = ""
    try:
        err = resp.json()
        detail = (err.get("error") or {}).get("message", "") if isinstance(err.get("error"), dict) \
            else str(err)[:200]
    except Exception:  # noqa: BLE001
        detail = resp.text[:200]
    raise RuntimeError(f"LLM 返回 {resp.status_code}：{detail or '未知错误'}")


def _safe_jsonify(content: str) -> dict:
    if not content:
        raise RuntimeError("LLM 返回空内容")
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`").lstrip("json").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"LLM 未返回合法 JSON：{content[:200]}") from e


def chat_json(system: str, user: str, temperature: float = 0.3) -> dict:
    """调用 LLM，要求返回 JSON 对象。按配置/自动检测选择协议。"""
    cfg = get_llm_config()
    if not cfg["api_key"]:
        raise RuntimeError("未配置 LLM API Key（可在「设置」页填写或设环境变量 LLM_API_KEY）")

    provider = _detect_provider(cfg["base_url"], cfg["provider"])
    prompt = system + "\n请严格只输出 JSON（不要 markdown 代码块）。"
    try:
        if provider == "anthropic":
            return _call_anthropic(cfg, prompt, user, temperature)
        if provider == "responses":
            return _call_openai_responses(cfg, prompt, user, temperature)
        return _call_openai_chat(cfg, prompt, user, temperature)
    except httpx.HTTPError as e:
        raise RuntimeError(f"请求 LLM 失败：{e.__class__.__name__}（检查网络/base_url）") from e