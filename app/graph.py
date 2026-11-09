"""The agent as a LangGraph state graph: a router sends each question to one of three agents.

    START -> memory -> router --qa------> qa_agent ------------------------------------> finish -> END
                          |--jd------> jd_parse -> jd_evidence -> jd_score -> jd_report -> finish
                          |              (no requirements found) -> qa_agent
                          '--contact-> contact ----------------------------------------> finish

memory      compresses old turns into a summary when the conversation gets long
router      one JSON call: intent (qa / jd / contact) + who the visitor is (company, role, focus)
qa_agent    tool-calling loop over the knowledge base and live demo data (streamed)
jd_*        a fixed workflow: split the JD into requirements -> hybrid search per requirement
            (in parallel, no model call) -> score each one against the evidence (JSON) -> the table
            is rendered by code, the model only writes the gaps and the summary (streamed)
contact     no model call: leaving a message is not offered, so it gives the contact details
finish      appends the turn to the history

State is saved per session (thread_id = session id) by the checkpointer, so history, summary and
visitor survive a restart. Nodes report progress through the stream writer as (event, data) pairs;
agent.py forwards them to the page. Token usage goes out as "usage" events and is added up there.
"""
import asyncio
import json
import time
from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app import llm, prompt, tools
from app.config import settings

SYSTEM = prompt.build_system()
LABELS = {"memory": "记忆", "router": "路由 Agent", "qa_agent": "问答 Agent", "jd_parse": "JD 拆解",
          "jd_evidence": "逐条检索证据", "jd_score": "逐条打分", "jd_report": "匹配报告",
          "contact": "联系方式", "finish": "保存会话"}
INTENTS = {"qa", "jd", "contact"}


class State(TypedDict, total=False):
    # kept across turns
    history: list           # recent messages, verbatim: [{"role": "user" | "assistant", "content"}]
    summary: str            # older messages, compressed
    visitor: dict           # {"company", "role", "focus"}
    turns: int
    # this turn
    question: str
    intent: str
    jd: dict                # {"company", "role", "requirements": [{"text", "kind", "query"}]}
    evidence: dict          # "E1" -> {"project", "title", "url", "text"}
    req_evidence: list      # per requirement: ["E1", ...]
    scores: list            # per requirement: {"score", "mine", "evidence"}
    answer: str
    note: str               # what goes into the history instead of the question (a JD is stored condensed)


def emit(event, data):
    get_stream_writer()((event, data))


def node(name):
    """Wraps a node: node_start / node_end events with time taken and a one-line summary.
    The wrapped function returns (state update, summary)."""
    def wrap(fn):
        async def run(state):
            emit("node_start", {"node": name, "label": LABELS[name]})
            t = time.monotonic()
            update, summary = await fn(state)
            emit("node_end", {"node": name, "label": LABELS[name], "summary": summary,
                              "ms": round((time.monotonic() - t) * 1000)})
            return update
        run.__name__ = name
        return run
    return wrap


def count(usage):
    h, m, o = llm.usage_of(usage)
    emit("usage", {"hit": h, "miss": m, "out": o})


async def text_of(messages, max_tokens=600):
    msg = usage = None
    async for ev in llm.stream(messages, max_tokens=max_tokens, temperature=0.2):
        if ev[0] == "message":
            _, msg, usage = ev
    count(usage)
    return ((msg or {}).get("content") or "").strip()


# ---- memory ---------------------------------------------------------------------------------

@node("memory")
async def memory(state):
    history, keep = state.get("history") or [], settings.keep_messages
    chars = sum(len(m["content"]) for m in history)
    if len(history) <= keep or (chars <= settings.summarize_chars and len(history) <= keep * 2):
        return {}, f"上下文 {len(history)} 条消息，{chars} 字，不用压缩"
    old, recent = history[:-keep], history[-keep:]
    lines = [f"之前的摘要：{state['summary']}"] if state.get("summary") else []
    lines += [f"{'对方' if m['role'] == 'user' else '我'}：{m['content'][:1500]}" for m in old]
    summary = await text_of([{"role": "system", "content": prompt.SUMMARIZE},
                             {"role": "user", "content": "\n\n".join(lines)}])
    emit("memory", {"summary": summary})
    return {"history": recent, "summary": summary}, f"把较早的 {len(old)} 条消息压缩成 {len(summary)} 字摘要"


