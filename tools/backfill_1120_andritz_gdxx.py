"""补录 11-20 傍晚两条：安德里茨（佛山）、广东省信息工程（广州）。

两条都是用户自己在 BOSS 上点的「立即沟通」，平台先发默认招呼；
我 17:56-17:58 补发实质话术，各两段。四段都不在库里。

安德里茨这条有个教训：我第一次读详情页顶部那一行，把「5天/周」看成了「6天/周」，
差点按单休直接毙掉——用户当场质疑，重截一小块才看清是 5 天。
**小字必须放大看，别拿整屏截图猜。**
"""

from __future__ import annotations

from datetime import datetime

from app.db import SessionLocal
from app.models import Job, Message
from app.pipeline import GREETED, log_out, set_stage

GREETING = "您好，看到您的招聘信息，我很感兴趣，希望可以进一步沟通。"

# ---------------------------------------------------------------- 安德里茨

JD_ANDRITZ = """主要职责：
1. AI 工具研究与业务应用
- 调研、测试和评估生成式 AI 及自动化工具在浆线服务部门日常业务场景中的应用，
  包括信息整理、数据分析、报告生成、文档处理及知识检索等。
- 结合实际工作流程，识别重复性、耗时较高的工作环节，提出 AI 或自动化改善方案。
- 协助建立 AI 工具使用方法、Prompt 模板及最佳实践，提高团队工作效率。
2. 工作流程自动化与优化
- 协助梳理现有业务流程，分析流程中的效率痛点及可自动化环节。
- 运用 Microsoft Power Automate、Power Apps、Copilot、Python 或其他 AI/自动化工具，
  搭建简单的自动化工作流或应用。
- 对已实施的流程改善方案进行测试、优化，并跟踪实际使用效果。
3. 数据处理与分析支持
- 运用 AI、Excel、Power BI、Python 等工具，对业务数据进行整理、分析及可视化。
- 探索 AI 在数据分析、信息提取、趋势识别和报告自动生成等场景中的应用。
- 根据业务需求协助开发简单的数据分析工具或 Dashboard。
（页面下半部分未读完，任职要求未记录）"""

MSG_A1 = (
    "您好，徐女士。看到在招 AI 与流程改善实习生，我是 2027 届智能科学与技术专业本科，"
    "11 月 23 日可到岗。JD 里「调研评估生成式 AI 工具」「把重复、耗时的环节自动化」这两件事"
    "我一直在做：自己写过一套 LLM 测试用例生成流水线，把「生成 → pydantic 结构校验 → "
    "失败回灌重写 → 覆盖度补轮 → 历史去重」串成一条链路，靠四层约束判断生成出来的东西"
    "到底能不能用；也独立做过一个多智能体模拟面试系统（FastAPI + MySQL + 大模型 API）。"
    "另外做过一个求职流程管理工具，把岗位采集、话术生成、状态机、超时提醒做成系统，"
    "等于把自己的一条业务流程整套自动化了一遍。"
)

MSG_A2 = (
    "想先确认三点：一是岗位标注每周 5 天，周末是否固定双休；二是标注 7 个月，"
    "学校要求 40 个打卡工作日，我只能做到 2027 年 2 月底，能否按 3 个多月来；"
    "三是我在南昌上学，能否远程或视频面试，实习结束能否开具实习证明、能否签三方？"
)

# ---------------------------------------------------------------- 广东省信息工程

JD_GDXX = """岗位职责
1、探索AI技术在公司业务场景中的应用，参与"AI+业务"需求调研与分析；
2、尝试将AI技术融入方案设计、软件开发、实施部署、运维等业务流程，输出可落地原型或工具；
3、跟踪AI前沿动态，参与AI工具选型与测试评估，输出学习分享；
4、配合项目团队完成方案文档、数据整理等基础工作，识别可被AI提效的业务环节。
任职要求
1、2027届应届毕业生，人工智能、计算机相关专业；
2、理解AI基本原理（机器学习、大语言模型等），有大模型应用、Prompt实践、RAG/Agent等经历者优先；
3、学习能力强、自驱力高，愿意主动钻研新技术并尝试与实际业务结合；
4、逻辑清晰、表达顺畅，责任心强，能适应政务信息化项目对严谨性和规范性的要求"""

