"""跟进提醒：python -m app.remind

为什么要有这个命令，而不是让网页自己弹窗：

    /inbox 是「你打开才看得到」的。而这一层真正会漏掉的场景是——你压根没打开它。
    发出去 40 条招呼，第三天忘了去翻，那不是界面问题。

所以把「现在轮到你」做成一条能进终端、能进计划任务的命令：

    python -m app.remind              # 有人等你回就打印出来
    python -m app.remind --json       # 给别的脚本消费
    python -m app.remind --quiet      # 没提醒就不出声，方便挂定时任务

Windows 上每小时查一次（弹一个小窗）：

    schtasks /create /tn JobPilot提醒 /tr "python -m app.remind" /sc hourly /mo 4

退出码：有待处理提醒返回 1，没有返回 0。这样外壳里的 `||` 判断能用上。
"""

from __future__ import annotations

import argparse
import json
import sys

from . import pipeline
from .db import SessionLocal, init_db


def collect() -> list[pipeline.Nudge]:
    init_db()
    db = SessionLocal()
    try:
        return pipeline.nudges(db)
    finally:
        db.close()


def render(rows: list[pipeline.Nudge]) -> str:
    if not rows:
        return "没有需要你动作的岗位。"
    lines = [f"现在轮到你：{len(rows)} 条", ""]
    for n in rows:
        stage = pipeline.STAGE_CN.get(n.job.status, n.job.status)
        lines.append(f"  [{n.kind_cn}] {n.job.company or '（未解析到公司名）'} · {n.job.title}")
        lines.append(f"          {n.reason}　（{stage}）")
        # 光说「等你回」没用，得让人一眼想起是哪件事。只截一句，多了终端里读不下去。
        if n.last_in is not None and (n.last_in.content or "").strip():
            quote = " ".join((n.last_in.content or "").split())
            if len(quote) > 48:
                quote = quote[:48] + "…"
            lines.append(f"          对方说：{quote}")
        lines.append("")
    lines.append("处理完去 /inbox 记一笔，或直接点「知道了」。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.remind", description="看看现在谁在等你")
    ap.add_argument("--json", action="store_true", help="输出 JSON，给脚本消费")
    ap.add_argument("--quiet", action="store_true", help="没提醒就什么都不打印（挂定时任务用）")
    args = ap.parse_args(argv)

    rows = collect()

    if args.json:
        print(json.dumps([
            {
                "alert_id": n.alert.id,
                "job_id": n.job.id,
                "company": n.job.company,
                "title": n.job.title,
                "stage": n.job.status,
                "kind": n.alert.kind,
                "kind_cn": n.kind_cn,
                "reason": n.reason,
            }
            for n in rows
        ], ensure_ascii=False, indent=2))
    elif rows or not args.quiet:
        print(render(rows))

    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