# ---- router ---------------------------------------------------------------------------------

def merge_visitor(old, new):
    out = dict(old or {})
    for k in ("company", "role", "focus"):
        v = str((new or {}).get(k) or "").strip()[:60]
        if v:
            out[k] = v
    return out


@node("router")
async def router(state):
    q = state["question"]
    recent = [{"role": m["role"], "content": m["content"][:300]} for m in (state.get("history") or [])[-2:]]
    data, usage = await llm.complete_json([
        {"role": "system", "content": prompt.ROUTER},
        {"role": "user", "content": prompt.router_input(q, state.get("visitor"), recent)}], max_tokens=300)
    count(usage)
    intent = data.get("intent") if data.get("intent") in INTENTS else "qa"
    if intent == "jd" and len(q) < 40:          # "看看匹配度" without a JD pasted: answer from what we know
        intent = "qa"
    visitor = merge_visitor(state.get("visitor"), data.get("visitor"))
    if visitor != (state.get("visitor") or {}):
        emit("memory", {"visitor": visitor})
    reason = str(data.get("reason") or "")[:80]
    return {"intent": intent, "visitor": visitor}, f"→ {LABELS['qa_agent' if intent == 'qa' else 'jd_parse' if intent == 'jd' else 'contact']}：{reason}"


# ---- Q&A agent: tool-calling loop -----------------------------------------------------------

async def call_tools(calls):
    for c in calls:
        try:
            inp = json.loads(c["function"]["arguments"])
        except ValueError:
            inp = c["function"]["arguments"]
        emit("tool_start", {"id": c["id"], "name": c["function"]["name"], "input": inp})

    async def one(c):
        s = time.monotonic()
        result, summary, args = await tools.run(c["function"]["name"], c["function"]["arguments"])
        return c, result, summary, args, round((time.monotonic() - s) * 1000)

    out = []
    for c, result, summary, args, ms in await asyncio.gather(*(one(c) for c in calls)):
        name = c["function"]["name"]
        ok = '"error"' not in result[:20]
        if name in tools.UI_TOOLS and ok:
            emit("ui_action", {"type": "highlight", "projects": args.get("projects", [])})
        emit("tool_end", {"id": c["id"], "name": name, "input": args, "ms": ms, "summary": summary, "ok": ok})
        out.append({"role": "tool", "tool_call_id": c["id"], "content": result})
    return out


@node("qa_agent")
async def qa_agent(state):
    ctx = prompt.context_message(state.get("summary"), state.get("visitor"))
    messages = [{"role": "system", "content": SYSTEM}] + ([ctx] if ctx else []) + \
        list(state.get("history") or []) + [{"role": "user", "content": state["question"]}]
    parts, used = [], []
    for step in range(1, settings.max_steps + 1):
        last = step == settings.max_steps          # no tools on the last step: it must answer
        msg = usage = None
        first = True
        async for ev in llm.stream(messages, tools=None if last else tools.SCHEMAS):
            if ev[0] == "text":
                if first and parts:                # a later step's text starts a new paragraph
                    parts.append("\n\n")
                    emit("text", {"delta": "\n\n"})
                first = False
                parts.append(ev[1])
                emit("text", {"delta": ev[1]})
            else:
                _, msg, usage = ev
        count(usage)
        messages.append(msg)
        if not msg.get("tool_calls"):
            break
        used += [c["function"]["name"] for c in msg["tool_calls"]]
        messages += await call_tools(msg["tool_calls"])
    return {"answer": "".join(parts)}, f"{step} 次模型调用" + (f"，工具：{'、'.join(used)}" if used else "")


# ---- JD matching workflow -------------------------------------------------------------------

