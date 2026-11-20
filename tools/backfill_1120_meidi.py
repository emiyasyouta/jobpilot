"""把 11-20 下午美的这条会话补进库。

这条是先手发的、后补话术的典型：
15:33 用户在 BOSS 上点「立即沟通」，平台自动发出默认招呼（不是我写的，没经过 pipeline）；
15:50 补发了一段实质话术（三条项目证据 + 三个确认点）。

两段都不在库里。不补的后果和前两次一样：明天早上提醒会照催一遍，
而对方其实昨天就收到了两轮消息——催得越勤，越像群发。

幂等：按 content 查重，重跑不会多发。
"""

from __future__ import annotations

from datetime import datetime

from app.db import SessionLocal
from app.models import Job, Message
from app.pipeline import GREETED, log_out, set_stage

GREETING = "您好，看到您的招聘信息，我很感兴趣，希望可以进一步沟通。"

FOLLOWUP = (
    "您好，我是 2027 届智能科学与技术专业本科在读，11 月 23 日可到岗。"
    "看到要求面试展示近期 AI 工具使用案例，我手上有三样现成的："
    "实习期间独立做的多智能体模拟面试系统（FastAPI + MySQL + 大模型 API，"
    "按岗位出题、多轮追问、五维评分）；一个 LLM 测试用例生成流水线"
    "（pydantic 结构校验、失败回灌重写、覆盖度补轮、历史去重）；"
    "还有一个求职流程管理工具，把岗位采集、话术生成、状态机、超时提醒做成系统，"
    "配了 7 套自检共 284 项。都是我自己从零写的，代码和演示需要的话我发您。"
    "想确认三点：一是岗位写连续 6 个月，我只能做到 2027 年 2 月底"
    "（学校要求 40 个打卡工作日），能否接受 3 个月左右；"
    "二是学校要求签三方实习协议，这边能签吗；三是我在南昌上学，是否支持远程面试。"
    "另外实习期间学校有毕业设计要回校，能否请假几天处理？"
)

# (内容, 发出时间)
MESSAGES = [
    (GREETING, "2026-11-20 15:33"),
    (FOLLOWUP, "2026-11-20 15:50"),
]

SPEC = {
    "company": "美的集团中国区域",
    "title": "AI应用实习生",
    "city": "佛山",
    "salary_min": 200,
    "salary_max": 250,
    "salary_unit": "day",
    "hr_name": "王女士",
    "hr_title": "招聘者",
}


def _record(db, job: Job, content: str, at: str) -> bool:
    dup = (
        db.query(Message)
        .filter(Message.job_id == job.id, Message.content == content)
        .count()
    )
    if dup:
        print(f"  #{job.id} 内容已存在，跳过：{content[:18]}…")
        return False
    msg = log_out(db, job, content)
    msg.sent_at = datetime.strptime(at, "%Y-%m-%d %H:%M")
    db.commit()
    return True


def main() -> None:
    db = SessionLocal()

    job = (
        db.query(Job)
        .filter(Job.company == SPEC["company"], Job.hr_name == SPEC["hr_name"])
        .first()
    )
    if job is None:
        job = Job(status="scored", source="manual", **SPEC)
        db.add(job)
        db.commit()
        print(f"新建 #{job.id} {job.company} {job.title}")

    job.raw_json = dict(job.raw_json or {}) | {
        "note_1120": "15:33 BOSS 手动点「立即沟通」，平台自动发默认招呼；"
        "15:50 补发实质话术，已问 3 个月可谈、三方协议、远程面试、请假回校做毕设。"
    }
    db.commit()

    for content, at in MESSAGES:
        wrote = _record(db, job, content, at)
        print(f"  {at} {'写入' if wrote else '已存在'}")

    before = job.status
    ok = set_stage(db, job, GREETED)
    print(f"状态 {before} -> {job.status} | {'推进' if ok else '未变'}")

    db.close()


if __name__ == "__main__":
    main()
