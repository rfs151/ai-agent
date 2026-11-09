"""Runs the graph (graph.py) for one question and turns what the nodes report into page events.

Events (main.py sends them as SSE):
    node_start {"node", "label"}               a graph node starts (router, qa_agent, jd_parse...)
    node_end   {"node", "label", "summary", "ms"}
    memory     {"visitor"} | {"summary"}       what the assistant now remembers about the visitor
    text       {"delta"}                       answer text as it is generated
    tool_start {"id", "name", "input"}
    tool_end   {"id", "ms", "summary", "ok"}
    ui_action  {"type": "highlight", "projects"}
    done       {"hit", "miss", "out", "cost", "ms", "steps", "intent", "path"}
"""
import time

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app import graph as g
from app import llm
from app.config import settings

_graph = None
_saver_cm = None


async def open_graph(path=None):
    """Compile the graph with a SQLite checkpointer (one thread per chat session)."""
    global _graph, _saver_cm
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _saver_cm = AsyncSqliteSaver.from_conn_string(str(path or settings.data_dir / "graph.db"))
    saver = await _saver_cm.__aenter__()
    _graph = g.build().compile(checkpointer=saver)


async def close_graph():
    global _graph, _saver_cm
    if _saver_cm:
        await _saver_cm.__aexit__(None, None, None)
    _graph = _saver_cm = None


def _config(sid):
    return {"configurable": {"thread_id": sid}}


async def session_state(sid):
    return (await _graph.aget_state(_config(sid))).values if sid else {}


async def answer(sid, question):
    t0 = time.monotonic()
    hit = miss = out = steps = 0
    trace, path, intent = [], [], None
    start = {"question": question, "intent": "", "answer": "", "note": "",
             "jd": {}, "evidence": {}, "req_evidence": [], "scores": []}
    async for ev, data in _graph.astream(start, _config(sid), stream_mode="custom"):
        if ev == "usage":
            hit, miss, out, steps = hit + data["hit"], miss + data["miss"], out + data["out"], steps + 1
            continue
        if ev == "node_end":
            path.append(data["node"])
            trace.append({"node": data["node"], "summary": data["summary"], "ms": data["ms"]})
        elif ev == "tool_end":
            trace.append({"tool": data["name"], "input": data.get("input"), "summary": data["summary"], "ms": data["ms"]})
            data = {k: v for k, v in data.items() if k != "input"}
        yield ev, data
    state = await session_state(sid)
    intent = state.get("intent")
    yield "done", {"hit": hit, "miss": miss, "out": out, "cost": round(llm.cost(hit, miss, out), 5),
                   "ms": round((time.monotonic() - t0) * 1000), "steps": steps, "intent": intent,
                   "path": path, "trace": trace, "visitor": state.get("visitor") or {}}