MSG_G1 = (
    "您好，梁女士。看到在招 AI 实习生，我是 2027 届智能科学与技术专业本科，11 月 23 日可到岗。"
    "JD 写「有大模型应用、Prompt 实践、RAG/Agent 经历者优先」，我手上有三样："
    "一个多智能体模拟面试系统（FastAPI + MySQL + 大模型 API，按岗位出题、多轮追问、五维评分）；"
    "一个 LLM 测试用例生成流水线（pydantic 结构校验、失败回灌重写、覆盖度补轮、历史去重）；"
    "还有一个求职流程管理工具，把岗位采集、话术生成、状态机、超时提醒串成一条链路。"
)

MSG_G2 = (
    "我理解这个岗的核心是「把 AI 融进别人的业务流程、输出能落地的原型」，"
    "前面那个求职工具就是我自己拿这套方法做了一件事——只不过流程是我自己的。"
    "另外确认四点：一是实习期是否双休、每周到岗几天、大概多长；"
    "二是我在南昌上学，能否远程或视频面试；三是学校还有毕业设计要求回校，实习期间能否请假；"
    "四是实习结束能否开具实习证明、能否签三方？"
)

# ---------------------------------------------------------------- 建档规格

SPECS = [
    {
        "spec": {
            "company": "安德里茨",
            "title": "AI与流程改善实习生",
            "city": "佛山",
            "salary_min": 120,
            "salary_max": 150,
            "salary_unit": "day",
            "days_per_week": 5,
            "months_req": 7,
            "edu_req": "本科",
            "hr_name": "徐女士",
            "hr_title": "招聘专员",
            "url": "https://www.zhipin.com/job_detail/78060b74f9491a0c0nxz0t60FlVT.html",
            "jd_text": JD_ANDRITZ,
        },
        "messages": [
            (GREETING, "2026-11-20 16:23"),
            (MSG_A1, "2026-11-20 17:57"),
            (MSG_A2, "2026-11-20 17:58"),
        ],
        "note": "奥地利工业设备集团（浆线服务部门）。5天/周、7个月、120-150/天、1000-9999人。"
                "第一次误读成 6天/周 差点毙掉，实际是 5 天。核心待确认：7 个月能否缩到 3 个多月。",
    },
    {
        "spec": {
            "company": "广东省信息工程",
            "title": "ai实习生",
            "city": "广州",
            "salary_min": 3000,
            "salary_max": 5000,
            "salary_unit": "month",
            "edu_req": "本科",
            "hr_name": "梁女士",
            "hr_title": "人资专员",
            "url": "https://www.zhipin.com/job_detail/184a1896e7540d5e0nN52dy9FlFR.html",
            "jd_text": JD_GDXX,
        },
        "messages": [
            (GREETING, "2026-11-20 16:22"),
            (MSG_G1, "2026-11-20 17:56"),
            (MSG_G2, "2026-11-20 17:57"),
        ],
        "note": "政务信息化方向，100-499 人，未融资。JD 明写「2027届应届毕业生」+「RAG/Agent 经历者优先」，"
                "匹配度高。JD 未写每周天数与时长，需在对话里问。",
    },
]


def _record(db, job: Job, content: str, at: str) -> bool:
    dup = (
        db.query(Message)
        .filter(Message.job_id == job.id, Message.content == content)
        .count()
    )
    if dup:
        print(f"    已存在：{content[:16]}…")
        return False
    msg = log_out(db, job, content)
    msg.sent_at = datetime.strptime(at, "%Y-%m-%d %H:%M")
    db.commit()
    return True


def main() -> None:
    db = SessionLocal()
    for item in SPECS:
        spec = item["spec"]
        job = (
            db.query(Job)
            .filter(Job.company == spec["company"], Job.hr_name == spec["hr_name"])
            .first()
        )
        if job is None:
            job = Job(status="scored", source="manual", **spec)
            db.add(job)
            db.commit()
            print(f"新建 #{job.id} {job.company} · {job.title}")
        else:
            print(f"已存在 #{job.id} {job.company}")

        job.raw_json = dict(job.raw_json or {}) | {"note_1120": item["note"]}
        db.commit()

        for content, at in item["messages"]:
            wrote = _record(db, job, content, at)
            print(f"    {at} {'写入' if wrote else '跳过'}")

        before = job.status
        ok = set_stage(db, job, GREETED)
        print(f"    状态 {before} -> {job.status} | {'推进' if ok else '未变'}")

    db.close()


if __name__ == "__main__":
    main()
