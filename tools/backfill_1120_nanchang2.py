"""补录 11-20 16:10 前后发的两条南昌岗位（#28 赣江中药、#34 毛毛虫）。

这两条是 11-17 首次扫南昌时的存货，当时判「等 HR 答工时」、话术生成了却没发；
今天按新的四条模板补发，平台先自动发默认招呼，再补实质话术。

两条在库里本来就有（status=scored），所以只补消息 + 推状态，不新建。
幂等：按 (job_id, content) 查重。
"""

from __future__ import annotations

from datetime import datetime

from app.db import SessionLocal
from app.models import Job, Message
from app.pipeline import GREETED, log_out, set_stage

GREETING = "您好，看到您的招聘信息，我很感兴趣，希望可以进一步沟通。"

MSG_28 = (
    "您好，看到在招测试实习生，补充一下我的情况。我是智能科学与技术专业 2027 届本科，"
    "11 月 23 日可到岗，能连续做到 2027 年 2 月底。之前做过一条 LLM 测试用例生成流水线："
    "pydantic 做结构校验、失败回灌重写、覆盖度补轮、历史去重，把用例生成到校验串成一条链路。"
    "想先确认四点：一是实习期是否双休、每周到岗几天；二是能否远程或视频面试；"
    "三是学校还有毕业设计要求回校，实习期间能否请假或部分时间远程；"
    "四是实习结束能否开具实习证明？另外岗位写 3 个月、学校要求 40 个打卡工作日，"
    "我只能做到 2 月底，能否接受？"
)

MSG_34 = (
    "您好，看到在招软件开发实习生，补充一下我的情况。我是智能科学与技术专业 2027 届本科，"
    "11 月 23 日可到岗，能连续做到 2027 年 2 月底。之前独立做过一个求职流程管理工具，"
    "把岗位采集、话术生成、状态机、超时提醒串成一条链路，Python 写后端；"
    "也做过一个多智能体模拟面试系统（FastAPI + MySQL + 大模型 API，按岗位出题、"
    "多轮追问、五维评分），数据库主要用 MySQL。想先确认四点：一是实习期是否双休；"
    "二是能否远程或视频面试；三是学校还有毕业设计要求回校，实习期间能否请假或部分时间远程；"
    "四是实习结束能否开具实习证明、能否签三方？岗位写 3 个月、4 天/周，"
    "我只能做到 2 月底，能否接受？"
)

# job_id -> (实质话术, 发出时间)
TARGETS = {
    28: (MSG_28, "2026-11-20 16:10"),
    34: (MSG_34, "2026-11-20 16:14"),
}


def _record(db, job: Job, content: str, at: str) -> bool:
    dup = (
        db.query(Message)
        .filter(Message.job_id == job.id, Message.content == content)
        .count()
    )
    if dup:
        print(f"  #{job.id} 已存在：{content[:16]}…")
        return False
    msg = log_out(db, job, content)
    msg.sent_at = datetime.strptime(at, "%Y-%m-%d %H:%M")
    db.commit()
    return True


def main() -> None:
    db = SessionLocal()
    for job_id, (content, at) in TARGETS.items():
        job = db.get(Job, job_id)
        if job is None:
            print(f"#{job_id} 不存在，跳过")
            continue
        before = job.status
        # 默认招呼比实质话术早一分钟
        greet_at = datetime.strptime(at, "%Y-%m-%d %H:%M")
        g_at = (greet_at.replace(minute=greet_at.minute - 1)).strftime("%Y-%m-%d %H:%M")
        w1 = _record(db, job, GREETING, g_at)
        w2 = _record(db, job, content, at)
        ok = set_stage(db, job, GREETED)
        print(
            f"#{job.id} {job.company} {before} -> {job.status}"
            f" | 招呼{'写入' if w1 else '跳过'} 话术{'写入' if w2 else '跳过'}"
            f" | 状态{'推进' if ok else '未变'}"
        )
    db.close()


if __name__ == "__main__":
    main()
