"""SQLite: one row per question (for reviewing what recruiters ask), also used for rate limits and
the daily budget, so limits survive a restart."""
import json
import sqlite3
import threading
import time

from app.config import settings

_lock = threading.Lock()
# Behind frp without PROXY protocol every visitor arrives from loopback: no per-IP limit then,
# only the site-wide hourly cap, the per-conversation turn limit and the daily budget.
UNKNOWN = {"127.0.0.1", "::1", "?"}
_db = None


def db():
    global _db
    if _db is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(settings.data_dir / "agent.db", check_same_thread=False)
        _db.execute("PRAGMA journal_mode=WAL")
        _db.execute("""CREATE TABLE IF NOT EXISTS turns (
            id INTEGER PRIMARY KEY, ts REAL, day TEXT, session TEXT, ip TEXT, question TEXT,
            answer TEXT, tools TEXT, hit INTEGER, miss INTEGER, out INTEGER, cost REAL, ms INTEGER,
            error TEXT)""")
        _db.execute("CREATE INDEX IF NOT EXISTS turns_ip ON turns(ip, ts)")
        _db.execute("CREATE INDEX IF NOT EXISTS turns_day ON turns(day)")
    return _db


def today():
    return time.strftime("%Y-%m-%d")


def check_limits(ip):
    """None if allowed, else the message to show."""
    now = time.time()
    with _lock:
        c = db()
        spent = c.execute("SELECT coalesce(sum(cost),0) FROM turns WHERE day=?", (today(),)).fetchone()[0]
        site = c.execute("SELECT count(*) FROM turns WHERE ts>?", (now - 3600,)).fetchone()[0]
        hour = day = 0
        if ip not in UNKNOWN:
            hour = c.execute("SELECT count(*) FROM turns WHERE ip=? AND ts>?", (ip, now - 3600)).fetchone()[0]
            day = c.execute("SELECT count(*) FROM turns WHERE ip=? AND ts>?", (ip, now - 86400)).fetchone()[0]
    if spent >= settings.daily_budget:
        return "今天的问答额度已经用完了，明天再来，或者直接通过简历上的电话、邮箱联系我。"
    if hour >= settings.per_hour or day >= settings.per_day or site >= settings.site_per_hour:
        return "提问有点频繁，请稍后再试，或者直接通过简历上的电话、邮箱联系我。"
    return None


def start(session, ip, question):
    with _lock:
        cur = db().execute("INSERT INTO turns(ts, day, session, ip, question) VALUES (?,?,?,?,?)",
                           (time.time(), today(), session, ip, question))
        db().commit()
        return cur.lastrowid


def finish(row, answer, done=None, error=None):
    d = done or {}
    with _lock:
        db().execute("UPDATE turns SET answer=?, tools=?, hit=?, miss=?, out=?, cost=?, ms=?, error=? WHERE id=?",
                     (answer, json.dumps({k: d.get(k) for k in ("intent", "path", "visitor", "trace")}, ensure_ascii=False),
                      d.get("hit"), d.get("miss"),
                      d.get("out"), d.get("cost", 0), d.get("ms"), error, row))
        db().commit()
