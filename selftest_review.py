"""人工确认自检：python selftest_review.py

这一层要防的不是「规则判错了」——那防不住，规则就是会错，所以才需要人。
要防的是**人工判断本身失效**，四种失效方式都不会报错，只会安静地错：

1. 改判后分数没变 —— override 没接进打分链路，按钮点了等于没点
2. 改判有效，但「重新评估」一次就丢 —— override 存在 Match 里而不是 reviews 里
3. 改判一次，之后这条岗位的所有新问题都被放过 —— override 按职位而非按规则 id 生效
4. 人工否决挡不住规则放行 —— manual block 没进阻断判断
5. 队列把不该问的也问了 —— 阈值失效，退回成全量列表

第 3 条最隐蔽：它只在「规则升级」或「JD 补录」之后才暴露，而那正是这个项目
每天都在做的事。

用内存 SQLite 跑，不碰 data/jobpilot.db。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 必须在 import app.* 之前改环境：config.DB_URL 是模块级求值的
os.environ["DB_URL"] = "sqlite://"
os.environ["DEMO"] = "1"

from app import review, scoring  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Job, Preference, Profile, ProfileItem  # noqa: E402

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

JD_GOOD = (
    "岗位职责：\n"
    "1. 负责测试用例设计与 Python 自动化脚本编写；\n"
    "2. 参与大模型接口联调与需求文档评审。\n"
    "任职要求：\n"
    "1. 本科在读，能连续实习 3 个月；\n"
    "2. 工作时间：双休。"
)

JD_TRAIN = JD_GOOD + "\n3. 入职后先学后付，课程结束推荐就业。"

JD_SALES = (
    "岗位职责：\n"
    "1. 负责客户拜访与客户资源维护；\n"
    "2. 完成月度销售目标。\n"
    "任职要求：\n"
    "1. 学历不限。\n"
    "工作时间：双休。"
)

JD_WEEKEND = JD_GOOD + "\n工作时间：6天/周。"

# 没写工时：规则给不出结论，只能人去问
JD_GAP = (
    "岗位职责：\n"
    "1. 负责测试用例设计与 Python 自动化脚本编写；\n"
    "2. 参与大模型接口联调。\n"
    "任职要求：\n"
    "1. 本科在读。"
)


def seed(db) -> None:
    p = Profile(name="自检", city="江西南昌", degree="本科", major="智能科学与技术")
    db.add(p)
    db.flush()
    db.add(ProfileItem(
        profile_id=p.id, type="project", title="测试用例生成工具", org="个人项目",
        verified=True, displayable=True,
        content_json={"keywords": ["测试用例", "用例", "Python", "自动化", "大模型", "需求文档"]},
    ))
    db.add(Preference(
        profile_id=p.id, available_from="2026-11-23", available_to="2027-02-28",
        remote_available_to="2027-05-31", must_weekend_off=True,
        must_remote_interview=True, must_certificate=True,
    ))
    db.commit()


def add_job(db, title: str, jd: str, company: str = "自检公司") -> Job:
    job = Job(company=company, title=title, city="南昌", jd_text=jd,
              salary_min=150, salary_max=200, salary_unit="day", status="new")
    db.add(job)
    db.commit()
    scoring.evaluate_job(db, job)
    return job


def queue_of(db) -> list[review.ReviewItem]:
    items, _ = scoring.review_queue(db, limit=50)
    return items


def find(items: list[review.ReviewItem], job: Job) -> review.ReviewItem | None:
    return next((it for it in items if it.job.id == job.id), None)


# ---------------------------------------------------------------- 主流程


def main() -> int:
    init_db()
    db = SessionLocal()
    seed(db)

    print(f"\n=== 1. 谁该进队列 ===")
    good = add_job(db, "AI 测试开发实习生", JD_GOOD)
    train = add_job(db, "AI 工程师（接受实习生）", JD_TRAIN)
    sales = add_job(db, "管培生", JD_SALES)
    weekend = add_job(db, "软件测试实习生", JD_WEEKEND)
    gap = add_job(db, "算法实习生", JD_GAP)

    items = queue_of(db)
    ids = [it.job.id for it in items]
    it_train = find(items, train)
    it_weekend = find(items, weekend)
    it_gap = find(items, gap)
    check("方向对口 + 无风险命中：不进队列", good.id not in ids,
          f"命中规则 {[f.rule_id for f in scoring.findings_for(db, good)]}")
    check("方向不对口的销售岗：不进队列", sales.id not in ids,
          "方向分低于 30 时把岗位塞进队列只是制造待办")
    check("疑似培训机构 + 方向对口：进队列，bucket=overturn",
          bool(it_train) and it_train.bucket == "overturn")
    check("事实型硬阻断：进队列，bucket=fact",
          bool(it_weekend) and it_weekend.bucket == "fact")
    check("JD 没写工时：进队列，bucket=gap",
          bool(it_gap) and it_gap.bucket == "gap")

    print(f"\n=== 2. 排序按误判代价，不按分数 ===")
    order = [it.bucket for it in items]
    check("「可能误杀」排在「事实型阻断」之前",
          order.index("overturn") < order.index("fact") if "fact" in order else True,
          f"顺序 {order}")

    print(f"\n=== 3. 改判可投（pass）===")
    before = train.latest_match
    before_dir = before.dimension_scores_json["direction"]["score"]
    check("改判前被高风险规则封顶 40", abs(before.score - 40.0) < 0.01, f"实际 {before.score}")

    scoring.review_apply(db, train, review.PASS, note="自检：先学后付是公司的培训补贴政策",
                         bucket="overturn")
    after = train.latest_match
    check("改判后分数上升（override 真的接进了打分链路）", after.score > before.score,
          f"{before.score} → {after.score}")
    check("改判后不再阻断", not after.blocked, after.blocked_reason)
    check("方向分不因人工改判而变（真实性归人，相关性归规则）",
          abs(after.dimension_scores_json["direction"]["score"] - before_dir) < 0.01,
          f"{before_dir} → {after.dimension_scores_json['direction']['score']}")
    check("理由里写明被推翻的规则", "已人工推翻" in after.reason_text, after.reason_text)
    check("已处理的岗位移出队列", train.id not in [it.job.id for it in queue_of(db)])

    print(f"\n=== 4. 重算不丢人工判断 ===")
    s1 = train.latest_match.score
    for _ in range(2):
        scoring.evaluate_job(db, train)
    s2 = train.latest_match.score
    check("连点两次「重新评估」，分数与结论都不变", abs(s1 - s2) < 0.01, f"{s1} → {s2}")

    print(f"\n=== 5. override 按规则 id 生效，不发免死金牌 ===")
    train.jd_text = JD_TRAIN + "\n工作时间：6天/周，每周单休。"
    db.commit()
    scoring.evaluate_job(db, train)
    m = train.latest_match
    check("改判后 JD 里新出现的「单休」照样拦住", m.blocked,
          f"score={m.score} blocked={m.blocked} reason={m.blocked_reason}")
    check("驳回理由指向新命中的规则，而不是旧的那条",
          "工作时间" in m.blocked_reason or "单休" in m.blocked_reason, m.blocked_reason)
    train.jd_text = JD_TRAIN
    db.commit()
    scoring.evaluate_job(db, train)
    check("把 JD 改回去，恢复可投", not train.latest_match.blocked)

    print(f"\n=== 6. 人工否决（block）在规则之上 ===")
    scoring.review_apply(db, good, review.BLOCK, note="自检：面试时对方说不签实习协议", bucket="")
    g = good.latest_match
    check("规则放行的岗位也能被人否决", g.blocked, f"blocked={g.blocked}")
    check("理由标明是人工判断", "人工判定不投" in g.blocked_reason, g.blocked_reason)
    check("状态同步为 blocked", good.status == "blocked", good.status)
    check("否决后不生成招呼语评分虚高", g.score == 0.0, str(g.score))

    print(f"\n=== 7. 等回复（wait）移出队列，但不改判定 ===")
    wait_before = gap.latest_match
    scoring.review_apply(db, gap, review.WAIT, note="自检：话术已发，等 HR 回工时", bucket="gap")
    wait_after = gap.latest_match
    check("wait 一分数都不动（等待不是证据）", abs(wait_after.score - wait_before.score) < 0.01,
          f"{wait_before.score} → {wait_after.score}")
    check("wait 不等于人工放行：blocked 状态与规则一致",
          bool(wait_after.blocked_reason) == bool(wait_before.blocked_reason),
          f"{wait_before.blocked_reason} → {wait_after.blocked_reason}")
    check("wait 后移出待确认队列",
          gap.id not in [it.job.id for it in queue_of(db)])
    check("wait 落库为独立决策，不与 ack 混同",
          review.latest(db, gap.id).decision == review.WAIT,
          str(review.latest(db, gap.id).decision))
    check("wait 的 note 能翻到（否则等的是什么就忘了）",
          "等 HR" in (review.latest(db, gap.id).note or ""),
          review.latest(db, gap.id).note)
    # 撤销后必须回到队列：工时还是没问到，不能因为「挂过」就当解决了
    scoring.review_apply(db, gap, review.RESET)
    check("撤销 wait 后重新回到队列",
          gap.id in [it.job.id for it in queue_of(db)])

    print(f"\n=== 8. 认可规则（ack）不改动任何判定 ===")
    gap_before = gap.latest_match
    scoring.review_apply(db, gap, review.ACK, note="自检：电话确认双休", bucket="gap")
    gap_after = gap.latest_match
    check("ack 不改分数", abs(gap_after.score - gap_before.score) < 0.01,
          f"{gap_before.score} → {gap_after.score}")
    check("ack 后移出队列（同一条不该反复问）",
          gap.id not in [it.job.id for it in queue_of(db)])

    print(f"\n=== 9. 撤销（reset）回到纯规则 ===")
    scoring.review_apply(db, train, review.RESET)
    check("撤销后分数回到规则算的 40", abs(train.latest_match.score - 40.0) < 0.01,
          str(train.latest_match.score))
    check("撤销后重新回到待确认队列",
          train.id in [it.job.id for it in queue_of(db)])
    scoring.review_apply(db, good, review.RESET)
    check("撤销人工否决后状态退回 scored", good.status == "scored", good.status)
    check("撤销后分数恢复", good.latest_match.score > 0, str(good.latest_match.score))

    print(f"\n=== 10. 留痕 ===")
    rows = db.query(review.Review).filter(review.Review.job_id == train.id).order_by(review.Review.id).all()
    check("历次判断都留在库里（含被撤销的）", len(rows) >= 2, f"{[r.decision for r in rows]}")
    check("pass 记录里存了被推翻的规则 id",
          any(r.decision == review.PASS and r.rule_ids_json for r in rows),
          str([(r.decision, r.rule_ids_json) for r in rows]))
    check("每条判断都有 bucket 或 note 可回溯（reset 只是撤销，不需要理由）",
          all(r.bucket or r.note for r in rows if r.decision != review.RESET),
          str([(r.decision, r.bucket, r.note) for r in rows]))

    print(f"\n=== 11. 空备注不能冲掉已写的理由 ===")
    scoring.review_apply(db, gap, review.WAIT, note="第一次写的理由：等 HR 回工时", bucket="gap")
    scoring.review_apply(db, gap, review.WAIT, note="", bucket="gap")
    check("重复同一判断、备注留空 -> 继承上一条理由",
          "第一次写的理由" in (review.latest(db, gap.id).note or ""),
          repr(review.latest(db, gap.id).note))
    scoring.review_apply(db, gap, review.WAIT, note="改主意了，改成等 offer", bucket="gap")
    check("填了新备注 -> 用新的，不沿用旧的",
          review.latest(db, gap.id).note.startswith("改主意了"),
          repr(review.latest(db, gap.id).note))
    scoring.review_apply(db, gap, review.ACK, note="认可，不再追问", bucket="gap")
    scoring.review_apply(db, gap, review.WAIT, note="", bucket="gap")
    check("换了判断类型 -> 不继承上一条的备注（否则等于张冠李戴）",
          review.latest(db, gap.id).note == "",
          repr(review.latest(db, gap.id).note))

    db.close()
    print("\n" + "=" * 46)
    color = GREEN if failed == 0 else RED
    print(f"{color}通过 {passed} / 失败 {failed}{RESET}")
    print("=" * 46 + "\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
