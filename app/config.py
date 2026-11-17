"""全局配置。

所有可变项都从这里出，业务代码不许直接读 os.environ。
默认值按「本机 LLM 中转 + SQLite」配置，换机器只需改 .env。
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DEMO_DIR = BASE_DIR / "demo"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# 静态资源的版本号 = style.css 的 mtime。不加这个，浏览器会按「启发式缓存」
# 直接复用旧 CSS（StaticFiles 只发 ETag/Last-Modified，不发 Cache-Control），
# 改完样式刷新看不到变化，只能强制刷新——本地工具里这种坑不值得留。
CSS_VERSION = str(int((STATIC_DIR / "style.css").stat().st_mtime))

for _d in (DATA_DIR, OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_dotenv(path: Path) -> None:
    """把 .env 读进 os.environ。

    规则只有一条：**已有的进程环境变量优先，绝不覆盖**。这样「临时在命令行
    export 一个值」永远压得住 .env，调试时不会出现「我明明设了却没生效」。

    之前这里没有这个函数，但 .env.example 和 README 都写着「复制成 .env 即可」，
    于是复制完什么也不会发生——配置读的全是默认值。工具里这种「文档承诺了、
    代码没做」的坑最难查，因为没有任何报错。
    """
    if not path.is_file():
        return
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        # 行内注释只在「空格 + #」时才算，避免把 key/url 里的 # 当成注释
        if " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE_DIR / ".env")


def _flag(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ---- LLM（OpenAI 兼容） ----
# 默认值仍是本机中转，但 57321 只挂了 Responses 协议、没有 Chat Completions，
# 实际要用请在 .env 里指到厂商的 /v1（见 .env.example）。
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:57321/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "agnes-3.0-flash")
LLM_API_KEY = os.getenv("LLM_API_KEY", "local-relay")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "90"))
LLM_MAX_RETRY = int(os.getenv("LLM_MAX_RETRY", "3"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))
# 是否走系统代理（HTTP_PROXY/NO_PROXY）。默认 False：本机有代理时会劫持
# 127.0.0.1 的请求并回 502，而报错完全指不到真正原因。改接外网 API 且必须
# 经过代理时，在 .env 里打开 LLM_TRUST_ENV=1，不要再去改 llm.py。
LLM_TRUST_ENV = _flag("LLM_TRUST_ENV", False)

# ---- 运行模式 ----
# DEMO=1 时：使用 demo/ 内置数据，不连真实网站，简历导出到 output/demo
DEMO_MODE = _flag("DEMO", False)

# ---- 数据库 ----
DB_URL = os.getenv("DB_URL", f"sqlite:///{(DATA_DIR / 'jobpilot.db').as_posix()}")

# ---- 服务 ----
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

# ---- 业务参数 ----
# 已读未回超过这么多小时，提示生成跟进话术
FOLLOWUP_AFTER_HOURS = int(os.getenv("FOLLOWUP_AFTER_HOURS", "18"))

# BOSS 城市码。新城市在这里加，代码里不许出现裸数字。
CITY_CODES: dict[str, str] = {
    "全国": "100010000",
    "南昌": "101240100",
    "赣州": "101240700",
    "九江": "101240200",
    "上饶": "101240300",
    "长沙": "101250100",
    "武汉": "101200100",
    "杭州": "101210100",
    "广州": "101280100",
    "深圳": "101280600",
    "苏州": "101190400",
    "合肥": "101220100",
    "东莞": "101281600",
    "南通": "101190500",
}

# 抓取节流：串行 + 随机延时，避免触发风控
CRAWL_DELAY_RANGE = (2.0, 5.0)
CRAWL_MAX_PAGES_PER_RUN = 30
# 浏览器调试端口。用 --remote-debugging-port 启动的那个 Edge/Chrome 的地址
CRAWL_CDP_URL = os.getenv("CRAWL_CDP", "http://127.0.0.1:9222")
