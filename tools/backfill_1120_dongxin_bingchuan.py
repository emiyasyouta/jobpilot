"""补录 11-20 傍晚两条：东信和平（珠海）、深圳冰川网络（武汉）。

两条都是用户自己在 BOSS 上点的「立即沟通」，平台先发默认招呼；
我 18:45-18:48 补发实质话术（东信三段、冰川四段）。全部不在库里，不补明天会重复催。

共同点：5天/周、3个月、时长天然吻合——这是这轮唯一两条**不用求人缩短实习期**的岗位。
东信和平的技术含量低（调研+文档+测试问题闭环），冰川是 H5 试玩广告开发（要 AI 开发经验）。
"""

from __future__ import annotations

from datetime import datetime

from app.db import SessionLocal
from app.models import Job, Message
from app.pipeline import GREETED, log_out, set_stage

GREETING = "您好，看到您的招聘信息，我很感兴趣，希望可以进一步沟通。"

# ---------------------------------------------------------------- 东信和平

JD_DONGXIN = """岗位职责：
1. 协助团队开展企业AI应用场景调研、需求收集与整理，梳理各业务线智能化改造需求，
   协助输出调研小结、需求清单等基础材料。
2. 配合完成AI应用原型测试、功能试用、问题记录与汇总，跟进测试问题整改闭环，
   协助验证AI工具落地效果。
3. 负责项目相关文档的整理、排版、归档，包括方案初稿、操作手册、项目周报、
   落地总结等基础资料的编撰工作。
4. 协助团队进行AI行业案例、前沿应用、竞品方案的搜集与整理，
   为公司AI场景创新落地提供参考素材。
5. 配合完成AI应用落地过程中的辅助性事务，包括数据整理、流程对接、内部试用反馈收集等，
   保障项目高效推进。
任职要求：
1. 本科及以上在读，人工智能、计算机、软件工程等相关专业；
2. 了解基础AI应用逻辑，愿意深耕AI业务场景落地相关工作；
3. 具备良好的文字功底，逻辑清晰，擅长资料整理、文档编写和信息汇总，
   能够高效完成各类基础性工作。
4. 细心严谨、责任心强，执行力佳，善于沟通对接，能够积极配合团队完成各项工作任务；
5. 有AI项目实习、场景调研、产品助理相关经验者优先，
   熟练使用Office、思维导图等办公工具者优先。
（福利标签：专业培训、周末双休、免费班车、定期体检、生日福利）"""

MSG_D1 = (
    "乔先生您好。看到在招 AI 应用实习生，岗位写 3 个月、5 天/周，我这边正好对得上："
    "11 月 23 日可到岗，能连续做到 2027 年 2 月底，时长不用谈。"
)

MSG_D2 = (
    "JD 前两条是场景调研和原型测试，这两件事我一直在做。"
    "我自己从零写过一套求职流程管理工具，把「找岗→打招呼→记录状态→超时提醒」"
    "整条流程拆成四个环节做成系统，每个环节定规则、留记录，配了 7 套自检共 284 项；"
    "也写过一套 LLM 测试用例生成流水线，把「生成→结构校验→失败回灌重写→覆盖度补轮」"
    "串起来，问题记录、重跑、验证是闭环的。"
    "文档类的工作我也习惯做，项目 README 上万字，每天的工作日志都留着。"
)

MSG_D3 = (
    "想确认三点：一是我在南昌上学，这个岗是否必须到珠海现场，能否远程或视频面试；"
    "二是学校还有毕业设计要求回校，实习期间能否请假几天；"
    "三是实习结束能否开具实习证明、能否签三方？"
)

# ---------------------------------------------------------------- 深圳冰川网络

JD_BINGCHUAN = """岗位职责：
1、参与游戏H5试玩广告制作，协助完善SOP流程；
2、根据需求进行H5游戏的设计与开发，进行游戏逻辑、交互、性能方面的优化；
任职要求：
1、具备一定的抗压能力；
2、具备良好的逻辑思维能力、问题排查能力；
3、具备优秀的团队协作意识与沟通能力，愿意主动学习新技术；
4、熟练使用至少一种AI工具，并有AI做开发经验，可附作品集；
5、有丰厚的游戏体验史，熟悉至少2种游戏品类，可附带游戏经验
工作地址：武汉市洪山区铭丰大厦A座23楼
工作时间：09:00-18:30，周末双休"""

