"""统一催办自检：python selftest_chase.py

`app.chase` 会**替人说话**，所以它错起来比状态机更伤人：
话术里多一句没有出处的事实，九条消息就同时变成假话；
记账不幂等，同一句追问答发两遍，对方能看出来这是脚本干的。

四条断言，都是「不报错但会出事」的类型：
  1. 话术只用档案里的日期，不写死、不编造
  2. 名单只收「该催了」，不把「等你回」和「久无回音」混进来
  3. 记账幂等——跑两遍只留一条消息
  4. 记账之后时钟重置、旧提醒关掉，下一轮才重新算

用内存 SQLite 跑，不碰 data/jobpilot.db。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ["DB_URL"] = "sqlite://"
os.environ["DEMO"] = "1"

from app import chase, pipeline  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Alert, Job, Message, Preference, Profile  # noqa: E402

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  {GREEN}PASS{RESET} {name}")
    else:
        failed += 1
        print(f"  {RED}FAIL{RESET} {name}")
        if detail:
            print(f"       {DIM}{detail}{RESET}")


def mk(db, company: str, *, status: str = pipeline.GREETED, hours_ago: float = 20,
       direction: str = "out", content: str = "您好，看到您的招聘信息，我很感兴趣。") -> Job:
    job = Job(company=company, title="测试实习", city="南昌", status=status)
    db.add(job)
    db.commit()
    db.add(Message(job_id=job.id, direction=direction, content=content,
                   sent_at=datetime.now() - timedelta(hours=hours_ago)))
    db.commit()
    return job


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        p = Profile(name="自检", city="江西南昌", degree="本科", major="智能科学与技术",
                    school="某应用型本科院校", edu_end="2027-06")
        db.add(p)
        db.flush()
        db.add(Preference(profile_id=p.id, available_from="2026-11-23",
                          available_to="2027-02-28"))
        db.commit()

        print("\n=== 1. 话术只引档案里的日期 ===")
        text = chase.follow_up_text(db)
        check("到岗日来自 preferences（11月23日）", "11月23日" in text, text)
        check("结束日来自 preferences（2月28日）", "2月28日" in text, text)
        check("没有把日期写死在字面量里（改档案就跟着变）",
              "2026-11-23" not in text and "20271123" not in text)
        banned = ["保证", "一定能开", "包吃", "包住", "随时到岗", "长期"]
        hit = [w for w in banned if w in text]
        check("不含没有出处的承诺", not hit, str(hit))

        print("\n=== 2. 名单只收「该催了」 ===")
        due = mk(db, "该催的公司", hours_ago=20)
        fresh = mk(db, "刚发的公司", hours_ago=2)
        overdue = mk(db, "对方回过的公司", status=pipeline.REPLIED,
                     direction="in", hours_ago=30)
        cold = mk(db, "早就凉了的公司", hours_ago=pipeline.COLD_HOURS + 5)

        rows = chase.pending(db)
        ids = {r["job_id"] for r in rows}
        check("发了 20 小时的进名单", due.id in ids)
        check("刚发 2 小时的不进名单", fresh.id not in ids)
        check("对方回了你没回的不算「该催」", overdue.id not in ids)
        check("凉透的（该作罢）不混进来", cold.id not in ids)
        check("每条都带话术原文，方便直接发", all(r["text"] == text for r in rows))
        check("等待小时数按此刻重算", all(r["waited_hours"] >= 18 for r in rows))

        print("\n=== 3. 记账幂等 ===")
        before = db.query(Message).filter(Message.job_id == due.id).count()
        done, skipped = chase.mark_sent(db, [due.id])
        after = db.query(Message).filter(Message.job_id == due.id).count()
        check("第一次记账写入一条", done == 1 and after == before + 1, f"{before}→{after}")
        done2, skipped2 = chase.mark_sent(db, [due.id])
        after2 = db.query(Message).filter(Message.job_id == due.id).count()
        check("第二次记账不再写（同一句话不重复记）", done2 == 0 and after2 == after,
              f"{after}→{after2}, skipped={skipped2}")

        print("\n=== 4. 记账之后这一轮才算结束 ===")
        ids_now = {r["job_id"] for r in chase.pending(db)}
        check("催过的立刻退出名单（时钟重置）", due.id not in ids_now)
        alive = db.query(Alert).filter(Alert.job_id == due.id, Alert.ack_at.is_(None)).count()
        check("该岗位还挂着的提醒已关掉（不是 snooze）", alive == 0, f"剩 {alive} 条")

        print("\n=== 5. 输出能被人和脚本分别消费 ===")
        rendered = chase.render(rows)
        check("人读的输出带公司名", "该催的公司" in rendered, rendered[:80])
        check("人读的输出直接给出 --sent 命令", "--sent" in rendered)
        check("没有候选时不报错", "没有该催的岗位" in chase.render([]))
    finally:
        db.close()

    print("\n" + "=" * 46)
    print(f"通过 {passed} / 失败 {failed}")
    print("=" * 46)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