@node("jd_parse")
async def jd_parse(state):
    data, usage = await llm.complete_json([{"role": "system", "content": prompt.JD_PARSE},
                                           {"role": "user", "content": state["question"][:settings.max_input_chars]}])
    count(usage)
    reqs = []
    for r in (data.get("requirements") or [])[:8]:
        if isinstance(r, dict) and str(r.get("text") or "").strip():
            text = str(r["text"]).strip()[:120]
            reqs.append({"text": text, "kind": "plus" if r.get("kind") == "plus" else "must",
                         "query": str(r.get("query") or text)[:80]})
    jd = {"company": str(data.get("company") or "")[:60], "role": str(data.get("role") or "")[:60], "requirements": reqs}
    visitor = merge_visitor(state.get("visitor"), jd)
    if visitor != (state.get("visitor") or {}):
        emit("memory", {"visitor": visitor})
    must = sum(r["kind"] == "must" for r in reqs)
    return {"jd": jd, "visitor": visitor}, f"拆出 {len(reqs)} 条要求（必须 {must}，加分 {len(reqs) - must}）"


def after_parse(state):
    return "jd_evidence" if state.get("jd", {}).get("requirements") else "qa_agent"


@node("jd_evidence")
async def jd_evidence(state):
    reqs = state["jd"]["requirements"]
    for i, r in enumerate(reqs):
        emit("tool_start", {"id": f"jd{i}", "name": "search_docs", "input": {"query": r["query"]}})

    async def one(i, r):
        s = time.monotonic()
        hits = await asyncio.to_thread(tools.retrieve, r["query"], None, 3, 400)
        return i, hits, round((time.monotonic() - s) * 1000)

    evidence, req_ev, keys = {}, [], {}
    for i, hits, ms in await asyncio.gather(*(one(i, r) for i, r in enumerate(reqs))):
        ids = []
        for h in hits:
            k = (h["title"], h["text"][:80])
            if k not in keys:
                keys[k] = f"E{len(keys) + 1}"
                evidence[keys[k]] = {x: h[x] for x in ("project", "title", "url", "text")}
            ids.append(keys[k])
        req_ev.append(ids)
        emit("tool_end", {"id": f"jd{i}", "name": "search_docs", "input": {"query": reqs[i]["query"]}, "ms": ms,
                          "ok": True, "summary": "、".join(f"{k} {evidence[k]['title'].split(' > ')[-1]}" for k in ids) or "没有找到"})
    return {"evidence": evidence, "req_evidence": req_ev}, f"{len(reqs)} 条要求并行检索，得到 {len(evidence)} 段证据"


@node("jd_score")
async def jd_score(state):
    reqs, ev = state["jd"]["requirements"], state["evidence"]
    payload = {"要求": [{"i": i + 1, "text": r["text"], "kind": r["kind"], "检索到的证据": state["req_evidence"][i]}
                        for i, r in enumerate(reqs)],
               "证据": {k: {"title": v["title"], "text": v["text"]} for k, v in ev.items()}}
    data, usage = await llm.complete_json([
        {"role": "system", "content": SYSTEM}, {"role": "system", "content": prompt.JD_SCORE},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], max_tokens=1500)
    count(usage)
    by_i = {it.get("i"): it for it in data.get("items") or [] if isinstance(it, dict)}
    scores = []
    for i in range(len(reqs)):
        it = by_i.get(i + 1) or {}
        try:
            sc = min(max(int(it.get("score", 0)), 0), 3)
        except (TypeError, ValueError):
            sc = 0
        refs = [e for e in it.get("evidence") or [] if e == "简历" or e in ev]
        scores.append({"score": sc, "mine": str(it.get("mine") or ("资料里没有" if sc == 0 else ""))[:80],
                       "evidence": refs[:3]})
    pct = match_pct(reqs, scores)
    return {"scores": scores}, f"匹配度 {pct}%：" + " ".join(str(s["score"]) for s in scores)


def match_pct(reqs, scores):
    w = [2 if r["kind"] == "must" else 1 for r in reqs]
    return round(100 * sum(x * s["score"] for x, s in zip(w, scores)) / max(3 * sum(w), 1))


def cell(s):
    return str(s).replace("|", "/").replace("\n", " ")


