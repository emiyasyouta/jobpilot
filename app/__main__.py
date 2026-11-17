"""启动入口：python -m app

只做一件事：把 uvicorn 拉起来。所有配置都从 app.config 读，
所以 .env 改了不用改这里。
"""

from __future__ import annotations

import os

import uvicorn

from .config import DEMO_MODE, HOST, PORT


def main() -> None:
    print(f"JobPilot 启动中 · http://{HOST}:{PORT}" + ("  [DEMO 模式]" if DEMO_MODE else ""))
    # 改模板/代码要重启太烦，RELOAD=1 走 uvicorn 自带热重载（仅开发用）
    reload = os.getenv("RELOAD", "").strip().lower() in ("1", "true", "yes", "on")
    if reload:
        uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="info", reload=True)
    else:
        uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
