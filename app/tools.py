"""Tools the model can call. Server tools return data; UI tools (highlight_projects) are carried out
by the browser: the server sends a ui_action event and tells the model it is done."""
import asyncio
import json
import re

import httpx

from app.config import settings
from app.search import Embedder, Index, tokens

PROJECTS = ["vision", "qt", "ems", "cam", "gvtest", "medlens", "scada", "agent"]
NAMES = {"vision": "ai-vision", "qt": "Qt 拓扑监控上位机", "ems": "储能 EMS", "cam": "工业相机采集",
         "gvtest": "GVTest", "medlens": "MedLens", "scada": "PowerGrid SCADA", "agent": "作品集 AI 助手"}
# How much of each hit the model sees. Tool results are new input every time, so they are most of
# the cache-miss tokens (61% of the cost). Write-ups are short and dense: kept whole. Doc sections
# are cut to the window that matches the query best (most sections are shorter than this anyway).
SNIPPET = 600
WRITEUP_MAX = 900
# The domain has no ICP filing (blocked in China): demo links in the docs become IP:port, the same
# rule the homepage uses for its "进入" buttons when opened by IP.
PUBLIC = "http://47.117.29.74"
PORTS = {"vision": 8090, "qt": 8080, "ems": 8095, "cam": 8098, "gvtest": 8097, "medlens": 8010, "scada": 8088}
_DOMAIN = re.compile(r"https?://(?:(\w+)\.)?edgebot\.vip(?::\d+)?")


def public_links(text):
    def sub(m):
        port = PORTS.get(m.group(1) or "")
        return f"{PUBLIC}:{port}" if port else PUBLIC
    return _DOMAIN.sub(sub, text)

index = Index.load(settings.knowledge_dir / "index.json", settings.knowledge_dir / "vectors.npz",
                   Embedder(settings.embed_model, settings.model_dir) if settings.embed_model else None)


def where(via):
    """Which rankings found a hit, for the page: "关键词#2 语义#1"."""
    return " ".join(f"{ {'bm25': '关键词', 'vec': '语义'}[k]}#{r}" for k, r in via.items())


def excerpt(text, query, limit):
    """The `limit` characters of text that share the most words with the query, starting at a line break."""
    if len(text) <= limit:
        return text
    q = set(tokens(query))
    starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n" and i + 1 < len(text) - limit // 3]
    best = max(starts, key=lambda st: (sum(t in q for t in tokens(text[st:st + limit])), -st))
    return ("…" if best else "") + text[best:best + limit] + ("…" if best + limit < len(text) else "")


def retrieve(query, project=None, k=4, snippet=SNIPPET):
    """Search the knowledge base (blocking: BM25 + a ~5 ms query embedding). Results ready for a model."""
    return [{"project": h["project"], "title": h["title"], "url": h["url"], "found_by": where(h["via"]),
             "text": public_links(excerpt(h["text"], query,
                                          WRITEUP_MAX if h.get("source") == "writeup" else snippet))}
            for h in index.search(query, project or None, k=k)]


def _fn(name, desc, props, required=()):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": list(required),
                       "additionalProperties": False}}}


SCHEMAS = [
    _fn("search_docs", "检索我的项目知识库（我写的项目说明 + 各项目 README 和设计文档，关键词 + 语义混合检索），"
                       "返回最相关的几段内容和 GitHub 链接。讲项目细节前先查。",
        {"query": {"type": "string", "description": "关键词，中文或英文，例如：告警录像 拼接 MP4"},
         "project": {"type": "string", "enum": PROJECTS, "description": "只查某个项目（可选）"}},
        ["query"]),
    _fn("highlight_projects", "在作品集页面上高亮和当前回答相关的项目卡片。",
        {"projects": {"type": "array", "items": {"type": "string", "enum": PROJECTS}, "maxItems": 4}},
        ["projects"]),
    _fn("get_project_status", "查询作品集各个在线演示现在是否在运行（实时）。", {}),
    _fn("get_vision_live", "查询 ai-vision 演示的实时情况：正在分析的视频路数、帧率、推理耗时、CPU、近 24 小时告警数。", {}),
]


