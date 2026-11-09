"""HTTP API (all under /agent/, which the gateway forwards here unchanged).

    GET  /agent/api/health   {"ok", "model", "search"}: the page shows the chat only when ok
    GET  /agent/api/graph    the agent graph as Mermaid text
    POST /agent/api/chat     {"session"?, "message"} -> text/event-stream (see agent.py for the events)

Conversation state lives in the graph's SQLite checkpointer (data/graph.db), keyed by session id;
only the per-session lock (one question at a time) is kept in memory.
"""
import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager

import openai
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import agent, store, tools
from app.config import settings
from app.graph import mermaid

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(_app):
    await agent.open_graph()
    log.info("graph ready, search: %s, %d chunks", tools.index.mode, len(tools.index.items))
    yield
    await agent.close_graph()


app = FastAPI(title="ai-agent", docs_url=None, redoc_url=None, lifespan=lifespan)
locks = {}          # session id -> asyncio.Lock (dropped when idle)


class ChatIn(BaseModel):
    session: str | None = Field(None, max_length=64)
    message: str = Field(..., min_length=1)


def client_ip(req: Request):
    """Cloudflare -> frp -> Caddy -> here: the visitor's address is in CF-Connecting-IP / X-Forwarded-For."""
    h = req.headers
    ip = h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip()
    return ip or (req.client.host if req.client else "?")


async def get_session(sid):
    """(session id, saved state); unknown or missing ids get a new session."""
    state = await agent.session_state(sid) if sid else {}
    if not state:
        sid = secrets.token_urlsafe(12)
    for k in [k for k, lk in locks.items() if not lk.locked()]:
        locks.pop(k)
    return sid, state


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/agent/api/health")
def health():
    return {"ok": bool(settings.api_key), "model": settings.model, "search": tools.index.mode}


@app.get("/agent/api/graph")
def graph():
    return {"mermaid": mermaid()}


@app.post("/agent/api/chat")
async def chat(body: ChatIn, req: Request):
    if not settings.api_key:
        return JSONResponse({"error": "助手暂未开放"}, 503)
    text = body.message.strip()
    if len(text) > settings.max_input_chars:
        return JSONResponse({"error": f"消息太长了（最多 {settings.max_input_chars} 字）"}, 400)
    ip = client_ip(req)
    if msg := store.check_limits(ip):
        return JSONResponse({"error": msg}, 429)
    sid, state = await get_session(body.session)
    if (state.get("turns") or 0) >= settings.max_turns:
        return JSONResponse({"error": "这轮对话已经很长了，点「新对话」重新开始吧。"}, 400)
    lock = locks.setdefault(sid, asyncio.Lock())
    if lock.locked():
        return JSONResponse({"error": "上一个问题还在回答中"}, 409)

    async def events():
        async with lock:
            row = store.start(sid, ip, text)
            parts, done, err = [], None, None
            yield sse("meta", {"session": sid, "model": settings.model, "search": tools.index.mode})
            try:
                async for ev, data in agent.answer(sid, text):
                    if ev == "text":
                        parts.append(data["delta"])
                    elif ev == "done":
                        done = data
                        data = {k: v for k, v in data.items() if k not in ("trace", "visitor")}
                    yield sse(ev, data)
            except openai.APIStatusError as e:
                err = f"{e.status_code} {e.message}"
            except (openai.APIConnectionError, openai.APITimeoutError) as e:
                err = f"连接模型服务失败：{e}"
            except Exception as e:      # keep the page usable whatever happens
                log.exception("chat failed")
                err = f"{type(e).__name__}: {e}"
            if err:
                log.warning("chat error (%s): %s", ip, err)     # history is only written by the last node
                yield sse("error", {"message": "模型服务暂时出错了，请稍后再试。"})
            store.finish(row, "".join(parts), done, err)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if settings.dev_site:       # local testing: serve the homepage here, no gateway needed
    app.mount("/", StaticFiles(directory=settings.dev_site, html=True), name="site")


def run():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=settings.port, proxy_headers=False)


if __name__ == "__main__":
    run()
