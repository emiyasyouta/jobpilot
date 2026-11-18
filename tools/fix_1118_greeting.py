"""修正 #26 赫澜星那条招呼语的记录。

补录时只看到会话列表的截断预览（「您好，我是 2027 届智能科学与技…」），
误以为招呼语是定制的。打开会话一看，招呼是标准那句，
截断的其实是后面那条追问——两条开头都撞成一样的字，肉眼分不出来。
"""

from __future__ import annotations

from app.db import SessionLocal
from app.models import Job, Message

REAL_GREETING = "您好，看到您的招聘信息，我很感兴趣，希望可以进一步沟通。"
PLACEHOLDER_PREFIX = "（招呼语，BOSS 会话列表只显示前 12 字"


def main() -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, 26)
        if job is None:
            print("没有 #26")
            return
        rows = (
            db.query(Message)
            .filter(Message.job_id == 26, Message.direction == "out")
            .order_by(Message.id)
            .all()
        )
        if not rows:
            print("没有消息")
            return
        first = rows[0]
        if (first.content or "").startswith(PLACEHOLDER_PREFIX):
            first.content = REAL_GREETING
            db.commit()
            print("已把占位描述换成真实招呼语")
        else:
            print("首条不是占位描述，未改动：", (first.content or "")[:30])
        for m in rows:
            print(" -", m.sent_at.strftime("%m-%d %H:%M"), (m.content or "")[:34])
    finally:
        db.close()


if __name__ == "__main__":
    main()