async def search_docs(query, project=None):
    res = await asyncio.to_thread(retrieve, query, project)
    if not res:
        return {"results": [], "note": "没有找到相关内容，可以换个关键词"}, "没有找到"
    summary = f"{index.mode} 找到 {len(res)} 段：" + "、".join(
        f"{h['title'].split(' > ')[-1]}（{h['found_by']}）" for h in res)
    return {"results": res}, summary


async def highlight_projects(projects):
    ids = [p for p in projects if p in PROJECTS][:4]
    return {"ok": True, "note": "已在页面上高亮"}, "高亮 " + "、".join(NAMES[p] for p in ids)


async def get_project_status():
    async with httpx.AsyncClient(timeout=3, trust_env=False) as c:
        async def one(url):
            try:
                return (await c.get(url)).is_success
            except httpx.HTTPError:
                return False
        oks = await asyncio.gather(*(one(u) for u in settings.status_urls.values()))
    status = {NAMES.get(k, k): ("运行中" if ok else "暂不可用") for k, ok in zip(settings.status_urls, oks)}
    up = sum(oks)
    return {"status": status}, f"{up}/{len(oks)} 个在线"


async def get_vision_live():
    base = settings.vision_api.rstrip("/")
    async with httpx.AsyncClient(timeout=3, trust_env=False) as c:
        try:
            devices, stats = await asyncio.gather(c.get(f"{base}/api/devices"),
                                                  c.get(f"{base}/api/alarms/stats", params={"hours": 24}))
            devices, stats = devices.json(), stats.json()
        except (httpx.HTTPError, ValueError):
            return {"error": "ai-vision 暂时连不上"}, "连不上"
    streams = []
    for d in devices:
        w = d.get("worker") or {}
        s = w.get("stats") or {}
        if not (d.get("online") and w.get("state") == "running"):
            continue
        streams.append({"name": d["stream"], "task": "姿态识别/摔倒检测" if d.get("task") == "pose" else "人车检测",
                        "resolution": s.get("resolution"), "fps": s.get("out_fps"),
                        "infer_ms": s.get("infer_ms"), "cpu_pct": s.get("cpu"),
                        "tracks": s.get("tracks"), "uptime_h": round((w.get("uptime") or 0) / 3600, 1)})
    out = {"analyzing_streams": streams, "devices_total": len(devices),
           "alarms_24h_by_type": stats.get("by_type"), "alarm_type_names":
           {"intrusion": "区域入侵", "line": "越线", "dwell": "逗留", "crowd": "人数超限", "fall": "摔倒"}}
    return out, f"{len(streams)} 路在分析，24 小时告警 {sum((stats.get('by_type') or {}).values())} 条"


HANDLERS = {"search_docs": search_docs, "highlight_projects": highlight_projects,
            "get_project_status": get_project_status, "get_vision_live": get_vision_live}
UI_TOOLS = {"highlight_projects"}


async def run(name, args_json):
    """Returns (result for the model as a JSON string, short summary for the page, parsed args)."""
    try:
        args = json.loads(args_json or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
    except ValueError as e:
        return json.dumps({"error": f"参数不是合法 JSON：{e}"}, ensure_ascii=False), "参数错误", {}
    fn = HANDLERS.get(name)
    if not fn:
        return json.dumps({"error": f"没有这个工具：{name}"}, ensure_ascii=False), "未知工具", args
    try:
        result, summary = await fn(**args)
    except TypeError as e:          # wrong / missing arguments
        return json.dumps({"error": f"参数错误：{e}"}, ensure_ascii=False), "参数错误", args
    return json.dumps(result, ensure_ascii=False), summary, args
