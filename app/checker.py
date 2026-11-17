"""事实回溯校验（第一道，纯规则）。

这个文件是整个项目里我最想拿来讲的部分。

**为什么要做这个：** 让模型写简历或打招呼，它会给你的简历上添一个"校级奖学金"、
把"参与"写成"主导"、把"了解"写成"精通"。这不是模型坏，是它天然倾向于
生成"听起来合理"的内容。而求职材料造假是硬伤——面试官一句"你说的这个项目
能展开讲讲吗"就穿了。

**为什么放在代码层：** 只靠提示词说"不许编造"没有用。提示词是软约束，
模型的遵从率大概九成，漏的那一成恰好会出现在你最不希望它出现的地方。
所以提示词里写一遍，代码里再硬校验一遍。两道都过才允许导出。

**为什么用规则而不是再调一次模型：** 这是第一道，要的是确定性、零延迟、离线可跑。
语义层面的过度概括交给第二道（prompts.VERIFY_SYSTEM）。规则抓数字和专有名词，
模型抓语义漂移，两者互补。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 无锚点形容词。这些词单独出现不构成事实，但堆起来会让 HR 觉得是模板。
# 注意：只提示，不阻断。删掉这些词是写法问题，不是诚信问题。
_VAGUE_PHRASES = [
    "学习能力强", "沟通能力强", "表达能力强", "吃苦耐劳", "认真负责",
    "积极主动", "团队精神", "抗压能力强", "责任心强", "踏实肯干",
    "有较强的", "有着丰富", "经验丰富", "能力突出", "表现优异", "成绩优异",
    "品学兼优", "全面发展", "自我驱动",
]

# 学校层次相关表述。触发即阻断：用户明确要求不在开场白里谈学校层次。
_SCHOOL_LEVEL = [
    "二本", "一本", "双非", "985", "211", "普通本科", "末流", "三本",
    "重点大学", "名校", "排名", "层次不高", "学校一般", "虽非名校",
]

# 证书类关键词。内容里提到就必须在档案里找到依据。
_CERT_KEYWORDS = [
    "CET-4", "CET-6", "四级", "六级", "雅思", "托福", "普通话", "驾驶证",
    "驾照", "C1", "训练师", "工程师证", "资格证", "证书", "认证",
]

# 「数字 + 量词」：这类组合带信息量，值得逐个核对。
# 纯序数（第一/第二）和年份不在此列，避免噪声。
_COUNTED = re.compile(
    r"(\d+(?:\.\d+)?)\s*(个|条|次|项|秒|分钟|小时|天|周|个月|年|人|%|分|万|千)"
)
_CN_NUM = {"两": "2", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6",
           "七": "7", "八": "8", "九": "9", "十": "10"}
_CN_COUNTED = re.compile(r"([两二三四五六七八九十])\s*(个|条|次|项|秒|分钟|小时|天|周|个月|年|人|分)")


@dataclass
class Issue:
    kind: str      # unknown_number / unknown_cert / vague_phrase / school_level
    level: str     # block / warn
    text: str      # 命中的原文片段
    detail: str


def _numeric_tokens(content: str) -> list[tuple[str, str]]:
    """抽出内容里的「数字+量词」，返回 (原始片段, 数字串)。"""
    out: list[tuple[str, str]] = []
    for m in _COUNTED.finditer(content):
        out.append((m.group(0), m.group(1)))
    for m in _CN_COUNTED.finditer(content):
        num = _CN_NUM.get(m.group(1), "")
        if num:
            out.append((m.group(0), num))
    return out


def check_text(content: str, facts: str) -> list[Issue]:
    """对照档案文本做规则核查。

    facts 是把档案拼成的一段纯文本（见 profile_to_text）。
    数字只要在档案里出现过就算通过——不做上下文语义比对，
    因为那需要模型，而这里要的是零成本、零误报的第二意见。
    """
    issues: list[Issue] = []
    low_facts = facts.lower()

    # 1. 数字：内容里新出现的数字，档案里没有 —— 最可能是编的
    for fragment, num in _numeric_tokens(content):
        if num not in facts:
            issues.append(Issue(
                kind="unknown_number",
                level="block",
                text=fragment,
                detail=f"数字「{num}」在档案里找不到依据，可能是模型编的，请核实后删除或补进档案",
            ))

    # 2. 证书：提到证书但档案里没有
    for kw in _CERT_KEYWORDS:
        if kw in content and kw.lower() not in low_facts:
            issues.append(Issue(
                kind="unknown_cert",
                level="block",
                text=kw,
                detail=f"提到了「{kw}」，但档案里没有这项证书",
            ))

    # 3. 学校层次：用户明确要求不谈
    for kw in _SCHOOL_LEVEL:
        if kw in content:
            issues.append(Issue(
                kind="school_level",
                level="block",
                text=kw,
                detail="开场白里不谈学校层次（本人要求）。这段信息交给简历去承担",
            ))

    # 4. 空话：只提示
    for kw in _VAGUE_PHRASES:
        if kw in content:
            issues.append(Issue(
                kind="vague_phrase",
                level="warn",
                text=kw,
                detail="无锚点表述，建议替换成可核查的行为或数字",
            ))

    # 去重（同一片段可能被多条规则命中）
    seen: set[tuple[str, str]] = set()
    uniq: list[Issue] = []
    for it in issues:
        key = (it.kind, it.text)
        if key not in seen:
            seen.add(key)
            uniq.append(it)
    return uniq


def check_used_facts(used_facts: list[str], allowed_texts: list[str]) -> list[Issue]:
    """模型自报「我引用了这些事实」，逐条核对是否真在档案里。

    allowed_texts 只应包含 verified=True 的素材，所以要传已过滤的列表。
    这一步抓的是「模型把档案里没有的东西写进 used_facts」——
    反过来也说明它可能在正文里用了别处的知识。
    """
    issues: list[Issue] = []
    haystack = "\n".join(allowed_texts).lower()
    for fact in used_facts:
        f = (fact or "").strip()
        if not f:
            continue
        # 取断言里最长的一个片段去比对，避免整句精确定位不上
        probe = max(re.findall(r"[\u4e00-\u9fa5A-Za-z0-9\-\.]{4,}", f) or [""], key=len)
        if probe and probe.lower() not in haystack:
            issues.append(Issue(
                kind="unknown_fact",
                level="block",
                text=f[:60],
                detail="模型声称引用了这条事实，但档案里找不到对应内容",
            ))
    return issues


def verdict(issues: list[Issue]) -> tuple[bool, str]:
    """返回 (是否通过, 说明)。任何 block 级问题都不通过。"""
    blocks = [i for i in issues if i.level == "block"]
    warns = [i for i in issues if i.level == "warn"]
    if blocks:
        return False, f"有 {len(blocks)} 处事实性问题必须先处理：{blocks[0].detail}"
    if warns:
        return True, f"通过，但有 {len(warns)} 处表述建议改写"
    return True, "通过"
