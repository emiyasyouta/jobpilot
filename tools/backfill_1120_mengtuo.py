"""建档并补录 11-20 16:15 的盟拓数字科技（苏州 · AI产品体验实习生）。

这条是用户自己在 BOSS 上点「立即沟通」发起的：平台先自动发默认招呼（16:15），
我 16:20 / 16:21 补发两段实质话术。三段都不在库里。

JD 存的是我从详情页读到的原文（个别句子在页面底部被截断，用「…」标出），
不补写、不润色——话术和复盘都要能对回原文。
"""

from __future__ import annotations

from datetime import datetime

from app.db import SessionLocal
from app.models import Job, Message
from app.pipeline import GREETED, log_out, set_stage

JD = """我们在做 Codealy —— 面向游戏与软件开发的 AI 编码助手（桌面端 Cowork、
VS Code / IntelliJ 插件、CLI、AI Canvas、3D / 图片 / 音频资产生成、Unity 编辑器联动）。
这份实习不是跑测试用例，而是像真实用户一样把 Codealy 用起来：用它做游戏、写工具、
做资产、整理项目，然后把「哪里用得不顺畅、哪里让人困惑、哪里真的出错了」整理清楚反馈给我们。

你将做什么
用 Codealy 完成真实任务：每周给自己定 1-2 个有明确产出的目标
- 用 Codealy 从零做一个能跑的小游戏原型（Unity / 网页均可）
- 用 AI 生成一套 3D 模型 / UI 图 / 音效，并接入项目
- 用它完成一段真实开发工作（写脚本、重构、查 bug、写文档）
- 用 AI Canvas 做一次完整的设计→生成→落地流程
记录使用过程：边做边记——哪一步卡住了、为什么卡、当时以为会发生什么、
实际发生了什么、等了多久、最后是怎么绕过去的
提交体验问题：把卡点分成两类分别反馈
- 缺陷：明确的功能错误（报错、崩溃、结果不对、数据丢失），给出可复现步骤与截图/日志
- 体验问题：没有报错但「不好用」（不知道下一步做什么、提示看不懂、
  等待时间不可预期、结果不符合预期、要求回答很多…）

我们希望你具备
必备
- 在校本科/研究生，能保证每周 ≥2-3 天、连续 3 个月以上（时间越长越好，
  因为很多问题要用久了才会暴露）
- 愿意真的把产品用起来：不是点两下就下结论，而是拿它完成一件自己在意的事
- 能说清「哪里不好用」：区分「我不会用」和「它没做好」，并能举出具体场景
- 有一定的动手能力：会一点 Unity、前端、Python 或任意脚本语言，
  能给自己定一个可完成的任务
加分
- 用过 Copilot / Cursor / Claude Code 等 AI 编程工具，能对比「别的工具这里怎么做」
- 自己做过游戏或小工具（哪怕是课程作业、Game Jam）
- 写过产品评测、使用笔记、教程或踩坑记录
- 对「AI 生成结果的质量」有自己的判断标准，而不只是「能用/不能用」
我们不要
- 不要求测试经验，不要求会写测试用例，也不做功能回归执行
- 不要求你是「找 bug 机器」——发现 3 个真实影响你干活的卡点，
  比提 50 条「建议优化」更有价值"""

GREETING = "您好，看到您的招聘信息，我很感兴趣，希望可以进一步沟通。"

MSG1 = (
    "您好，高先生。看到在招 AI 产品体验实习生，我是智能科学与技术专业 2027 届本科，"
    "11 月 23 日可到岗，每周能保证 3 天，能连续做到 2027 年 2 月底（约 14 周）。"
    "我平时就是用 AI 编码工具干活的人：自己写过一套 LLM 测试用例生成流水线，"
    "把「生成 → pydantic 结构校验 → 失败回灌重写 → 覆盖度补轮 → 历史去重」串成一条链路，"
    "这四层约束就是用来判断生成结果到底能不能用的；也独立做过一个多智能体模拟面试系统"
    "（FastAPI + MySQL + 大模型 API）。副产品是一堆踩坑记录——哪一步卡住、"
    "当时以为会发生什么、实际发生了什么，我习惯边做边记。"
)

MSG2 = (
    "「发现 3 个真实影响干活的卡点，比提 50 条建议优化更有价值」这句我很认。"
    "如果方便，我想先拿自己的项目在 Codealy 上跑一遍，把卡点整理成缺陷和体验两类，"
    "连同复现步骤一起给你们。另外确认四点：一是实习期是否双休、每周具体到岗几天；"
    "二是我在南昌上学，能否远程或部分远程（这条对我最关键）；"
    "三是学校还有毕业设计要求回校，实习期间能否请假；"
    "四是实习结束能否开具实习证明、能否签三方？"
)

MESSAGES = [
    (GREETING, "2026-11-20 16:15"),
    (MSG1, "2026-11-20 16:20"),
    (MSG2, "2026-11-20 16:21"),
]

SPEC = {
    "company": "盟拓数字科技",
    "title": "AI产品体验实习生",
    "city": "苏州",
    "salary_min": 120,
    "salary_max": 150,
    "salary_unit": "day",
    "days_per_week": 3,
    "months_req": 3,
    "edu_req": "本科",
    "hr_name": "高先生",
    "hr_title": "HR",
    "url": "https://www.zhipin.com/job_detail/40d86b9f57e14d170n162ty_GFtT.html",
    "jd_text": JD,
}


def _record(db, job: Job, content: str, at: str) -> bool:
    dup = (
        db.query(Message)
        .filter(Message.job_id == job.id, Message.content == content)
        .count()
    )
    if dup:
        print(f"  已存在：{content[:16]}…")
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
        print(f"新建 #{job.id} {job.company} · {job.title}")

    job.raw_json = dict(job.raw_json or {}) | {
        "note_1120": "16:15 用户手动点「立即沟通」；16:20-16:21 补发两段实质话术。"
        "产品 Codealy（AI 编码助手），公司 A 轮 500-999 人。"
        "关键待确认：能否远程/部分远程——用户在南昌，岗位在苏州，3 个月驻场成本高。",
        "key_risk": "苏州驻场 3 个月；3天/周 × 14 周 = 42 天，刚好压线凑满 40 个打卡工作日",
    }
    db.commit()

    for content, at in MESSAGES:
        wrote = _record(db, job, content, at)
        print(f"  {at} {'写入' if wrote else '跳过'}")

    before = job.status
    ok = set_stage(db, job, GREETED)
    print(f"状态 {before} -> {job.status} | {'推进' if ok else '未变'}")
    db.close()


if __name__ == "__main__":
    main()
