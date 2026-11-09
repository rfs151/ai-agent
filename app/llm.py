"""DeepSeek calls (OpenAI-compatible API), always streamed.

stream() yields ("text", delta) while the answer is generated, then ("message", msg, usage).
History rule (DeepSeek thinking mode): an assistant message with tool calls is sent back exactly as
returned, including reasoning_content, or the API answers 400. With thinking disabled (the default)
there is no reasoning_content and nothing changes.
"""
import json

import openai

from app.config import settings

_client = None


def client():
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url,
                                     timeout=60, max_retries=1)
    return _client


def cost(hit, miss, out):
    return (hit * settings.price_hit + miss * settings.price_miss + out * settings.price_out) / 1e6


def usage_of(u):
    """(cache hit, cache miss, output) tokens."""
    if not u:
        return 0, 0, 0
    h = getattr(u, "prompt_cache_hit_tokens", None)
    m = getattr(u, "prompt_cache_miss_tokens", None)
    if h is None or m is None:                  # API without the cache fields
        h, m = 0, u.prompt_tokens
    return h, m, u.completion_tokens


async def stream(messages, tools=None, json_mode=False, max_tokens=None, temperature=None):
    kw = dict(model=settings.model, messages=messages, stream=True, stream_options={"include_usage": True},
              max_tokens=max_tokens or settings.max_tokens,
              temperature=settings.temperature if temperature is None else temperature,
              extra_body={"thinking": {"type": settings.thinking}})
    if tools:
        kw["tools"] = tools
    if json_mode:
        kw["response_format"] = {"type": "json_object"}
    s = await client().chat.completions.create(**kw)
    content, reasoning, calls, usage = [], [], {}, None
    async for chunk in s:
        if chunk.usage:
            usage = chunk.usage
        if not chunk.choices:
            continue
        d = chunk.choices[0].delta
        if d.content:
            content.append(d.content)
            yield ("text", d.content)
        rc = getattr(d, "reasoning_content", None)
        if rc:
            reasoning.append(rc)
        for tc in d.tool_calls or []:
            c = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
            c["id"] = tc.id or c["id"]
            if tc.function:
                c["name"] += tc.function.name or ""
                c["args"] += tc.function.arguments or ""
    msg = {"role": "assistant", "content": "".join(content) or None}
    if reasoning:
        msg["reasoning_content"] = "".join(reasoning)
    if calls:
        msg["tool_calls"] = [{"id": c["id"], "type": "function",
                              "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                             for _, c in sorted(calls.items())]
    yield ("message", msg, usage)


async def complete_json(messages, max_tokens=1500):
    """One JSON-mode call (temperature 0). Returns (parsed dict, usage); {} if the model's JSON is broken."""
    msg = usage = None
    async for ev in stream(messages, json_mode=True, max_tokens=max_tokens, temperature=0):
        if ev[0] == "message":
            _, msg, usage = ev
    text = (msg or {}).get("content") or ""
    try:
        data = json.loads(text[text.find("{"):text.rfind("}") + 1] if "{" in text else text)
    except ValueError:
        data = {}
    return (data if isinstance(data, dict) else {}), usage
