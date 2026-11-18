"""把 11-18 晚 BOSS 上真实发出去的动作补进库。

背景：这两条招呼+追问是在浏览器里手动点出去的，pipeline 没有经过
（招呼语是在 BOSS 页面直接生成发送的，没走 greeter 的入库路径）。
不补的话明早提醒会继续喊「该发招呼了」，而实际上人家 18:2x 就收到了。
"""

from __future__ import annotations

import json
from datetime import datetime

from app.db import SessionLocal
from app.models import Job, Message
from app.pipeline import GREETED, log_out

# (job_id, 招呼时间, 招呼语, 追问时间, 追问内容)
SENT = [
    (
        21,
        "2026-11-18 18:22",
        "您好，看到您的招聘信息，我很感兴趣，希望可以进一步沟通。",
        "2026-11-18 18:47",
        "您好，我是 2027 届智能科学与技术专业本科在读，看到贵司这个测试岗。"
        "想确认两点：一是学校这边要求签三方实习协议，这个岗位可以签吗？"
        "二是实习大概几个月、每周到岗几天？我 11 月下旬可以到岗。",
    ),
    (
        26,
        "2026-11-18 18:21",
        "您好，看到您的招聘信息，我很感兴趣，希望可以进一步沟通。",
        "2026-11-18 18:21",
        "您好，我是 2027 届智能科学与技术专业本科在读，看到贵司这个测试岗。"
        "想确认两点：一是学校这边要求签三方实习协议，这个岗位可以签吗？"
        "二是实习大概几个月、每周到岗几天？我 11 月下旬可以到岗。",
    ),
    (
        32,
        "2026-11-18 18:51",
        "您好，看到您的招聘信息，我很感兴趣，希望可以进一步沟通。",
        "2026-11-18 18:52",
        "您好，我是 2027 届智能科学与技术专业本科在读。想确认两点："
        "一是学校这边要求签三方实习协议，这个岗位可以签吗？"
        "二是实习大概几个月、每周到岗几天？我 11 月下旬可以到岗。",
    ),
]


def main() -> None:
    db = SessionLocal()
    try:
        for job_id, g_at, g_text, q_at, q_text in SENT:
            job = db.get(Job, job_id)
            if job is None:
                print(f"#{job_id} 不存在，跳过")
                continue

            # 已经记过就不重复写（幂等：重跑不会造出双份消息）
            exists = (
                db.query(Message)
                .filter(Message.job_id == job_id, Message.content == q_text)
                .count()
            )
            if exists:
                print(f"#{job_id} 追问已存在，只补状态与标记")
            else:
                for at, text in ((g_at, g_text), (q_at, q_text)):
                    msg = log_out(db, job, text)
                    msg.sent_at = datetime.strptime(at, "%Y-%m-%d %H:%M")
                db.commit()
                print(f"#{job_id} 两条消息已入库")

            job.status = GREETED
            # raw_json 是 JSON 列，取出来已经是 dict，不是字符串
            raw = dict(job.raw_json or {})
            raw["three_party_status"] = "已问，待回复"
            raw["sent_1118"] = f"11-18 晚 BOSS 已发招呼+三方追问（{q_at[11:]}），等回音"
            job.raw_json = raw
            db.commit()
            print(f"#{job_id} {job.company} → greeted")
    finally:
        db.close()


if __name__ == "__main__":
    main()
