"""人工确认：规则之外的第二类判断来源。

---- 为什么需要这一层 ----

规则是纯函数，同一份 JD 永远同一个判断。但「永远一致」不等于「永远正确」：
20 条对抗集 + 19 条精度集都过了，只能说明这两批写法覆盖到了，覆盖不到的那种
既不会报错也不会告警——它只会安静地把一个好岗位压到 40 分，而 40 分意味着
用户不会再点开它。**这种错是无声的，必须由人补上。**

但「人工确认」不等于「人把所有岗位过一遍」，那等于取消自动化。这一层只做一件事：
把**规则没把握、而且判错代价高**的岗位挑出来，其余全部交给规则。

判错的代价是不对称的，所以队列不按分数排：

    「疑似培训机构」判错了 → 你损失一个方向对口的岗位，而且你永远不会知道
    「JD 写明单休」判错了 → 你损失一次点开详情页的时间

    → 同样是「规则可能错」，前者要人看一眼，后者不用。这就是 bucket 的分类依据。

还有一类岗位根本不进队列：方向分低于 MIN_DIRECTION 的。规则就算把它判错了，
你也不会投，塞进待办只是制造负担。
**队列的取舍标准不是「规则有没有把握」，而是「这条值不值得你花两分钟」。**

---- 三条操作 ----

    pass  规则误判，改判可投（同时记下被推翻的规则 id）
    block 人工判定不投（即使规则没拦，人的否决权也在规则之上）
    ack   认可规则判断，移出队列（不改任何判定）
    reset 撤销人工判断，回到纯规则

---- 四条工程约束（都是踩过坑才定下来的）----

1. override 按**规则 id** 生效，见 Review 模型注释。
2. override 在 `score_only()` 里读，不在 Match 里改。这样「重新评估」不会把人的
   判断冲掉——否则人工结论只活到下一次重算，而这个项目里重算每天都在发生。
3. 人工只推翻**风险类**判断，不碰方向分。「这不是培训机构」不等于「这岗对口」，
   相关性归规则、真实性归人，两条线不能互相借道。
4. 每次判断都带时间与备注字段（备注可空），且可撤销。人工改判是**证据链上的一环**，不是覆盖写：
   被问起「你的分数为什么和规则算的不一样」时，要能翻出当时为什么。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .models import Job, Review
from .rules import Finding

# ---------------------------------------------------------------- 常量

PASS = "pass"
BLOCK = "block"
ACK = "ack"
WAIT = "wait"
RESET = "reset"
DECISIONS = (PASS, BLOCK, ACK, WAIT, RESET)

DECISION_CN = {
    PASS: "改判可投",
    BLOCK: "判定不投",
    ACK: "认可规则",
    WAIT: "在等回复",
    RESET: "撤销判断",
}

# 方向分低于这条线，判错也不值得人看。取 30 是因为 matcher 里
# 「方向 < 30」本身就是一条封顶条件：低于它，分数已经不可能进入可投区间。
MIN_DIRECTION = 30.0

# 推断型规则：结论建立在话术而不是事实上，换个说法就可能不成立，
# 因此人看一眼的收益最高。反过来 WEEKEND_VIOLATION / GRADE_MISMATCH 是原文写死的事实。
INFERRED = {"TRAINING_SELL", "SALES_DISGUISE", "EXP_SENIOR", "MONTHS_TOO_LONG"}

BUCKETS = {
    "overturn": {
        "cn": "规则可能判重了",
        "why": "结论建立在招生／销售话术上，而这岗方向对得上——判错的话你损失的是一个能投的岗位，而且不会知道。",
        "priority": 1,
    },
    "fact": {
        "cn": "事实型阻断",
        "why": "阻断依据是 JD 原文里的一句硬话（工时／届别），不是推断。要确认的只有一件事：规则有没有读错上下文。",
        "priority": 2,
    },
    "gap": {
        "cn": "关键信息缺失",
        "why": "JD 没写工作时间。这一项不参与打分，等于分数里缺了一块。",
        "priority": 3,
    },
}

_ACTIONS = {
    "overturn": [(PASS, "规则误判，改判可投"), (BLOCK, "规则判断成立，我不投"), (ACK, "认可规则，不再追问")],
    "fact": [(BLOCK, "确认放弃"), (PASS, "规则读错了，改判可投")],
    "gap": [(WAIT, "已问 HR，等回复"), (ACK, "信息补齐，不再追问"), (BLOCK, "不投")],
}


# ---------------------------------------------------------------- 数据结构


@dataclass
class Resolved:
    """一条职位当前生效的人工干预。空值表示「纯规则」。"""

    review: Review | None = None
    ignored: set[str] = field(default_factory=set)
    manual_block_reason: str = ""


@dataclass
class ReviewItem:
    """队列里的一行。question 是让人能立刻做决定的那句话，不是规则输出的复述。"""

    job: Job
    bucket: str
    score: float | None
    blocked: bool
    direction: float
    question: str
    evidence: list[str]
    rule_labels: list[str]
    facts: list[str]

    @property
    def actions(self) -> list[tuple[str, str]]:
        return _ACTIONS[self.bucket]

    @property
    def bucket_cn(self) -> str:
        return BUCKETS[self.bucket]["cn"]


# ---------------------------------------------------------------- 读写


def latest(db: Session, job_id: int | None) -> Review | None:
    if not job_id:
        return None
    return (
        db.query(Review)
        .filter(Review.job_id == job_id)
        .order_by(Review.id.desc())
        .first()
    )


def resolve(db: Session, job_id: int | None) -> Resolved:
    """把最新一条人工判断翻译成「规则该让哪几条路」。"""
    rv = latest(db, job_id)
    if rv is None or rv.decision == RESET:
        return Resolved()
    if rv.decision == PASS:
        return Resolved(review=rv, ignored={str(x) for x in (rv.rule_ids_json or [])})
    if rv.decision == BLOCK:
        note = (rv.note or "").strip()
        return Resolved(review=rv, manual_block_reason=note or "你判定这条不投")
    # ACK / WAIT：规则照常生效，只是不再追问。
    # WAIT 和 ACK 的区别只在「事还没完」：工时欠着 HR 一个回复，所以要单独列出来，
    # 但判定上不能有一丝差别——等待不是证据，凭什么改分数。
    return Resolved(review=rv)


def record(
    db: Session,
    job: Job,
    decision: str,
    *,
    rule_ids: list[str] | None = None,
    bucket: str = "",
    note: str = "",
) -> Review:
    """写一条人工判断。不做重算——重算由 scoring 层负责，这里只管留痕。"""
    if decision not in DECISIONS:
        raise ValueError(f"未知的人工判断：{decision}")
    note = (note or "").strip()[:600]
    # 备注框留空 + 判断和上一条相同（重复点同一个按钮、刷新后重提），
    # 极可能不是「我要把理由删掉」，而是「我懒得再写一遍」。备注是证据链的一环，
    # 无意识地清空它，比多留一条旧理由危险得多——所以这里继承，不覆盖。
    # 想真改就填新的；想真删，去详情页撤销重来。
    if not note:
        prev = latest(db, job.id)
        if prev is not None and prev.decision == decision and (prev.note or "").strip():
            note = prev.note
    rv = Review(
        job_id=job.id,
        decision=decision,
        rule_ids_json=[str(x) for x in (rule_ids or [])],
        bucket=bucket,
        note=(note or "").strip()[:600],
    )
    db.add(rv)
    db.commit()
    return rv


# ---------------------------------------------------------------- 判据（纯函数）


def judge(findings: list[Finding], direction: float) -> dict | None:
    """这条要不要人看一眼。返回 None 表示规则自己就能定。

    判的是「规则这个结论能不能直接用」，不是「分数高不高」——分数是规则的结论，
    拿它当判据等于用结论论证结论。
    """
    if direction < MIN_DIRECTION:
        return None

    hard = [f for f in findings if f.level == "hard_block"]
    if hard:
        f = hard[0]
        quote = "、".join(f.evidence[:2])
        lead = f"JD 原文写了「{quote}」，按你的硬约束不能投。" if quote else f"{f.label}：{f.detail}"
        return {"bucket": "fact", "question": lead + " 要确认的只有一件事——规则有没有读错上下文。",
                "focus": [f.rule_id]}

    inferred = [f for f in findings if f.rule_id in INFERRED]
    if inferred:
        f = max(inferred, key=lambda x: {"high": 3, "medium": 2}.get(x.level, 1))
        quote = "、".join(f"「{e}」" for e in f.evidence[:3]) or "（没抓到具体原文）"
        return {
            "bucket": "overturn",
            "question": f"规则判为「{f.label}」，依据是 {quote}。"
                        "这条是推断不是事实——信规则，还是自己看一遍 JD？",
            "focus": [f.rule_id],
        }

    if any(f.rule_id == "WEEKEND_UNKNOWN" for f in findings):
        return {
            "bucket": "gap",
            "question": "JD 没写工作时间。你的硬约束是休周日（12/06 上午考 N2），这条只能去问 HR。",
            "focus": ["WEEKEND_UNKNOWN"],
        }
    return None


def _facts(job: Job, direction: float) -> list[str]:
    out: list[str] = []
    place = f"{job.city}{job.district}".strip()
    if place:
        out.append(place)
    if job.salary_min or job.salary_max:
        unit = {"day": "元/天", "month": "元/月", "year": "元/年"}.get(job.salary_unit, "")
        lo = f"{job.salary_min:.0f}" if job.salary_min else "?"
        hi = f"{job.salary_max:.0f}" if job.salary_max else "?"
        out.append((lo if lo == hi else f"{lo}-{hi}") + unit)
    out.append(f"方向 {direction:.0f} 分")
    out.append(f"JD {len((job.jd_text or '').strip())} 字")
    return out


def build_item(
    job: Job,
    findings: list[Finding],
    direction: float,
    score: float | None,
    blocked: bool,
) -> ReviewItem | None:
    verdict = judge(findings, direction)
    if verdict is None:
        return None
    # 证据只取「驱动这次判断的那条规则」的原文。全都摊上去的话，
    # 「6天/周、单休」旁边会跟着一条「6 个月」——那是时长规则的话，跟这次要你确认的事无关，
    # 只会让人以为规则在乱命中。
    focus = set(verdict.get("focus") or [])
    evidence: list[str] = []
    for f in findings:
        if focus and f.rule_id not in focus:
            continue
        for e in f.evidence:
            if e not in evidence:
                evidence.append(e)
    return ReviewItem(
        job=job,
        bucket=verdict["bucket"],
        score=score,
        blocked=blocked,
        direction=direction,
        question=verdict["question"],
        evidence=evidence[:6],
        rule_labels=[f.label for f in findings if f.rule_id in focus] or [f.label for f in findings],
        facts=_facts(job, direction),
    )
