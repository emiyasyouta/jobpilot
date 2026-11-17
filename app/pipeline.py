"""投递跟进：招呼发出去之后的那半条链路。

---- 这一层解决的不是「记录」，是「谁欠谁一个动作」 ----

P1 抓岗位，P2 判断值不值得投。到 P3，问题从「该不该投」变成「投了之后呢」。
后者有两条特有的失效方式，都不会报错：

1. **对方回了，我没看见。** HR 回你一句「明天方便来面试吗」，你隔两天才看到。
   这跟漏看消息无关——是没有人替你把「这条现在轮到你」算出来。
2. **发出去就忘了。** 40 条岗位发完招呼，谁回过、谁没回、谁该催第二次，
   靠翻 Boss 的列表是靠不住的：它的列表按时间排，不按「欠了多久」排。

所以这里的第一性问题是：**这条岗位现在卡在谁手上。**

    me    等你动作（还没投 / 人家回了你还没回）
    them  等对方（招呼已发 / 面完了等结果）
    done  已经结束（给了 offer / 拒了 / 彻底没回音）

按分数排是 P2 的事；P3 按「卡在谁手上 + 卡了多久」排。一个 60 分但 HR 等你回话
的岗位，比一个 84 分但还没投的岗位更急——前者有时效，后者没有。

---- 三条边界 ----

* **状态推进由事实决定，不靠人点。** HR 回了 → 必然 replied，不需要谁去点一个
  「标记为已回复」。要人手动维护的状态，三天后一定是错的。
* **回复里抽出来的事实不自动生效。** 抽取是纯函数，采信是判断。HR 口头说「双休」
  和 JD 白纸黑字写着「单休」，这两句冲突时谁对，得有人拍板并留痕。
* **不猜 HR 的想法。** 意图识别只做关键词能定的事（约面／问时间／问薪资／拒绝），
  定不了就说「看不出要你做什么」，让人自己读。猜错了比不猜更费时间。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import config
from .models import Greeting, Job, Message

# ---------------------------------------------------------------- 状态机

NEW = "new"
SCORED = "scored"
BLOCKED = "blocked"
GREETED = "greeted"
REPLIED = "replied"
INTERVIEW = "interview"
OFFER = "offer"
REJECTED = "rejected"
GHOSTED = "ghosted"

STAGES = (NEW, SCORED, BLOCKED, GREETED, REPLIED, INTERVIEW, OFFER, REJECTED, GHOSTED)

STAGE_CN = {
    NEW: "待评分",
    SCORED: "已评分，待投",
    BLOCKED: "硬约束不满足",
    GREETED: "已发消息，等回音",
    REPLIED: "对方已回，等你",
    INTERVIEW: "面试中，等结果",
    OFFER: "已给 offer",
    REJECTED: "已结束",
    GHOSTED: "久无回音",
}

# 合法转移。写在表里而不是散在路由里，一是能自检，二是逼人想清楚
# 「这一步真的可能发生吗」——例如 greeted 直接跳 offer 就该被拦住。
TRANSITIONS: dict[str, set[str]] = {
    # NEW / BLOCKED 也能直接被一条回复推到 replied：录入顺序不保证先评分后投递，
    # 而「对方确实回了你」是事实，不该因为状态还没走到 greeted 就丢掉。
    NEW: {SCORED, BLOCKED, REPLIED},
    SCORED: {GREETED, BLOCKED, REJECTED, REPLIED},
    BLOCKED: {NEW, SCORED, REPLIED},
    GREETED: {REPLIED, GHOSTED, REJECTED, SCORED},
    # GREETED → SCORED：投错了要能撤回，否则这条岗位永远退出不了"已投"的池子
    REPLIED: {INTERVIEW, OFFER, REJECTED, GHOSTED, GREETED},
    # REPLIED → GREETED：你回完之后又轮到对方，语义上就是「已发消息等回音」
    INTERVIEW: {OFFER, REJECTED, GHOSTED, REPLIED},
    OFFER: set(),
    REJECTED: {SCORED},
    GHOSTED: {GREETED, REPLIED, REJECTED},
}

OWNER = {
    NEW: "me",
    SCORED: "me",
    BLOCKED: "me",
    REPLIED: "me",
    GREETED: "them",
    INTERVIEW: "them",
    OFFER: "done",
    REJECTED: "done",
    GHOSTED: "done",
}

OWNER_CN = {"me": "等你动作", "them": "等对方", "done": "已结束"}


def can_go(src: str, dst: str) -> bool:
    return dst in TRANSITIONS.get(src, set())


def set_stage(db, job: Job, dst: str) -> bool:
    """推进状态。不合法的转移直接拒绝并返回 False，不抛异常——

    调用点全是「人点了个按钮」，抛异常只会变成 500 页面；拒绝并保持原状，
    界面上那条岗位还在原来的分组里，人一看就知道这一步走不通。
    """
    if job.status == dst:
        return False
    if not can_go(job.status, dst):
        return False
    job.status = dst
    db.commit()
    return True


# 界面上该给哪些按钮。只映射「人真的会点」的转移——表里合法的每一条都配上按钮，
# 结果就是一排看不懂的状态名（「置为 interview」）。动作名，不是状态名。
_STAGE_ACTIONS: dict[tuple[str, str], str] = {
    (SCORED, REJECTED): "我不投了",
    (GREETED, REPLIED): "对方回了，去记一下",
    (GREETED, GHOSTED): "确定没回音，收工",
    (GREETED, REJECTED): "对方明确拒了",
    (REPLIED, INTERVIEW): "约上面试了",
    (REPLIED, OFFER): "直接给 offer 了",
    (REPLIED, REJECTED): "谈崩／被拒",
    (REPLIED, GHOSTED): "问了不回，作罢",
    (INTERVIEW, OFFER): "拿到 offer",
    (INTERVIEW, REJECTED): "面试没过",
    (INTERVIEW, GHOSTED): "面完没下文",
    (GHOSTED, GREETED): "再催一次",
    (REJECTED, SCORED): "标错了，退回待投",
}


def stage_actions(status: str) -> list[tuple[str, str]]:
    return [(dst, label) for (src, dst), label in _STAGE_ACTIONS.items() if src == status]


# ---------------------------------------------------------------- 会话

@dataclass
class Turn:
    """一条消息，以及「这条之后轮到我」的判定。"""

    at: datetime
    direction: str            # out=我发的 / in=对方发的
    content: str
    message_id: int | None = None
    is_greeting: bool = False


def thread(db, job: Job) -> list[Turn]:
    rows = (
        db.query(Message)
        .filter(Message.job_id == job.id)
        .order_by(Message.sent_at, Message.id)
        .all()
    )
    return [
        Turn(at=r.sent_at, direction=r.direction, content=r.content,
             message_id=r.id, is_greeting=(r.source_msg_id or "").startswith("greeting:"))
        for r in rows
    ]


def log_out(db, job: Job, content: str, *, greeting_id: int | None = None) -> Message:
    """记一条「我发的」。招呼语发出时自动调用。"""
    msg = Message(
        job_id=job.id,
        direction="out",
        content=(content or "").strip(),
        sent_at=datetime.now(),
        source_msg_id=f"greeting:{greeting_id}" if greeting_id else "",
    )
    db.add(msg)
    db.commit()
    return msg


def log_in(db, job: Job, content: str) -> Message:
    """记一条「对方发的」。HR 回话必须由人粘进来——没有合规的方式自动抓。

    粘进来之后立刻做的事只有一件：**把状态推到 replied**。
    因为「对方回了一句话」是客观事实，不该等谁来点确认。
    """
    msg = Message(
        job_id=job.id,
        direction="in",
        content=(content or "").strip(),
        sent_at=datetime.now(),
    )
    db.add(msg)
    # 走状态机而不是直接赋值：NEW→REPLIED 这种边如果没写进表里，
    # 说明它本来就不该发生，那也该走同一条校验，而不是在函数里另开一条暗道。
    if can_go(job.status, REPLIED):
        job.status = REPLIED
    db.commit()
    return msg


def backfill_sent_greetings(db) -> int:
    """把历史上「已发出但没进会话流」的招呼语补成 Message。

    P3 之前 mark_sent 只改 Greeting，不写 Message。不补的话，这些岗位在跟进板上
    会显示「一条消息都没有」，于是被当成「还没投」重新催一遍。
    """
    have = {
        (r.source_msg_id or "")
        for r in db.query(Message).filter(Message.source_msg_id != "").all()
    }
    added = 0
    for g in db.query(Greeting).filter(Greeting.is_sent.is_(True)).all():
        key = f"greeting:{g.id}"
        if key in have:
            continue
        db.add(Message(
            job_id=g.job_id,
            direction="out",
            content=g.content or "",
            sent_at=g.sent_at or g.created_at,
            source_msg_id=key,
        ))
        added += 1
    if added:
        db.commit()
    return added


# ---------------------------------------------------------------- 时效

FOLLOWUP_HOURS = config.FOLLOWUP_AFTER_HOURS          # 发出后多久没回算「该催」
COLD_HOURS = FOLLOWUP_HOURS * 4                        # 再翻四倍还没回 → 久无回音
REPLY_DUE_HOURS = 24                                   # 对方回了，你拖多久算失礼


def _last(thread_: list[Turn], direction: str) -> Turn | None:
    for t in reversed(thread_):
        if t.direction == direction:
            return t
    return None


def _hours_since(t: Turn | None, now: datetime) -> float | None:
    if t is None or t.at is None:
        return None
    return max(0.0, (now - t.at).total_seconds() / 3600.0)


@dataclass
class Clock:
    """这条岗位的时间账。所有字段都是「小时数」，None 表示没有这件事。"""

    my_last: float | None = None       # 我最后一条发出距今
    their_last: float | None = None    # 对方最后一条发来距今
    due: bool = False                  # 该催了（等到超过阈值）
    cold: bool = False                 # 凉了（等到超过四倍阈值）
    overdue: bool = False              # 轮到我，且已经拖过 24 小时

    @property
    def badge(self) -> str:
        if self.overdue:
            return "你拖了"
        if self.cold:
            return "久无回音"
        if self.due:
            return "该催"
        return ""


def clock(db, job: Job, now: datetime | None = None) -> Clock:
    now = now or datetime.now()
    th = thread(db, job)
    c = Clock(
        my_last=_hours_since(_last(th, "out"), now),
        their_last=_hours_since(_last(th, "in"), now),
    )
    owner = OWNER.get(job.status, "me")
    if owner == "them":
        # 等对方的时长，从我最后一条发出算；没发过就按「还没开始等」处理
        waited = c.my_last
        if waited is not None:
            c.due = waited >= FOLLOWUP_HOURS
            c.cold = waited >= COLD_HOURS
    elif owner == "me" and job.status == REPLIED:
        c.overdue = (c.their_last or 0) >= REPLY_DUE_HOURS
    return c


# ---------------------------------------------------------------- 意图

ASK_INTERVIEW = "ask_interview"
ASK_SCHEDULE = "ask_schedule"
ASK_SALARY = "ask_salary"
ASK_INFO = "ask_info"
REJECT = "reject"
INFO = "info"
UNCLEAR = "unclear"

INTENT_CN = {
    ASK_INTERVIEW: "约你面试",
    ASK_SCHEDULE: "问你的时间",
    ASK_SALARY: "问薪资预期",
    ASK_INFO: "问你的情况",
    REJECT: "拒绝／已招满",
    INFO: "只是告知信息",
    UNCLEAR: "看不出要你做什么",
}

_PATTERNS: list[tuple[str, list[str]]] = [
    (REJECT, [r"已招满", r"招满了", r"已招到", r"暂时不需要", r"不太合适", r"不合适",
              r"岗位已关", r"暂时没有.{0,6}名额", r"不再考虑", r"已停止招聘"]),
    (ASK_INTERVIEW, [r"面试", r"面谈", r"聊一聊", r"聊聊", r"来公司", r"过来看看",
                     r"复试", r"初试"]),
    (ASK_SALARY, [r"期望薪资", r"薪资要求", r"多少钱", r"待遇要求", r"日薪.*多少",
                  r"你期望.*薪"]),
    (ASK_SCHEDULE, [r"什么时候(能|可以)?到岗", r"几号(能|可以)?到岗", r"什么时候开始",
                    r"能实习多久", r"可以实习到"]),
    (ASK_INFO, [r"你(是|是哪个).{0,8}(学校|专业)", r"大几", r"哪一届", r"有没有.{0,8}经验",
                r"会(不会|用)?(python|java|测试|sql)", r"发.{0,4}简历"]),
]

# 「问句」的信号：没有这些，说明对方只是在告知，不是在要你做事
_QUESTION_HINT = re.compile(r"[?？]|吗|呢|方便|能否|可以.{0,4}吗|几|多少|怎么样|如何")


def intent_of(text: str) -> str:
    """识别 HR 这段话要你做什么。定不了就返回 UNCLEAR，不硬猜。

    顺序即优先级：一句「已经招满了，下次面试再联系你」里同时出现「招满」和「面试」，
    该判拒绝——把拒绝读成约面，代价是白准备一场面试。
    """
    t = (text or "").strip()
    if not t:
        return UNCLEAR
    for name, pats in _PATTERNS:
        if any(re.search(p, t) for p in pats):
            return name
    if _QUESTION_HINT.search(t):
        return ASK_INFO
    return INFO


# ---------------------------------------------------------------- 事实抽取

@dataclass
class Fact:
    """从回复里抽出来的一条事实。**带原文引用**，否则人没法判断该不该采信。"""

    key: str                  # days_per_week / salary / weekends / certificate / interview_at
    value: object
    quote: str
    label: str = ""

    def as_text(self) -> str:
        return f"{self.label}（原文「{self.quote}」）"


_DAYS = re.compile(r"([1-6])\s*天\s*/\s*周|每周\s*([1-6])\s*天|一周\s*([1-6])\s*天")
_WEEKEND = re.compile(r"双休|单休|大小周|做五休二|做六休一|周休二日|轮休|排休")
_CERT = re.compile(r"(可以|能|可)开.{0,4}(实习)?证明|开不了.{0,4}证明|不提供.{0,4}证明|提供.{0,4}实习证明")
_SALARY = re.compile(
    r"(\d{2,5})\s*[-~到至]\s*(\d{2,5})\s*(元)?\s*/\s*(天|月)|"
    r"(\d{2,5})\s*(元)?\s*/\s*(天|月)|"
    r"(\d{1,2})\s*[kK]\s*[-~到至]\s*(\d{1,2})\s*[kK]"
)

_WEEKEND_DAYS = {"双休": 5, "做五休二": 5, "周休二日": 5, "单休": 6, "做六休一": 6}


def _clip(text: str, m: re.Match, pad: int = 12) -> str:
    """取命中处前后一小段做引文。整段粘上来人读不下去，只取一句就够判断。"""
    lo = max(0, m.start() - pad)
    hi = min(len(text), m.end() + pad)
    return text[lo:hi].strip()


def extract(text: str) -> list[Fact]:
    """从一段 HR 回复里抽事实。纯函数、可复现，不落库。

    同一类事实只取第一条命中：HR 说到「我们双休」又提到「偶尔要值班」时，
    第二条不该变成第二个值——那不是数据，那是新的一句话，读它是人的事。
    """
    t = (text or "").strip()
    out: list[Fact] = []
    if not t:
        return out

    m = _DAYS.search(t)
    w = _WEEKEND.search(t)
    if m:
        days = int(next(g for g in m.groups() if g))
        out.append(Fact("days_per_week", days, _clip(t, m), f"每周 {days} 天"))
    elif w:
        # 只说了「双休」没说几天：能推就推出天数，推不出（大小周/轮休）只记这个词，
        # 让采信那一步去问人——把「大小周」硬算成 5.5 天，是在替 HR 编数字。
        word = w.group(0)
        days = _WEEKEND_DAYS.get(word)
        label = word + (f"（折 {days} 天/周）" if days else "（天数未明说）")
        if days:
            out.append(Fact("days_per_week", days, _clip(t, w), label))
        else:
            out.append(Fact("weekends", word, _clip(t, w), label))
    # 天数已经由数字给出时，「双休」只作为旁证记录，不覆盖那个数字
    if w and m:
        word = w.group(0)
        out.append(Fact("weekends", word, _clip(t, w), f"也说到了「{word}」"))

    c = _CERT.search(t)
    if c:
        ok = not re.search(r"开不了|不提供|不能开", c.group(0))
        out.append(Fact("certificate", ok, _clip(t, c), "实习证明：" + ("可以开" if ok else "开不了")))

    s = _SALARY.search(t)
    if s:
        out.append(Fact("salary", s.group(0).strip(), _clip(t, s), f"薪资：{s.group(0).strip()}"))

    return out


# ---------------------------------------------------------------- 回复草稿

# 每种意图配一句回法。**只引档案里有的东西**，所以这些模板里没有一个是
# 「我学习能力强」这类不可验证的话——模板的价值是省打字，不是替你吹。
_DRAFTS: dict[str, str] = {
    ASK_INTERVIEW: (
        "好的，谢谢您。{when}我都可以，方便的话想约在下午。"
        "想确认下是现场还是线上视频面试？如果是现场，地址是哪里？"
    ),
    ASK_SCHEDULE: "我{major}{year}届，{start}可以到岗，能连续做到{end}。实习期间时间可以配合岗位安排。",
    ASK_SALARY: "薪资我参考岗位写的范围就可以，实习期按贵司标准来。主要想确认下是日薪结算还是月结？",
    ASK_INFO: "我是{school}{major}{year}届{degree}，{start}可以到岗。简历我这就发您，有需要补充的材料随时说。",
    INFO: "好的，明白了，谢谢您。那我这边等您下一步安排。",
    REJECT: "",
    UNCLEAR: "",
}


def _cn_date(raw: str) -> str:
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", (raw or "").strip())
    if not m:
        return ""
    return f"{int(m.group(2))}月{int(m.group(3))}日"


def draft_reply(job: Job, intent: str, *, profile=None, pref=None, days_ok: bool | None = None) -> str:
    """按意图给一句可复制的回话。空串表示「这条不该由模板回」。

    拒绝和看不懂这两种情况故意不给模板：前者不需要回，后者模板一定是错的方向。
    """
    tpl = _DRAFTS.get(intent, "")
    if not tpl:
        return ""
    values = {
        "when": "时间上",
        "major": (getattr(profile, "major", "") or ""),
        "school": (getattr(profile, "school", "") or ""),
        "degree": (getattr(profile, "degree", "") or ""),
        "year": ((getattr(profile, "edu_end", "") or "")[:4]),
        "start": _cn_date(getattr(pref, "available_from", "")),
        "end": _cn_date(getattr(pref, "available_to", "")),
    }
    # 模板里每个占位符都得有值。缺一个就整句不发——「我智能科学与技术届，可以到岗」
    # 这种残句发出去，比不发还糟：对方能看出这是脚本拼的。
    if any(not values.get(k) for k in re.findall(r"\{(\w+)\}", tpl)):
        return ""
    text = tpl
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text.strip()


# ---------------------------------------------------------------- 看板

@dataclass
class Card:
    """跟进板上的一行。带够让人立刻动手的信息，但不带任何「建议语气」的话。"""

    job: Job
    clock: Clock
    score: float = 0.0
    last_in: Turn | None = None
    intent: str = ""
    facts: list[Fact] = field(default_factory=list)
    draft: str = ""

    @property
    def stage_cn(self) -> str:
        return STAGE_CN.get(self.job.status, self.job.status)

    @property
    def intent_cn(self) -> str:
        return INTENT_CN.get(self.intent, "") if self.intent else ""

    @property
    def actions(self) -> list[tuple[str, str]]:
        return stage_actions(self.job.status)

    @property
    def claims(self) -> dict:
        """已经采信过的事实。模板里要判断「这条是不是已经采信了」。"""
        return self.job.hr_facts_json or {}


def _card(db, job: Job, now: datetime, profile=None, pref=None) -> Card:
    th = thread(db, job)
    last_in = _last(th, "in")
    intent = intent_of(last_in.content) if last_in else ""
    facts = extract(last_in.content) if last_in else []
    m = job.latest_match
    card = Card(
        job=job,
        clock=clock(db, job, now),
        score=round(m.score, 1) if m else 0.0,
        last_in=last_in,
        intent=intent,
        facts=facts,
    )
    if job.status == REPLIED:
        card.draft = draft_reply(job, intent, profile=profile, pref=pref)
    return card


def board(db, *, profile=None, pref=None, now: datetime | None = None) -> dict[str, list[Card]]:
    """跟进看板。四组，按「谁欠谁一个动作」分，不按分数分。

    顺序就是优先级，别调：对方回了你没回（过期就凉）> 发出去了该催 >
    等对方 / 面完等结果 > 还没投（没时效压力）> 已结束。
    """
    now = now or datetime.now()
    groups: dict[str, list[Card]] = {"due_me": [], "chase": [], "waiting": [], "todo": [], "closed": []}

    for job in db.query(Job).order_by(Job.id.desc()).all():
        stage = job.status
        if stage in (NEW,):
            continue
        card = _card(db, job, now, profile, pref)
        owner = OWNER.get(stage, "me")
        if stage == REPLIED:
            groups["due_me"].append(card)
        elif owner == "them" and (card.clock.due or card.clock.cold):
            groups["chase"].append(card)
        elif owner == "them":
            groups["waiting"].append(card)
        elif owner == "done":
            groups["closed"].append(card)
        elif stage in (SCORED, BLOCKED):
            groups["todo"].append(card)
        else:
            groups["waiting"].append(card)

    # 过期最久的排最前：已经拖过 24 小时的那条，每多拖一小时就更接近凉
    groups["due_me"].sort(key=lambda c: (not c.clock.overdue, -(c.clock.their_last or 0)))
    # 凉的排前面（该放弃了），然后按等了多久
    groups["chase"].sort(key=lambda c: (not c.clock.cold, -(c.clock.my_last or 0)))
    groups["waiting"].sort(key=lambda c: -(c.clock.my_last or 0))
    # 还没投的按分数：这块没有时效，分数就是唯一的优先级
    groups["todo"].sort(key=lambda c: -c.score)
    groups["closed"].sort(key=lambda c: -(c.clock.their_last or c.clock.my_last or 0))
    return groups


def stats(groups: dict[str, list[Card]]) -> dict[str, int]:
    return {
        "due_me": len(groups["due_me"]),
        "chase": len(groups["chase"]),
        "waiting": len(groups["waiting"]),
        "todo": len(groups["todo"]),
        "closed": len(groups["closed"]),
        "overdue": sum(1 for c in groups["due_me"] if c.clock.overdue),
        "cold": sum(1 for c in groups["chase"] if c.clock.cold),
    }


# ---------------------------------------------------------------- 一致性

@dataclass
class Anomaly:
    job: Job
    kind: str        # phantom=状态虚高 / stale=状态滞后
    detail: str


def audit(db) -> list[Anomaly]:
    """找出「状态」和「会话」对不上的岗位。

    这两种不一致都不会报错，但后果相反且都很贵：

    phantom  状态说投过了，会话里一条发出去的记录都没有 → 它会被放进「等对方」，
             于是你永远等一个不存在的回复。
    stale    发过消息，状态还停在「待投」→ 跟进板不会催它，那条线就断在这儿了。

    **不自动修。** 修要判断哪一边是真的：可能状态错了，也可能只是当时没把消息记进来。
    自动改任一边，都会把「当时到底发生了什么」永久抹掉。
    """
    out: list[Anomaly] = []
    sent_states = (GREETED, REPLIED, INTERVIEW)
    for job in db.query(Job).all():
        th = thread(db, job)
        has_out = any(t.direction == "out" for t in th)
        has_in = any(t.direction == "in" for t in th)
        if job.status in sent_states and not has_out:
            out.append(Anomaly(job, "phantom", "状态说已投递，但会话里没有你发出去的消息"))
        elif job.status in (NEW, SCORED, BLOCKED) and has_out:
            out.append(Anomaly(job, "stale", "发过消息，状态却还停在「没投」"))
        elif job.status == REPLIED and not has_in:
            out.append(Anomaly(job, "phantom", "状态说对方回了，但会话里没有对方的消息"))
    return out
