"""统一催一波：python -m app.chase

`app.remind` 说的是「现在谁在等你」，这个命令管的是**接下来那一下**：
把「该催了」的那几条捞出来，配一句统一的跟进话术，发完再把账记回库里。

为什么不直接在网页上挨个点：

    九条岗位，九个会话，同一句话。人手点是九次复制粘贴，
    真正的成本不在打字，在于**点完就忘了记账**——消息发出去了，库里还停在
    「等回音（18 小时前）」，明天提醒照催，而你会以为是系统坏了。

所以拆成三步，每一步都能单独跑：

    python -m app.chase                 # 看名单和话术（不改任何东西）
    python -m app.chase --json          # 给自动化脚本消费
    python -m app.chase --sent 36 37    # 发完了，记一笔

`--sent` 做的事：把这句话记成一条 out 消息（时钟自然重置，18 小时后才会再催），
并把该岗位还挂着的提醒关掉——新一轮开始了，上一轮的「该催了」不该继续响。

退出码：有待催的返回 1，没有返回 0（和 `app.remind` 保持一致）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from . import pipeline
from .db import SessionLocal, init_db
from .models import Job, Message

# 一句话里只能有档案里 verified 的事实。到岗日与结束日从 preferences 读，
# 不写死在文本里——这两个日期变了，九条话术不会跟着变成假话。
FOLLOW_UP = (
    "您好，之前给您发的消息不知有没有看到。我{start}就能到岗，"
    "能连续做到{end}。岗位如果还在招，想跟您确认一下是否方便安排一次沟通；"
    "如果不合适也请直接告诉我，我好安排后面的时间。"
)

_START_FALLBACK = "11月23日"
_END_FALLBACK = "明年2月底"


def _cn_date(raw: str) -> str:
    """'2026-11-23' → '11 月 23 日'。跨年也只说月日，句子才读得顺。"""
    try:
        d = datetime.strptime((raw or "").strip(), "%Y-%m-%d")
    except ValueError:
        return ""
    return f"{d.month}月{d.day}日"


def follow_up_text(db) -> str:
    from .models import Preference

    pref = db.query(Preference).first()
    start = _START_FALLBACK
    end = _END_FALLBACK
    if pref is not None:
        start = _cn_date(pref.available_from) or start
        end = _cn_date(pref.available_to) or end
    return FOLLOW_UP.format(start=start, end=end)


def pending(db, *, now: datetime | None = None) -> list[dict]:
    """现在还该催的，最急的在前。判断依据永远是此刻的时间账。"""
    now = now or datetime.now()
    text = follow_up_text(db)
    rows: list[dict] = []
    for n in pipeline.nudges(db, now=now):
        if n.alert.kind != pipeline.CHASE:
            continue
        ran = db.query(Message).filter(
            Message.job_id == n.job.id,
            Message.direction == "out",
            Message.content == text,
        ).count()
        rows.append({
            "job_id": n.job.id,
            "company": n.job.company or "",
            "title": n.job.title or "",
            "hr_name": n.job.hr_name or "",
            "waited_hours": round(n.clock.my_last or 0, 1),
            "already_chased": ran,
            "text": text,
        })
    return rows


def render(rows: list[dict]) -> str:
    if not rows:
        return "没有该催的岗位。"
    lines = [f"可以催一轮：{len(rows)} 条", ""]
    for r in rows:
        tag = f"（已催过 {r['already_chased']} 次）" if r["already_chased"] else ""
        lines.append(f"  #{r['job_id']} {r['company']} · {r['title']}{tag}")
        lines.append(f"       发出 {r['waited_hours']:.0f} 小时没回音")
    lines.append("")
    lines.append("统一话术：")
    lines.append(f"  {rows[0]['text']}")
    lines.append("")
    lines.append("发完执行：python -m app.chase --sent " + " ".join(str(r["job_id"]) for r in rows))
    return "\n".join(lines)


def mark_sent(db, job_ids: list[int], *, now: datetime | None = None) -> tuple[int, list[str]]:
    now = now or datetime.now()
    text = follow_up_text(db)
    done, skipped = 0, []
    for job_id in job_ids:
        job = db.get(Job, job_id)
        if job is None:
            skipped.append(f"#{job_id} 不存在")
            continue
        # 幂等：最后一条 out 已经是这句了，说明这一轮催过了，不重复记
        last_out = (
            db.query(Message)
            .filter(Message.job_id == job_id, Message.direction == "out")
            .order_by(Message.sent_at.desc(), Message.id.desc())
            .first()
        )
        if last_out is not None and (last_out.content or "").strip() == text:
            skipped.append(f"#{job_id} 已记过")
            continue
        msg = pipeline.log_out(db, job, text)
        msg.sent_at = now
        db.commit()
        # 新一轮开始，上一轮的提醒作废（不是 snooze——那个情形已经不存在了）
        pipeline.ack_for_job(db, job_id, now=now)
        done += 1
    return done, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.chase", description="把该催的统一催一轮")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--text", action="store_true", help="只打统一话术，方便复制")
    ap.add_argument("--sent", nargs="+", type=int, metavar="ID", help="这些岗位已经发出去了，记账")
    args = ap.parse_args(argv)

    init_db()
    db = SessionLocal()
    try:
        if args.sent:
            done, skipped = mark_sent(db, args.sent)
            print(f"已记账 {done} 条")
            for s in skipped:
                print(f"  跳过：{s}")
            return 0

        rows = pending(db)
        if args.text:
            print(follow_up_text(db))
            return 1 if rows else 0
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 1 if rows else 0
        print(render(rows))
        return 1 if rows else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
