"""A fake DeepSeek (OpenAI chat completions, streaming) for tests and for trying the page offline.

    python -m tests.fake_llm 8199        # then DEEPSEEK_BASE_URL=http://127.0.0.1:8199

Each graph node is recognised by its system prompt:
  router     JSON intent: "任职要求" -> jd, "联系" -> contact, else qa; "测试科技" -> visitor company
  jd_parse   two requirements; jd_score: scores 3 and 0; jd_report / memory: a fixed text
  Q&A agent  if the last message is from the user and tools are offered, it calls search_docs and
             highlight_projects (arguments split over several chunks, like the real API); after tool
             results it answers with text that quotes the first search hit's link.
A message containing "boom" -> HTTP 500.
"""
import json
import sys
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
calls = []          # request bodies, for tests


def chunk(delta, finish=None, usage=None):
    c = {"id": "x", "object": "chat.completion.chunk", "created": int(time.time()), "model": "fake",
         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if delta is not None else []}
    if usage:
        c["usage"] = usage
    return f"data: {json.dumps(c, ensure_ascii=False)}\n\n"


USAGE = {"prompt_tokens": 1200, "completion_tokens": 80, "total_tokens": 1280,
         "prompt_cache_hit_tokens": 1000, "prompt_cache_miss_tokens": 200}


@app.post("/chat/completions")
async def completions(req: Request):
    body = await req.json()
    calls.append(body)
    msgs = body["messages"]
    last = msgs[-1]
    if last["role"] == "user" and "boom" in (last.get("content") or ""):
        return JSONResponse({"error": {"message": "boom"}}, 500)

    starts = [m["content"][:30] for m in msgs if m["role"] == "system"]
    is_ = lambda prefix: any(x.startswith(prefix) for x in starts)      # noqa: E731
    canned = None
    if is_("你是作品集 AI 助手的路由 Agent"):
        q = json.loads(last["content"])["当前消息"]
        intent = "jd" if "任职要求" in q else "contact" if "联系" in q else "qa"
        canned = json.dumps({"intent": intent, "reason": "测试",
                             "visitor": {"company": "测试科技" if "测试科技" in q else "", "role": "", "focus": ""}},
                            ensure_ascii=False)
    elif is_("你是 JD 拆解 Agent"):
        canned = json.dumps({"company": "测试科技", "role": "C++ 工程师", "requirements": [
            {"text": "熟悉 Modbus 协议", "kind": "must", "query": "Modbus"},
            {"text": "有 Rust 经验", "kind": "plus", "query": "Rust"}]}, ensure_ascii=False)
    elif is_("现在你是 JD 匹配的打分 Agent"):
        ev = json.loads(last["content"])["要求"][0]["检索到的证据"]
        canned = json.dumps({"items": [{"i": 1, "score": 3, "mine": "我做过 Modbus 接入", "evidence": ev[:1] + ["简历"]},
                                       {"i": 2, "score": 0, "mine": "", "evidence": []}]}, ensure_ascii=False)
    elif is_("现在你在写 JD 匹配报告的结尾"):
        canned = "### 差距\n- Rust 资料里没有\n### 总结\n我最匹配的是 Modbus。"
    elif is_("把下面这段招聘方"):
        canned = "对方问过几个问题。"

    def gen():
        if canned is not None:
            for i in range(0, len(canned), 20):
                yield chunk({"content": canned[i:i + 20]})
            yield chunk({}, "stop")
        elif last["role"] == "user" and body.get("tools"):
            q = json.dumps({"query": last["content"][:20]}, ensure_ascii=False)
            yield chunk({"role": "assistant", "content": None, "tool_calls": [
                {"index": 0, "id": "call_1", "type": "function", "function": {"name": "search_docs", "arguments": ""}}]})
            for i in range(0, len(q), 5):
                yield chunk({"tool_calls": [{"index": 0, "function": {"arguments": q[i:i + 5]}}]})
            yield chunk({"tool_calls": [{"index": 1, "id": "call_2", "type": "function",
                                         "function": {"name": "highlight_projects", "arguments": '{"projects": ["vision"]}'}}]})
            yield chunk({}, "tool_calls")
        else:
            url = ""
            for m in msgs:
                if m["role"] == "tool" and '"results"' in m["content"]:
                    r = json.loads(m["content"])["results"]
                    url = r[0]["url"] if r else ""
            text = f"我在 ai-vision 里做过这个。告警触发后从环形缓冲取前后 10 秒拼成 **MP4**，详见 [文档]({url})。"
            for i in range(0, len(text), 6):
                yield chunk({"content": text[i:i + 6]})
                time.sleep(0.02)
            yield chunk({}, "stop")
        yield chunk(None, usage=USAGE)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8199)
