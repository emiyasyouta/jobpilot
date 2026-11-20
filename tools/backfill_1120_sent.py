"""把 11-20 上午在 BOSS 上手动发出的四条招呼补进库。

背景：这四条是用户自己在 BOSS 上点「立即沟通」发的，BOSS 自动带出默认招呼语，
没有经过 pipeline，所以库里查不到。不补的后果和 11-18 那次一样：
今晚提醒会喊「该发招呼了」，而人家上午就收到了。

其中 #22 华勤（蒋兰）本来就在库里的 scored，这里只补消息并推进状态；
另外三条是新岗位，需要新建记录。
"""

from __future__ import annotations

from datetime import datetime

from app.db import SessionLocal
from app.models import Job, Message
from app.pipeline import GREETED, log_out, set_stage

GREETING = "您好，看到您的招聘信息，我很感兴趣，希望可以进一步沟通。"

# 库里已有，只需补消息 + 推状态
EXISTING = [(22, "2026-11-20 11:57")]

# 库里没有，新建
NEW = [
    {
        "company": "腾讯",
        "title": "开放世界游戏测试实习生",
        "city": "武汉",
        "salary_min": 150,
        "salary_max": 200,
        "salary_unit": "day",
        "hr_name": "于先生",
        "hr_title": "招聘者",
        "at": "2026-11-20 12:16",
    },
    {
        "company": "优数云旅",
        "title": "软件测试实习生（AI智能体方向）",
        "city": "杭州",
        "salary_min": 150,
        "salary_max": 200,
        "salary_unit": "day",
        "hr_name": "王女士",
        "hr_title": "HRM",
        "at": "2026-11-20 12:11",
    },
    {
        "company": "华勤技术股份有限公司",
        "title": "游戏测试实习生",
        "city": "无锡",
        "salary_min": 100,
        "salary_max": 120,
        "salary_unit": "day",
        "hr_name": "王先生",
        "hr_title": "测试组长",
        "at": "2026-11-20 12:01",
    },
]


def _record(db, job: Job, at: str) -> bool:
    """记一条 out 消息。同一时刻同一条内容已存在就跳过（幂等）。"""
    ts = datetime.strptime(at, "%Y-%m-%d %H:%M")
    dup = (
        db.query(Message)
        .filter(Message.job_id == job.id, Message.content == GREETING)
        .count()
    )
    if dup:
        print(f"  #{job.id} {job.company} 招呼已存在，跳过")
        return False
    msg = log_out(db, job, GREETING)
    msg.sent_at = ts
    db.commit()
    return True


def main() -> None:
    db = SessionLocal()

    print("== 已有岗位：补消息 ==")
    for job_id, at in EXISTING:
        job = db.get(Job, job_id)
        if job is None:
            print(f"  #{job_id} 不存在，跳过")
            continue
        before = job.status
        wrote = _record(db, job, at)
        ok = set_stage(db, job, GREETED)
        print(
            f"  #{job.id} {job.company} {before} -> {job.status}"
            f" | 消息{'写入' if wrote else '已存在'} | 状态{'推进' if ok else '未变'}"
        )

    print("== 新岗位：建档 + 补消息 ==")
    for spec in NEW:
        at = spec.pop("at")
        exists = (
            db.query(Job)
            .filter(
                Job.company == spec["company"],
                Job.hr_name == spec["hr_name"],
                Job.title == spec["title"],
            )
            .first()
        )
        if exists:
            print(f"  {spec['company']} {spec['hr_name']} 已存在（#{exists.id}），跳过建档")
            _record(db, exists, at)
            continue

        job = Job(status="scored", source="manual", **spec)
        job.raw_json = {
            "note_1120": "11-20 上午 BOSS 手动点「立即沟通」，系统自动发默认招呼语；"
            "尚未追问三方协议与双休情况。"
        }
        db.add(job)
        db.commit()
        wrote = _record(db, job, at)
        ok = set_stage(db, job, GREETED)
        print(
            f"  #{job.id} {job.company} {job.title} 新建"
            f" | 消息{'写入' if wrote else '已存在'} | 状态{'推进' if ok else '未变'}"
        )

    db.close()


if __name__ == "__main__":
    main()
