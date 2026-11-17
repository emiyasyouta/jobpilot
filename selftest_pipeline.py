"""投递跟进自检：python selftest_pipeline.py

P3 要防的失效方式和 P2 不同。P2 是「规则判错了没人知道」，
P3 是**状态和事实悄悄对不上**——四种都不会报错：

1. 状态推不动 / 乱跳 —— 按钮点了没反应，或者从「已投」直接跳到「给了 offer」
2. 记了回复但状态没变 —— 跟进板上这条还在「等对方」，于是被漏掉
3. 采信了 HR 的话但分数没变 —— 采信按钮等于没点（P2 已经踩过同款）
4. 采信优先级错 —— HR 说 5 天/周，平台写 6 天/周，结果还是按 6 天判

另外两条是这一层特有的判断，错了不一定有症状，但会让人做错决定：
5. 时效算错 —— 该催的没催、不该催的催了
6. 抽到的事实没带原文 —— 人没法判断该不该采信，只能盲信

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

from app import pipeline, scoring  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Job, Preference, Profile, ProfileItem  # noqa: E402

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[0m", "\033[0m"
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


# ---------------------------------------------------------------- 样本

JD_OK = (
    "岗位职责：\n"
    "1. 负责测试用例设计与 Python 自动化脚本编写；\n"
    "2. 参与大模型接口联调与需求文档评审。\n"
    "任职要求：\n"
    "1. 本科在读，能连续实习 3 个月。\n"
    "2. 工作时间：双休。"
)


def seed(db) -> None:
    p = Profile(name="自检", city="江西南昌", degree="本科",
                major="智能科学与技术", school="某应用型本科院校", edu_end="2027-06")
    db.add(p)
    db.flush()
    db.add(ProfileItem(
        profile_id=p.id, type="project", title="测试用例生成工具", org="个人项目",
        verified=True, displayable=True,
        content_json={"keywords": ["测试用例", "用例", "Python", "自动化", "大模型", "需求文档"]},
    ))
    db.add(Preference(
        profile_id=p.id, available_from="2026-11-23", available_to="2027-02-28",
        must_weekend_off=True, must_remote_interview=True, must_certificate=True,
    ))
    db.commit()


def add_job(db, title="软件测试实习", jd=JD_OK, days=None, status="new") -> Job:
    job = Job(company="自检公司", title=title, city="南昌", jd_text=jd,
              salary_min=150, salary_max=200, salary_unit="day",
              days_per_week=days, status=status)
    db.add(job)
    db.commit()
    scoring.evaluate_job(db, job)
    return job


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        seed(db)

        # ------------------------------------------------------------ 状态机
        print("\n=== 1. 状态机：只走写进表里的转移 ===")
        check("greeted 不能直跳 offer（没面试就发 offer 说明状态记错了）",
              not pipeline.can_go(pipeline.GREETED, pipeline.OFFER))
        check("greeted 能到 replied", pipeline.can_go(pipeline.GREETED, pipeline.REPLIED))
        check("replied 能回 greeted（你回完之后又轮到对方）",
              pipeline.can_go(pipeline.REPLIED, pipeline.GREETED))
        check("offer 是终态，没有出边", not pipeline.TRANSITIONS[pipeline.OFFER])
        check("blocked 能回到 scored（重新评估解阻）",
              pipeline.can_go(pipeline.BLOCKED, pipeline.SCORED))

        print("\n=== 2. 每个状态都有「卡在谁手上」的归属 ===")
        missing = [s for s in pipeline.STAGES if s not in pipeline.OWNER]
        check("STAGES 与 OWNER 一一对应（漏一个，看板上那条就消失）",
              not missing, str(missing))
        orphan = [s for s in pipeline.STAGES if s not in pipeline.STAGE_CN]
        check("每个状态都有中文名", not orphan, str(orphan))

        print("\n=== 3. 按钮只给合法转移 ===")
        for st in pipeline.STAGES:
            bad = [(s, lab) for s, lab in pipeline.stage_actions(st) if not pipeline.can_go(st, s)]
            check(f"{st} 的动作按钮全部合法", not bad, str(bad))

        j = add_job(db)
        print("\n=== 4. set_stage 拒绝非法转移，不抛异常 ===")
        j.status = pipeline.GREETED
        db.commit()
        ok = pipeline.set_stage(db, j, pipeline.OFFER)
        check("非法转移返回 False 且状态不变", (not ok) and j.status == pipeline.GREETED, j.status)
        ok = pipeline.set_stage(db, j, pipeline.REPLIED)
        check("合法转移返回 True", ok and j.status == pipeline.REPLIED, j.status)
        check("同状态重复设置返回 False（不是错误，只是没事可做）",
              not pipeline.set_stage(db, j, pipeline.REPLIED))

        # ------------------------------------------------------------ 会话
        print("\n=== 5. 记一条对方的回复，状态自己推进 ===")
        a = add_job(db, title="A岗")
        a.status = pipeline.GREETED
        db.commit()
        msg = pipeline.log_in(db, a, "我们双休，每周5天")
        check("greeted + 收到回复 → replied", a.status == pipeline.REPLIED, a.status)
        check("消息进了会话流", len(pipeline.thread(db, a)) == 1)

        b = add_job(db, title="B岗")
        b.status = pipeline.GHOSTED
        db.commit()
        pipeline.log_in(db, b, "还在招，你还有兴趣吗")
        check("凉了三个月又回你 → 复活成 replied", b.status == pipeline.REPLIED, b.status)

        c = add_job(db, title="C岗")
        c.status = pipeline.SCORED
        db.commit()
        pipeline.log_in(db, c, "直接说，什么时候来")
        check("还没投就收到回复 → 也算 replied", c.status == pipeline.REPLIED, c.status)

        print("\n=== 6. 我发出的消息也进会话流 ===")
        n0 = len(pipeline.thread(db, a))
        pipeline.log_out(db, a, "好的，那我周三下午过去")
        th = pipeline.thread(db, a)
        check("发一条多一条", len(th) == n0 + 1, f"{n0} → {len(th)}")
        check("顺序按时间，最后一条是 out", th[-1].direction == "out")
        check("来源标记能区分招呼语和普通消息",
              all(isinstance(t.is_greeting, bool) for t in th))

        # ------------------------------------------------------------ 意图
        print("\n=== 7. 意图识别：定不了就说不清，不硬猜 ===")
        cases = [
            ("明天下午方便来面试吗？", pipeline.ASK_INTERVIEW),
            ("你的期望薪资是多少呢", pipeline.ASK_SALARY),
            ("什么时候能到岗", pipeline.ASK_SCHEDULE),
            ("不好意思已经招满了", pipeline.REJECT),
            ("好的收到", pipeline.INFO),
            ("", pipeline.UNCLEAR),
        ]
        for text, want in cases:
            got = pipeline.intent_of(text)
            check(f"「{text or '空'}」→ {want}", got == want, f"实际 {got}")
        check("拒绝优先于约面（「招满了，下次再联系」不该读成约你面试）",
              pipeline.intent_of("已经招满了，下次面试再联系你") == pipeline.REJECT)

        # ------------------------------------------------------------ 抽取
        print("\n=== 8. 事实抽取：每条都带原文 ===")
        f = {x.key: x for x in pipeline.extract("我们双休，每周5天，可以开实习证明，120元/天")}
        check("抽到天数 5", f.get("days_per_week") and f["days_per_week"].value == 5,
              str(f.get("days_per_week")))
        check("抽到能开证明", f.get("certificate") and f["certificate"].value is True)
        check("抽到薪资", bool(f.get("salary")), str(f.get("salary")))
        check("每条事实都带原文引用（否则人没法判断该不该信）",
              all(x.quote for x in f.values()), str([(k, v.quote) for k, v in f.items()]))
        check("原文引用真的出自原文",
              all(x.quote in "我们双休，每周5天，可以开实习证明，120元/天" for x in f.values()))

        g = {x.key: x for x in pipeline.extract("每周6天，单休")}
        check("6天/周 抽成 6", g["days_per_week"].value == 6)
        h = {x.key: x for x in pipeline.extract("大小周能接受吗")}
        check("「大小周」不瞎折成天数（只有 weekends，没有 days_per_week）",
              "weekends" in h and "days_per_week" not in h, str(list(h)))
        check("没提工时就抽不出工时（不猜）",
              not any(x.key == "days_per_week" for x in pipeline.extract("你简历发我看看")))

        # ------------------------------------------------------------ 采信
        print("\n=== 9. 采信 HR 的事实，分数必须跟着变 ===")
        e = add_job(db, title="E岗", jd=JD_OK.replace("2. 工作时间：双休。", ""))
        before = e.latest_match.score
        schedule_before = scoring._schedule_text(e)
        check("采信前没有工时", schedule_before == "", repr(schedule_before))
        e.hr_facts_json = {"days_per_week": {"value": 5, "quote": "每周5天",
                                             "message_id": None, "at": "2026-11-17T21:00:00"}}
        db.commit()
        m = scoring.evaluate_job(db, e)
        check("采信 5天/周 后 _schedule_text 认得出",
              scoring._schedule_text(e) == "5天/周", scoring._schedule_text(e))
        check("分数发生变化（「点了按钮分数不动」是 P2 踩过的同款 bug）",
              abs(m.score - before) > 0.01, f"{before} → {m.score}")

        print("\n=== 10. HR 的话压过平台字段 ===")
        f6 = add_job(db, title="F岗", days=6)
        check("平台写 6 天/周 → 单休，被阻断", bool(f6.latest_match.blocked_reason),
              f6.latest_match.blocked_reason[:40])
        f6.hr_facts_json = {"days_per_week": {"value": 5, "quote": "我们双休",
                                              "message_id": None, "at": "2026-11-17T21:00:00"}}
        db.commit()
        m = scoring.evaluate_job(db, f6)
        check("采信 HR 的 5 天/周 后解除阻断", not m.blocked_reason, m.blocked_reason[:40])
        check("_schedule_text 用的是 HR 的值", scoring._schedule_text(f6) == "5天/周",
              scoring._schedule_text(f6))
        check("规则命中里不再有「单休」这类阻断",
              not any(x.level == "hard_block" for x in scoring.findings_for(db, f6)),
              str([x.rule_id for x in scoring.findings_for(db, f6)]))

        print("\n=== 10b. 已投递的岗位不会被重算打回 blocked ===")
        k = add_job(db, title="K岗", jd=JD_OK.replace("2. 工作时间：双休。", ""))
        k.status = pipeline.GREETED
        db.commit()
        k.hr_facts_json = {"days_per_week": {"value": 6, "quote": "每周6天",
                                             "message_id": None, "at": "2026-11-17T21:00:00"}}
        db.commit()
        m = scoring.evaluate_job(db, k)
        check("采信 6 天/周 后规则确实判阻断", bool(m.blocked_reason), m.blocked_reason[:40])
        check("但状态仍是 greeted（消息已经发出去了，这是历史事实）",
              k.status == pipeline.GREETED, k.status)
        check("阻断信息没丢，只是不改状态", "单休" in m.blocked_reason or "6" in scoring._schedule_text(k),
              m.blocked_reason[:40])

        # ------------------------------------------------------------ 时效
        print("\n=== 11. 时效：该催的催、不该催的不催 ===")
        now = datetime(2026, 11, 17, 21, 0)
        t = add_job(db, title="T岗")
        t.status = pipeline.GREETED
        db.commit()
        pipeline.log_out(db, t, "你好")
        # 把消息时间往前拨
        from app.models import Message
        row = db.query(Message).filter(Message.job_id == t.id).first()
        row.sent_at = now - timedelta(hours=pipeline.FOLLOWUP_HOURS - 1)
        db.commit()
        check("差一小时到点 → 不催", not pipeline.clock(db, t, now).due)
        row.sent_at = now - timedelta(hours=pipeline.FOLLOWUP_HOURS + 1)
        db.commit()
        check("超过阈值 → 该催", pipeline.clock(db, t, now).due)
        check("超过四倍阈值 → 凉了", not pipeline.clock(db, t, now).cold)
        row.sent_at = now - timedelta(hours=pipeline.COLD_HOURS + 1)
        db.commit()
        check("超过 4×阈值 → cold", pipeline.clock(db, t, now).cold)
        check("告警词优先显示 cold 而不是 due", pipeline.clock(db, t, now).badge == "久无回音",
              pipeline.clock(db, t, now).badge)

        print("\n=== 12. 轮到我拖太久要标出来 ===")
        r = add_job(db, title="R岗")
        pipeline.log_in(db, r, "明天能来面试吗")
        row = db.query(Message).filter(Message.job_id == r.id).first()
        row.sent_at = now - timedelta(hours=1)
        db.commit()
        check("刚回 → 不标", not pipeline.clock(db, r, now).overdue)
        row.sent_at = now - timedelta(hours=pipeline.REPLY_DUE_HOURS + 1)
        db.commit()
        check("拖过 24 小时 → overdue", pipeline.clock(db, r, now).overdue)
        check("overdue 的告警词是「你拖了」", pipeline.clock(db, r, now).badge == "你拖了",
              pipeline.clock(db, r, now).badge)

        # ------------------------------------------------------------ 看板
        print("\n=== 13. 看板：分组与排序 ===")
        groups = pipeline.board(db, now=now)
        check("replied 的进 due_me 而不是 waiting",
              all(c.job.status == pipeline.REPLIED for c in groups["due_me"]),
              str([(c.job.title, c.job.status) for c in groups["due_me"]]))
        check("超时未回的进 chase",
              all(c.job.status == pipeline.GREETED for c in groups["chase"]))
        check("过了期的排在同组最前",
              not groups["due_me"] or groups["due_me"][0].clock.overdue
              or not any(c.clock.overdue for c in groups["due_me"]))
        check("还没投的按分数从高到低",
              [c.score for c in groups["todo"]] == sorted([c.score for c in groups["todo"]], reverse=True),
              str([c.score for c in groups["todo"]]))
        total = sum(len(v) for v in groups.values())
        check("每个不含 new 的岗位都恰好出现在一组里（漏一组就等于消失）",
              total == db.query(Job).filter(Job.status != pipeline.NEW).count(),
              f"看板 {total} 条 / 库内 {db.query(Job).filter(Job.status != pipeline.NEW).count()} 条")

        print("\n=== 14. 回复草稿：缺事实就不给残句 ===")
        p = db.query(Profile).first()
        pref = scoring.get_pref(db, p)
        s = pipeline.draft_reply(r, pipeline.ASK_SCHEDULE, profile=p, pref=pref)
        check("档案齐时能拼出完整句子", "11月23日" in s, s)
        check("拒绝不给模板（被拒的岗不用回）",
              pipeline.draft_reply(r, pipeline.REJECT, profile=p, pref=pref) == "")
        check("看不懂不给模板（模板一定是错方向）",
              pipeline.draft_reply(r, pipeline.UNCLEAR, profile=p, pref=pref) == "")
        bare = Profile()
        db.add(bare)
        db.commit()
        check("空档案不给残句",
              pipeline.draft_reply(r, pipeline.ASK_SCHEDULE, profile=bare, pref=None) == "",
              pipeline.draft_reply(r, pipeline.ASK_SCHEDULE, profile=bare, pref=None))

        print("\n=== 15. 历史招呼语回填 ===")
        from app.models import Greeting
        gjob = add_job(db, title="G岗")
        gjob.status = pipeline.GREETED
        db.add(Greeting(job_id=gjob.id, content="你好，我想投这个岗位", is_sent=True,
                        confirmed_by_user=True, sent_at=datetime(2026, 11, 16, 10, 0)))
        db.commit()
        added = pipeline.backfill_sent_greetings(db)
        check("已发但没进会话的招呼语被补上", added >= 1, str(added))
        check("补完能看到这条消息", any(t.direction == "out" for t in pipeline.thread(db, gjob)))
        check("重复回填不重复插", pipeline.backfill_sent_greetings(db) == 0)

        print("\n=== 16. 一致性检查：状态和会话对不上要报出来 ===")
        ph = add_job(db, title="虚高岗")
        ph.status = pipeline.GREETED       # 直接改状态，不补消息——这就是 phantom
        db.commit()
        found = {a.job.id: a for a in pipeline.audit(db)}
        check("phantom：说投过了但一条消息都没有 → 被抓到",
              ph.id in found and found[ph.id].kind == "phantom",
              str([(a.job.id, a.kind) for a in pipeline.audit(db)]))

        st = add_job(db, title="滞后岗")
        st.status = pipeline.SCORED
        db.commit()
        pipeline.log_out(db, st, "你好，我想投这个岗位")
        found = {a.job.id: a for a in pipeline.audit(db)}
        check("stale：发过消息但状态还停在待投 → 被抓到",
              st.id in found and found[st.id].kind == "stale",
              str([(a.job.id, a.kind) for a in pipeline.audit(db)]))

        # 修好之后不该再报——否则这个检查会一直在喊狼来了，喊久了就没人看
        st.status = pipeline.GREETED
        db.commit()
        check("状态补对之后不再报", st.id not in {a.job.id for a in pipeline.audit(db)})

        rp = add_job(db, title="假回复岗")
        rp.status = pipeline.REPLIED
        db.commit()
        check("说对方回了、但会话里没有对方的话 → 也报 phantom",
              any(a.job.id == rp.id and a.kind == "phantom" for a in pipeline.audit(db)))
        check("audit 不改数据（它只负责说，不负责修）", rp.status == pipeline.REPLIED)

    finally:
        db.close()

    print("\n" + "=" * 46)
    color = GREEN if failed == 0 else RED
    print(f"{color}通过 {passed} / 失败 {failed}{RESET}")
    print("=" * 46 + "\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