MSG_B1 = (
    "许女士您好。看到在招 AI 试玩，我是 2027 届智能科学与技术专业本科，11 月 23 日可到岗。"
    "岗位写 3 个月、5 天/周，我这边完全对得上，能连续做到 2027 年 2 月底。"
)

MSG_B2 = (
    "JD 第 4 条要「熟练使用至少一种 AI 工具、有 AI 做开发经验」，我日常就是靠这个干活的："
    "自己从零写过一套求职流程管理工具（Python 后端，岗位采集→话术生成→状态机→超时提醒"
    "一条链路，配 7 套自检共 284 项）、一套 LLM 测试用例生成流水线（pydantic 结构校验、"
    "失败回灌重写、覆盖度补轮）、一个多智能体模拟面试系统（FastAPI + MySQL + 大模型 API）。"
    "作品集和演示需要的话我发您。"
)

MSG_B3 = (
    "第 5 条游戏体验：王者荣耀排位百星，三角洲行动一直在玩，MOBA 和 FPS 两类都算熟。"
    "实话说 H5 游戏开发我没直接做过。但试玩广告本质是「短、快、能跑起来」，"
    "正好是 AI 辅助开发最占便宜的地方——如果方便，我可以先拆一个你们现成的试玩广告，"
    "用 AI 重做一个同类 demo 给您看，做得好再谈，做不好也不耽误您时间。"
)

MSG_B4 = (
    "想确认几点：一是 09:00–18:30 是否含午休；"
    "二是岗位是否需要到武汉驻场（我在南昌上学，能否远程或视频面试）；"
    "三是学校还有毕业设计要求回校，实习期间能否请假几天；"
    "四是实习结束能否开具实习证明、能否签三方？"
)

# ---------------------------------------------------------------- 建档规格

SPECS = [
    {
        "spec": {
            "company": "东信和平",
            "title": "AI应用实习生",
            "city": "珠海",
            "salary_min": 150,
            "salary_max": 200,
            "salary_unit": "day",
            "days_per_week": 5,
            "months_req": 3,
            "edu_req": "本科",
            "hr_name": "乔先生",
            "hr_title": "HR",
            "url": "https://www.zhipin.com/job_detail/7e0d85ee5007e3ba0nJy2N24F1RT.html",
            "jd_text": JD_DONGXIN,
        },
        "messages": [
            (GREETING, "2026-11-20 18:25"),
            (MSG_D1, "2026-11-20 18:45"),
            (MSG_D2, "2026-11-20 18:45"),
            (MSG_D3, "2026-11-20 18:46"),
        ],
        "note": "已上市、1000-9999人、通信/网络设备。5天/周、3个月、本科、150-200/天，"
                "福利标签明写「周末双休」。性质是「AI 落地推进助理」——调研、需求整理、"
                "原型测试问题闭环、文档编撰、案例搜集，技术含量低但能力要求全对得上。"
                "唯一代价：珠海是这轮最远的城市。待确认：能否远程面试、请假回校、证明/三方。",
    },
    {
        "spec": {
            "company": "深圳冰川网络",
            "title": "AI试玩",
            "city": "武汉",
            "salary_min": 120,
            "salary_max": 170,
            "salary_unit": "day",
            "days_per_week": 5,
            "months_req": 3,
            "edu_req": "大专",
            "hr_name": "许女士",
            "hr_title": "hr",
            "url": "https://www.zhipin.com/job_detail/0299aa6a36d3eacd0nB63t-7EVdU.html",
            "jd_text": JD_BINGCHUAN,
        },
        "messages": [
            (GREETING, "2026-11-20 18:02"),
            (MSG_B1, "2026-11-20 18:47"),
            (MSG_B2, "2026-11-20 18:48"),
            (MSG_B3, "2026-11-20 18:48"),
            (MSG_B4, "2026-11-20 18:48"),
        ],
        "note": "已上市游戏公司、1000-9999人。做 H5 试玩广告（playable ad，买量素材）。"
                "09:00-18:30 + JD 明写「周末双休」，5天/周 × 8 周即满 40 个打卡工作日，"
                "打卡风险全池最低。短板：H5 游戏开发用户没做过，话术里直接承认并提议先做 demo。",
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
