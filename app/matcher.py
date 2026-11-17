"""匹配评估：把「这岗值不值得投」变成一个可追问的数字。

刻意不把打分交给大模型。理由有三：
1. 分数要稳定——同一份 JD 今天 72 分明天 68 分，用户就没法信任它；
2. 分数要可拆——用户会问「为什么 62」，答案必须落到具体证据，不能是"模型觉得"；
3. 离线要能跑——DEMO 模式不配 API key 也得给出分数，否则整个项目 clone 下来是死的。

大模型只在 M5 生成话术时出场。这也是本项目的一条设计分界线：
**判断归规则，表达归模型。**
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import rules

# 五维权重。硬约束不是分数而是闸门，所以不在这里。
WEIGHTS = {
    "direction": 0.35,    # 方向匹配
    "time": 0.20,         # 时间可行
    "threshold": 0.20,    # 门槛匹配
    "risk": 0.25,         # 风险（反向）
}


@dataclass
class Capability:
    """一条可引用的能力素材。keywords 越具体越好，"数据"这种泛词会污染分数。"""

    name: str
    type: str                      # project / internship / skill / course / cert
    keywords: list[str]
    verified: bool = True


@dataclass
class MatchResult:
    score: float
    blocked: bool
    blocked_reason: str
    dimensions: dict[str, dict] = field(default_factory=dict)
    findings: list[rules.Finding] = field(default_factory=list)
    reason_text: str = ""

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 1),
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "dimensions": self.dimensions,
            "findings": [
                {
                    "rule_id": f.rule_id,
                    "level": f.level,
                    "label": f.label,
                    "detail": f.detail,
                    "evidence": f.evidence,
                }
                for f in self.findings
            ],
            "reason_text": self.reason_text,
        }


# 素材类型的基础权重：项目最能证明能力，课程最弱
_TYPE_WEIGHT = {"project": 1.0, "internship": 0.9, "skill": 0.6, "cert": 0.4, "course": 0.35}
# 每个有效关键词命中给的分
_HIT_SCORE = 13.0
# 太短或太泛的词不计分
_NOISE = {"数据", "模型", "系统", "项目", "开发", "学习", "能力", "经验", "工作"}


def _norm(text: str) -> str:
    return text.lower()


def _direction(jd: str, caps: list[Capability]) -> dict:
    """方向匹配：能力素材的关键词在 JD 里的命中情况。

    这里用字面匹配而不是向量相似度，是权衡后的选择：可解释性优先。
    用户看到「命中：测试用例、大模型、需求文档」立刻明白分数怎么来的；
    而余弦相似度给个 0.73 是没法反驳也没法利用的。
    """
    low = _norm(jd)
    raw = 0.0
    hits: list[dict] = []

    for cap in caps:
        if not cap.verified:
            continue  # 未核实的素材不参与打分——这是防编造的第一道闸门
        matched = []
        for kw in cap.keywords:
            k = kw.strip()
            if len(k) < 2 or k in _NOISE:
                continue
            if _norm(k) in low:
                matched.append(k)
        if not matched:
            continue
        w = _TYPE_WEIGHT.get(cap.type, 0.5)
        gained = min(len(matched), 4) * _HIT_SCORE * w
        raw += gained
        hits.append({"name": cap.name, "type": cap.type, "matched": matched, "gained": round(gained, 1)})

    score = min(100.0, raw)
    hits.sort(key=lambda x: x["gained"], reverse=True)

    if score >= 60:
        reason = f"方向高度匹配，{hits[0]['name']} 直接对得上 JD 里的 {len(hits[0]['matched'])} 个要求"
    elif score >= 30:
        reason = f"方向基本匹配，最相关的素材是 {hits[0]['name']}" if hits else "方向匹配一般"
    elif hits:
        reason = "方向偏弱，仅有个别关键词重合，需要靠简历里的通用能力补"
    else:
        reason = "方向不匹配，你的项目和技能在本 JD 里找不到落点"

    return {"score": round(score, 1), "reason": reason, "hits": hits[:5]}


def _time(months_req: int | None, months_available: int, can_remote: bool) -> dict:
    if months_req is None:
        return {"score": 70.0, "reason": "JD 未写实习月数，需在招呼语里问清"}
    if months_req <= months_available:
        return {
            "score": 100.0,
            "reason": f"要求 {months_req} 个月，你能提供 {months_available} 个月，满足",
        }
    gap = months_req - months_available
    if can_remote:
        return {
            "score": 75.0,
            "reason": f"要求 {months_req} 个月，本地只能给 {months_available} 个月；若支持远程可延到 6 个月，需谈",
        }
    return {
        "score": max(20.0, 100.0 - gap * 25.0),
        "reason": f"要求 {months_req} 个月，你只有 {months_available} 个月，差 {gap} 个月，谈不拢就白投",
    }


def _threshold(jd: str, edu_req: str, exp_req: str, findings: list[rules.Finding]) -> dict:
    """门槛：学历、经验、年级。命中 hard_block 的年级已在 rules 里处理，这里只扣软分。"""
    score = 100.0
    notes: list[str] = []

    text = f"{jd}\n{edu_req}\n{exp_req}"
    if re.search(r"硕士|研究生", text) and "本科" not in edu_req:
        score -= 40
        notes.append("要求硕士")
    if re.search(r"大专|专科", edu_req) and not re.search(r"本科", edu_req):
        notes.append("门槛低于你的学历，投递不构成优势但可投")

    if any(f.rule_id == "EXP_SENIOR" for f in findings):
        score -= 30
        notes.append("要求 1 年以上经验")
    if re.search(r"经验不限|在校|应届|实习", text):
        notes.append("接受在校生/应届")

    score = max(0.0, score)
    reason = "、".join(notes) if notes else "学历与经验门槛无冲突"
    return {"score": round(score, 1), "reason": reason}


def _risk(findings: list[rules.Finding]) -> dict:
    """风险分：满分 100，逐条扣。hard_block 直接 0。"""
    penalty = {"hard_block": 100.0, "high": 28.0, "medium": 12.0, "low": 4.0}
    lost = sum(penalty.get(f.level, 5.0) for f in findings)
    score = max(0.0, 100.0 - lost)
    bad = [f for f in findings if f.level in ("hard_block", "high")]
    if bad:
        reason = "、".join(f.label for f in bad[:3])
    elif findings:
        reason = "、".join(f.label for f in findings[:3])
    else:
        reason = "未命中风险规则"
    return {"score": round(score, 1), "reason": reason}


def evaluate(
    jd_text: str,
    *,
    title: str = "",
    company: str = "",
    edu_req: str = "",
    exp_req: str = "",
    schedule: str = "",
    months_available: int = 3,
    can_remote: bool = True,
    user_grad_year: int = 2027,
    capabilities: list[Capability] | None = None,
    ignored_rules: set[str] | None = None,
    manual_block_reason: str = "",
) -> MatchResult:
    """跑完整评估。纯函数，不碰数据库，方便单测和 DEMO 复用。

    ignored_rules / manual_block_reason 是人工判断的注入点，由上层（scoring）
    从 reviews 表读出来传进来——这里不查库，保持纯函数才测得动。

    ignored_rules 只影响**打分与阻断**，不影响 `findings` 的返回内容：
    界面上要把被推翻的那条照样列出来，标一句「已推翻」。如果连展示都过滤掉，
    规则命中列表会莫名少一行，用户会以为规则漏了。
    """
    caps = capabilities or []
    findings = rules.analyze(
        jd_text,
        title=title,
        company=company,
        user_grad_year=user_grad_year,
        months_available=months_available,
        schedule=schedule,
    )

    ignored = {str(x) for x in (ignored_rules or set())}
    active = [f for f in findings if f.rule_id not in ignored]

    dims = {
        "direction": _direction(f"{title}\n{jd_text}", caps),
        "time": _time(rules.detect_months(jd_text), months_available, can_remote),
        "threshold": _threshold(jd_text, edu_req, exp_req, active),
        "risk": _risk(active),
    }

    # 人工否决在规则之上：它不看规则怎么算，直接把结论钉死。
    # 放在 dims 之后是为了让界面仍能显示四维依据——人要能看见自己否决了什么。
    if manual_block_reason:
        return MatchResult(
            score=0.0,
            blocked=True,
            blocked_reason=f"人工判定不投：{manual_block_reason}",
            dimensions=dims,
            findings=findings,
            reason_text="这条是你自己判定不投的，规则怎么算都不影响这个结论。"
                        "想回到纯规则判断，在详情页撤销人工判断即可。",
        )

    block = rules.hard_blocked(active)
    if block:
        return MatchResult(
            score=0.0,
            blocked=True,
            blocked_reason=f"{block.label}：{block.detail}",
            dimensions=dims,
            findings=findings,
            reason_text=f"不满足硬约束（{block.label}），不建议投递。",
        )

    total = sum(dims[k]["score"] * w for k, w in WEIGHTS.items())

    # 方向与风险是否决项，不是加分项。
    # 这条是自检跑出来的：第一版里培训机构拿了 67.8 分——因为「门槛匹配」和
    # 「风险」两项基础分都给得很高，把方向分低的窟窿补上了。结果反直觉到没法用：
    # 一个卖课的岗位显示 67 分，用户会真的去投。
    # 修正方式是给总分设上限（ceiling），而不是继续调权重——调权重治不了
    # "三项及格一项极差"这种结构性问题。
    ceilings: list[tuple[float, str]] = []
    if any(f.level == "high" for f in active):
        ceilings.append((40.0, "命中高风险规则"))
    if dims["direction"]["score"] < 30:
        ceilings.append((55.0, "方向匹配不足"))
    if dims["time"]["score"] < 40:
        ceilings.append((65.0, "实习时长谈不拢"))

    # 没拿到 JD 正文时，门槛和风险两项都是"没看见问题"而不是"没有问题"，
    # 加权出来会得到一个虚高的中间分（实测空 JD 能到 55）。抓取只取列表页时最容易踩这个坑。
    # 处理方式不是调权重，而是压低上限并明说——分数不可信的时候要让人看出来。
    short_jd = len((jd_text or "").strip()) < 60
    if short_jd:
        ceilings.append((45.0, "职位描述不完整"))

    cap_note = ""
    if ceilings:
        cap, why = min(ceilings, key=lambda x: x[0])
        if total > cap:
            total = cap
            cap_note = f"（分数按「{why}」封顶）"

    # 理由永远先讲方向：用户看一行字就该知道这岗到底对不对口，
    # 而不是读到"主要得分来自风险：未命中风险规则"这种等于没说的话。
    if short_jd:
        reason = "职位描述不完整，只拿到了标题和标签——方向无法判断，这个分数只反映门槛与已知风险"
    elif dims["direction"]["score"] >= 50:
        reason = f"最匹配的是方向：{dims['direction']['reason']}"
    else:
        reason = f"主要短板在方向：{dims['direction']['reason']}"

    others = [k for k, w in WEIGHTS.items() if dims[k]["score"] < 50 and k != "direction"]
    if others:
        reason += "；另外偏弱的是" + "、".join(_DIM_CN[k] for k in others)
    reason += cap_note

    # 被人推翻的规则要在理由里留一句。否则分数会从 40 跳到 78 而正文一个字不提，
    # 看起来像分数自己错了。
    overturned = [f.label for f in findings if f.rule_id in ignored]
    if overturned:
        reason += "（已人工推翻：" + "、".join(overturned) + "）"

    return MatchResult(
        score=round(total, 1),
        blocked=False,
        blocked_reason="",
        dimensions=dims,
        findings=findings,
        reason_text=reason,
    )


_DIM_CN = {
    "direction": "方向匹配",
    "time": "时间可行",
    "threshold": "门槛匹配",
    "risk": "风险",
}