def render_table(state):
    jd, reqs, scores, ev = state["jd"], state["jd"]["requirements"], state["scores"], state["evidence"]
    pct = match_pct(reqs, scores)
    musts = [s for r, s in zip(reqs, scores) if r["kind"] == "must"]
    target = f"「{jd['role']}」" if jd.get("role") else "这个岗位"
    lines = [f"**我和{target}的匹配度：{pct}%**（必须项 {len(musts)} 条，其中 {sum(s['score'] >= 2 for s in musts)} 条有对应经历）",
             "", "| 要求 | 类型 | 匹配 | 我的对应经历 | 证据 |", "|---|---|---|---|---|"]
    for r, s in zip(reqs, scores):
        links = []
        for e in s["evidence"]:
            if e == "简历":
                links.append("简历")
            else:
                x = ev[e]
                name = tools.NAMES.get(x["project"], x["project"])
                links.append(f"[{name} · {x['title'].split(' > ')[-1]}]({x['url']})")
        lines.append(f"| {cell(r['text'])} | {'必须' if r['kind'] == 'must' else '加分'} | "
                     f"{'●' * s['score'] + '○' * (3 - s['score'])} | {cell(s['mine'])} | {'、'.join(links) or '—'} |")
    return "\n".join(lines)


@node("jd_report")
async def jd_report(state):
    table = render_table(state)
    emit("text", {"delta": table + "\n\n"})
    # highlight the demo projects that back the requirements I match
    hits = {}
    for s in state["scores"]:
        if s["score"] >= 2:
            for e in s["evidence"]:
                p = state["evidence"].get(e, {}).get("project")
                if p and p != "agent":
                    hits[p] = hits.get(p, 0) + 1
    projects = sorted(hits, key=lambda p: -hits[p])[:4]
    if projects:
        emit("ui_action", {"type": "highlight", "projects": projects})
    parts = []
    msg = usage = None
    async for ev in llm.stream([{"role": "system", "content": SYSTEM}, {"role": "system", "content": prompt.JD_REPORT},
                                {"role": "user", "content": table}], max_tokens=600):
        if ev[0] == "text":
            parts.append(ev[1])
            emit("text", {"delta": ev[1]})
        else:
            _, msg, usage = ev
    count(usage)
    jd = state["jd"]
    note = (f"（贴了一份 JD：{jd.get('company') or ''} {jd.get('role') or ''}，要求："
            + "；".join(r["text"] for r in jd["requirements"]) + "）")
    return {"answer": table + "\n\n" + "".join(parts), "note": note}, \
        f"表格由程序生成，模型写差距和总结；高亮 {'、'.join(tools.NAMES[p] for p in projects) or '无'}"


# ---- contact --------------------------------------------------------------------------------

@node("contact")
async def contact(state):
    v = state.get("visitor") or {}
    about = f"关于{v.get('company') or '贵公司'}的{v['role']}岗位，" if v.get("role") else ""
    text = (f"{about}可以直接联系我本人：\n\n- 电话：{prompt.PHONE}\n- 邮箱：{prompt.EMAIL}\n\n"
            "这里暂时不支持留言。联系时说一下公司和岗位，我好提前准备。")
    emit("text", {"delta": text})
    return {"answer": text}, "直接给出联系方式（没有调用模型）"


# ---- finish ---------------------------------------------------------------------------------

@node("finish")
async def finish(state):
    history = list(state.get("history") or []) + [
        {"role": "user", "content": state.get("note") or state["question"]},
        {"role": "assistant", "content": state.get("answer") or ""}]
    return {"history": history, "turns": (state.get("turns") or 0) + 1}, f"会话共 {len(history)} 条消息"


def build():
    g = StateGraph(State)
    for fn in (memory, router, qa_agent, jd_parse, jd_evidence, jd_score, jd_report, contact, finish):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "memory")
    g.add_edge("memory", "router")
    g.add_conditional_edges("router", lambda s: s["intent"], {"qa": "qa_agent", "jd": "jd_parse", "contact": "contact"})
    g.add_conditional_edges("jd_parse", after_parse, ["jd_evidence", "qa_agent"])
    g.add_edge("jd_evidence", "jd_score")
    g.add_edge("jd_score", "jd_report")
    for n in ("qa_agent", "jd_report", "contact"):
        g.add_edge(n, "finish")
    g.add_edge("finish", END)
    return g


def mermaid():
    return build().compile().get_graph().draw_mermaid()
