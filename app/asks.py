"""向 HR 提问的话术：缺什么，就问什么。

和 greeter 的分工必须说清，否则很容易写成第二套招呼语：

- greeter 解决「怎么让对方**愿意回你**」，那是表达问题，要模型；
- asks 解决「问什么才能**把这条岗定下来**」，那是事实问题，所以**不用模型**。

待确认队列里的岗位不缺卖点，缺的是 JD 从来不写、规则也推不出来的事实：
每周到底到岗几天、能不能开实习证明。这两件事只能问，而且必须问得短——
Boss 上第一条消息要是三段客套，HR 划走就没了。

问题清单由**命中的规则**决定（见 `_QUESTIONS`），不是写死三问。
以后规则库多一条 gap，这里自动多一问，不用改话术代码。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import checker
from .rules import Finding


@dataclass
class Ask:
    content: str
    questions: list[str] = field(default_factory=list)
    why: list[str] = field(default_factory=list)        # 每条为什么要问，给用户看，不发给 HR
    issues: list[checker.Issue] = field(default_factory=list)
    ok: bool = True
    verdict_text: str = ""


def _cn_date(raw: str, *, with_year: bool = False) -> str:
    """2026-11-23 → 11月23日（或 2026年11月23日）。解析不出来就返回空串，不显示。"""
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", (raw or "").strip())
    if not m:
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{y}年{mo}月{d}日" if with_year else f"{mo}月{d}日"


# 规则 → 要问的问题 + 为什么要问。
# key 是 rule_id，这样问题清单永远跟着规则库走，不会出现「问了规则没提的事」。
_QUESTIONS: dict[str, tuple[str, str]] = {
    "WEEKEND_UNKNOWN": (
        "每周需要到岗几天？周末是否双休？",
        "JD 没写工时——这是唯一必须问 HR 才能定下来的事。",
    ),
}

_WHY_WEEKEND_DETAIL = (
    "注意「双休」不等于「五天」：3 天/周也是双休，但按那个天数凑满 40 个打卡工作日"
    "要十几周。所以要连着问「每周几天」，不要只问「是不是双休」。"
)

_WHY_CERT = "实习证明这条 JD 从来不写，但它是你凑够 40 个打卡工作日的前提，必须在到岗前确认。"


def build(job, profile, pref, findings: list[Finding]) -> Ask:
    """按命中的 gap 生成一段可直接发给 HR 的问询。纯函数，可复现。"""
    ids = {f.rule_id for f in findings}

    questions: list[str] = []
    why: list[str] = []

    # 触发条件只看「有没有只能问 HR 的缺口」。实习证明虽然人人要问，但它是
    # 附加项不是触发项——拿它当理由给每条岗都生成一段话术，这个功能就退化成
    # 「话术模板库」，而不是「缺什么问什么」。
    if not any(rule_id in ids for rule_id in _QUESTIONS):
        return Ask(content="", ok=False,
                   verdict_text="这条岗没有「只能问 HR」的缺口，不用硬凑话术")

    for rule_id, (question, reason) in _QUESTIONS.items():
        if rule_id in ids:
            questions.append(question)
            why.append(reason)

    if "WEEKEND_UNKNOWN" in ids:
        why.append(_WHY_WEEKEND_DETAIL)

    if getattr(pref, "must_certificate", False):
        questions.append("实习期满可以开实习证明吗？")
        why.append(_WHY_CERT)

    # 异地岗才问面试形式。同城问了显得没诚意，也浪费一次开口机会
    cities = list(getattr(pref, "cities_json", None) or [])
    if (getattr(pref, "must_remote_interview", False) and cities
            and job.city and job.city not in cities):
        questions.append("可以远程或视频面试吗？")
        why.append(f"岗位在{job.city}，你不在当地，先确认面试形式能省一趟路。")

    # 一问都没有就别生成——那说明这条岗压根不该进 gap，是判据出了问题
    if not questions:
        return Ask(content="", ok=False,
                   verdict_text="没抓到需要问的事，这条可能不该进待确认队列")

    major = (profile.major if profile else "") or ""
    degree = (profile.degree if profile else "") or ""
    grad_year = (profile.edu_end[:4] if profile and profile.edu_end else "")
    start_raw = getattr(pref, "available_from", "")
    end_raw = getattr(pref, "available_to", "")
    start = _cn_date(start_raw)
    # 起止跨年就带年份，否则「做到 2 月 28 日」会被读成今年二月
    end = _cn_date(end_raw, with_year=bool(start_raw and end_raw and start_raw[:4] != end_raw[:4]))

    head = f"你好，我是{major}专业 {grad_year} 届{degree}，"
    if start:
        head += f"{start}可以到岗"
        head += f"，能连续做到{end}。" if end else "。"
    else:
        head += "看到这个岗位想了解一下。"

    # 只问一件事时不要写「两件事」——数量词对不上会让对方以为漏看了
    if len(questions) == 1:
        body = f"想先确认一件事：\n{questions[0]}"
    else:
        body = "想先确认两件事：" if len(questions) == 2 else f"想先确认 {len(questions)} 件事："
        body += "\n" + "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))

    content = f"{head}\n\n看到「{job.title}」这个岗位，{body}\n\n麻烦您了。"

    # 事实回溯：这段话里只该出现档案里有的数字（届别、到岗日期）。
    # 模板生成的东西理论上不会编，但校验的成本是零，漏掉一次就是硬伤。
    facts = " ".join(x for x in (
        major, degree, grad_year, getattr(pref, "available_from", ""),
        getattr(pref, "available_to", ""), start, end,
        (profile.name if profile else ""),
    ) if x)
    issues = checker.check_text(content, facts)
    ok, msg = checker.verdict(issues)

    return Ask(content=content, questions=questions, why=why,
               issues=issues, ok=ok, verdict_text=msg)
