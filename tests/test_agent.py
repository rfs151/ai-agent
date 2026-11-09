"""End to end against the fake model: SSE events, tool loop, history, limits, errors."""
import json
import os
import socket
import threading
import time

import pytest


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = free_port()
os.environ.update(DEEPSEEK_API_KEY="test", DEEPSEEK_BASE_URL=f"http://127.0.0.1:{PORT}",
                  RATE_PER_HOUR="5", EMBED_MODEL="", KEEP_MESSAGES="2", STATUS_URLS=json.dumps({"vision": f"http://127.0.0.1:{PORT}/nope"}))


@pytest.fixture(scope="module", autouse=True)
def fake_llm(tmp_path_factory):
    os.environ["DATA_DIR"] = str(tmp_path_factory.mktemp("data"))
    import uvicorn
    from tests import fake_llm
    server = uvicorn.Server(uvicorn.Config(fake_llm.app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    yield fake_llm
    server.should_exit = True


@pytest.fixture(scope="module")
def client(fake_llm):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:      # one event loop for all requests, as under uvicorn
        yield c


def events(resp):
    out = []
    for block in resp.text.strip().split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def ask(client, msg, session=None, ip="1.1.1.1"):
    return client.post("/agent/api/chat", json={"message": msg, "session": session},
                       headers={"cf-connecting-ip": ip})


def test_tool_loop(client, fake_llm):
    r = ask(client, "告警录像怎么生成的")
    assert r.status_code == 200
    ev = events(r)
    names = [e for e, _ in ev]
    assert names[0] == "meta" and names[-1] == "done"
    nodes = [d["node"] for e, d in ev if e == "node_end"]
    assert nodes == ["memory", "router", "qa_agent", "finish"]
    starts = [d for e, d in ev if e == "tool_start"]
    assert [s["name"] for s in starts] == ["search_docs", "highlight_projects"]
    assert starts[0]["input"]["query"]                      # arguments joined from several chunks
    assert ("ui_action", {"type": "highlight", "projects": ["vision"]}) in ev
    text = "".join(d["delta"] for e, d in ev if e == "text")
    assert "github.com/rfs151/ai-vision" in text            # link taken from the search result
    done = ev[-1][1]
    # router + two Q&A agent steps
    assert done["steps"] == 3 and done["hit"] == 3000 and done["miss"] == 600 and done["cost"] > 0
    assert done["intent"] == "qa" and done["path"] == nodes
    # second agent call got: system, user, assistant(tool_calls), 2 tool results
    roles = [m["role"] for m in fake_llm.calls[-1]["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "tool"]
    assert fake_llm.calls[-1]["thinking"] == {"type": "disabled"}


def test_session_keeps_history(client, fake_llm):
    sid = events(ask(client, "第一个问题", ip="2.2.2.2"))[0][1]["session"]
    ask(client, "第二个问题", session=sid, ip="2.2.2.2")
    users = [m["content"] for m in fake_llm.calls[-2]["messages"] if m["role"] == "user"]
    assert users == ["第一个问题", "第二个问题"]


def test_model_error_leaves_history_alone(client, fake_llm):
    sid = events(ask(client, "你好", ip="3.3.3.3"))[0][1]["session"]
    ev = events(ask(client, "boom", session=sid, ip="3.3.3.3"))
    assert ev[-1][0] == "error"
    ask(client, "再问一次", session=sid, ip="3.3.3.3")       # a new run after the failed one
    users = [m["content"] for m in fake_llm.calls[-2]["messages"] if m["role"] == "user"]
    assert users == ["你好", "再问一次"]


def test_jd_workflow(client, fake_llm):
    jd = "测试科技招聘 C++ 工程师。任职要求：1. 熟悉 Modbus 协议；2. 有 Rust 经验优先。" * 2
    ev = events(ask(client, jd, ip="5.5.5.5"))
    nodes = [d["node"] for e, d in ev if e == "node_end"]
    assert nodes == ["memory", "router", "jd_parse", "jd_evidence", "jd_score", "jd_report", "finish"]
    searches = [d for e, d in ev if e == "tool_start"]
    assert [s["input"]["query"] for s in searches] == ["Modbus", "Rust"]     # one search per requirement
    text = "".join(d["delta"] for e, d in ev if e == "text")
    assert "匹配度：67%" in text                 # must 3/3 x2 + plus 0/3 x1 -> 6/9
    assert "| 熟悉 Modbus 协议 | 必须 | ●●● |" in text and "| 有 Rust 经验 | 加分 | ○○○ |" in text
    assert "### 差距" in text
    assert ("memory", {"visitor": {"company": "测试科技", "role": "C++ 工程师"}}) in ev
    done = ev[-1][1]
    assert done["intent"] == "jd" and done["steps"] == 4     # router, parse, score, report


def test_contact_and_memory(client, fake_llm):
    sid = events(ask(client, "我是测试科技的 HR", ip="6.6.6.6"))[0][1]["session"]
    n = len(fake_llm.calls)
    ev = events(ask(client, "怎么联系你", session=sid, ip="6.6.6.6"))
    assert [d["node"] for e, d in ev if e == "node_end"][2] == "contact"
    assert len(fake_llm.calls) == n + 1          # only the router called the model
    assert "15301587132" in "".join(d["delta"] for e, d in ev if e == "text")
    # KEEP_MESSAGES=2: past 4 messages, the older ones are compressed into a summary
    ev = events(ask(client, "第三个问题", session=sid, ip="6.6.6.6"))
    assert not [d for e, d in ev if e == "memory"]
    ev = events(ask(client, "第四个问题", session=sid, ip="6.6.6.6"))
    assert ("memory", {"summary": "对方问过几个问题。"}) in ev
    qa = fake_llm.calls[-2]["messages"]          # the Q&A agent's first call
    mem = [m["content"] for m in qa if m["role"] == "system" and m["content"].startswith("<memory>")][0]
    assert "测试科技" in mem and "对方问过几个问题" in mem
    assert [m["content"] for m in qa if m["role"] == "user"] == ["第三个问题", "第四个问题"]


def test_rate_limit(client):
    codes = [ask(client, "问题", ip="9.9.9.9").status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


def test_too_long(client):
    assert ask(client, "长" * 5000, ip="4.4.4.4").status_code == 400


def test_status_tool_handles_down_service():
    import asyncio
    from app import tools
    result, summary, _ = asyncio.run(tools.run("get_project_status", "{}"))
    assert json.loads(result)["status"] == {"ai-vision": "暂不可用"}
    bad, s, _ = asyncio.run(tools.run("search_docs", "{not json"))
    assert "error" in json.loads(bad)


def test_loopback_ip_not_limited_per_ip(client):
    codes = [ask(client, "问题", ip="127.0.0.1").status_code for _ in range(6)]
    assert codes == [200] * 6          # RATE_PER_HOUR=5 applies to real IPs only
