import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env_file():
    """.env next to the project (docker compose passes it as env vars; this is for running it directly)."""
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and not line.lstrip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip())


_env_file()


def _f(name, default):
    return float(os.getenv(name, default))


class Settings:
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("MODEL", "deepseek-flash")
    thinking = os.getenv("THINKING", "disabled")      # enabled | disabled
    max_tokens = int(os.getenv("MAX_TOKENS", "2000"))
    temperature = _f("TEMPERATURE", "0.3")              # 低一些：少发挥、少编造
    # 元 / 百万 token。默认按高峰价（偏保守），见 https://api-docs.deepseek.com/zh-cn/quick_start/pricing
    price_hit = _f("PRICE_CACHE_HIT", "0.04")
    price_miss = _f("PRICE_CACHE_MISS", "2")
    price_out = _f("PRICE_OUTPUT", "8")

    port = int(os.getenv("PORT", "8099"))
    data_dir = Path(os.getenv("DATA_DIR", ROOT / "data"))
    knowledge_dir = Path(os.getenv("KNOWLEDGE_DIR", ROOT / "knowledge"))
    model_dir = Path(os.getenv("MODEL_DIR", ROOT / "models"))
    embed_model = os.getenv("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")   # empty: keyword search only
    dev_site = os.getenv("DEV_SITE", "")               # serve this dir at / (local testing without Caddy)

    # limits
    max_input_chars = int(os.getenv("MAX_INPUT_CHARS", "3000"))   # a pasted JD can be long
    max_turns = int(os.getenv("MAX_TURNS", "20"))                 # per conversation
    max_steps = int(os.getenv("MAX_STEPS", "5"))                  # model calls per question (Q&A agent)
    keep_messages = int(os.getenv("KEEP_MESSAGES", "6"))          # memory: recent messages kept verbatim
    summarize_chars = int(os.getenv("SUMMARIZE_CHARS", "5000"))   # memory: compress older ones past this
    per_hour = int(os.getenv("RATE_PER_HOUR", "20"))              # questions per IP
    per_day = int(os.getenv("RATE_PER_DAY", "60"))
    daily_budget = _f("DAILY_BUDGET_CNY", "20")                   # whole site, then chat closes for the day
    site_per_hour = int(os.getenv("SITE_PER_HOUR", "200"))        # whole site, used when the visitor IP is unknown

    # live tools: same health checks as the gateway's /status/* (Caddyfile.local)
    status_urls = json.loads(os.getenv("STATUS_URLS", "{}")) or {
        "vision": "http://127.0.0.1:8090/api/system",
        "qt": "http://127.0.0.1:8080/healthz",
        "ems": "http://127.0.0.1:8095/api/health",
        "cam": "http://127.0.0.1:8098/api/health",
        "gvtest": "http://127.0.0.1:8097/healthz",
        "medlens": "http://127.0.0.1:8010/api/health",
        "scada": "http://127.0.0.1:8088/api/health",
    }
    vision_api = os.getenv("VISION_API", "http://127.0.0.1:8090")


settings = Settings()
