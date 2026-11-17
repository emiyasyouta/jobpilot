"""招呼语生成。

职责边界很清楚：**这里只管表达，不管事实**。
事实由 checker 事后校验，判断由 matcher 事前给分。

有一个设计上必须坚持的点：**LLM 挂了要有降级路径**。
模板拼接出来的话术当然不如模型写的自然，但它是零依赖的——
DEMO 模式下不配 API key 也能出东西，用户断网也能继续干活。
很多 AI 工具把模型当成单点依赖，模型一挂整个产品就是白屏，
那是产品设计问题，不是模型问题。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from . import checker, prompts
from .llm import LLMClient, LLMError, get_client
from .matcher import Capability

_MAX_EXPORT_LEN = 600


@dataclass
class Variant:
    content: str
    rationale: str = ""
    issues: list[checker.Issue] = field(default_factory=list)
    ok: bool = True
    verdict_text: str = ""


@dataclass
class GreetingResult:
    variants: list[Variant]
    used_facts: list[str] = field(default_factory=list)
    pending_questions: list[str] = field(default_factory=list)
    degraded: bool = False       # True 表示没走模型，是模板兜底
    model: str = ""
    error: str = ""


# ---------------------------------------------------------------- 档案转文本


def profile_to_text(profile, items, *, only_verified: bool = True) -> str:
    """把 ORM 对象压成一段纯文本给模型看。

    只输出 verified 的素材——这是防编造的第一道闸门：
    模型看不到未经核实的素材，也就不会引用它。
    """
    lines = []
    if profile:
        lines.append(f"姓名：{profile.name}｜{profile.major}｜{profile.degree}｜"
                     f"{profile.edu_start}–{profile.edu_end} 在读")
        lines.append(f"学校：{profile.school} {profile.college}")
        if profile.city:
            lines.append(f"现居：{profile.city}")

    for it in items:
        if only_verified and not it.verified:
            continue
        c = it.content_json or {}
        head = f"[{it.type}] {it.title}"
        if it.org:
            head += f"｜{it.org}"
        if it.start_date:
            head += f"｜{it.start_date}–{it.end_date or '至今'}"
        lines.append(head)
        for key in ("pain", "approach", "result", "detail"):
            if c.get(key):
                label = {"pain": "问题", "approach": "做法", "result": "结果", "detail": "内容"}[key]
                lines.append(f"  {label}：{c[key]}")
    return "\n".join(lines)


def build_capabilities(items) -> list[Capability]:
    return [
        Capability(
            name=it.title,
            type=it.type,
            keywords=list((it.content_json or {}).get("keywords", [])),
            verified=it.verified,
        )
        for it in items
    ]


def allowed_texts(items) -> list[str]:
    """事实核查用的白名单：只有 verified 素材的文本。"""
    out = []
    for it in items:
        if not it.verified:
            continue
        c = it.content_json or {}
        out.append(it.title)
        out.append(it.org or "")
        out.extend(str(c.get(k, "")) for k in ("pain", "approach", "result", "detail"))
        out.extend(str(k) for k in c.get("keywords", []))
    return [t for t in out if t]


# ---------------------------------------------------------------- 降级模板


def _fmt_date(s: str) -> str:
    """把 2026-11-23 写成 11/23，招呼语里更自然。"""
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
        return f"{d.month}/{d.day}"
    except (ValueError, TypeError):
        return s or "随时"


def _fallback(job, profile, items, pref, analysis_text: str) -> GreetingResult:
    """不调模型的兜底版本。只用档案字段，不做任何推断。"""
    start = _fmt_date(getattr(pref, "available_from", "") or "")
    end = _fmt_date(getattr(pref, "available_to", "") or "")
    major = profile.major if profile else ""
    degree = profile.degree if profile else ""
    grad_year = (profile.edu_end[:4] if profile and profile.edu_end else "")

    # 挑最相关的一个项目：按 matcher 算过的命中素材优先
    caps = build_capabilities(items)
    cap_names = {c.name for c in caps if c.verified}
    best = None
    for it in items:
        if it.title in cap_names and it.type in ("project", "internship"):
            c = it.content_json or {}
            if c.get("approach"):
                best = it
                break

    proof = ""
    if best:
        c = best.content_json or {}
        approach = str(c.get("approach", "")).rstrip("。；;")
        result = str(c.get("result", "")).rstrip("。；;")
        if best.type == "internship":
            org = (best.org or "").split("·")[0].strip()
            lead = f"之前在{org}实习，" if org else "之前在实习中，"
        else:
            lead = f"之前做过{best.title}，"
        proof = f"\n\n{lead}{approach}"
        proof += f"，{result}。" if result else "。"

    # 句式按「像人在说话」而不是「像简历条目」来组织：
    # 先接一句对方的话头，再说时间，再给证据，最后落到要确认的事。
    body = (
        f"您好，看到在招「{job.title}」，补充一下我的情况。\n\n"
        f"我是{major}专业 {grad_year} 届{degree}，"
        f"{start} 可以到岗，能连续实习到 {end}。{proof}"
        f"\n\n想先确认三点：实习期是否双休？能否远程或视频面试？"
        f"实习结束能否开具实习证明？"
    ).strip()

    issues = checker.check_text(body, "\n".join(allowed_texts(items)))
    ok, msg = checker.verdict(issues)

    return GreetingResult(
        variants=[Variant(
            content=body,
            rationale="未调用模型，按档案字段直接拼接。不含任何推断性内容，可安全使用。",
            issues=issues,
            ok=ok,
            verdict_text=msg,
        )],
        used_facts=[],
        pending_questions=["是否双休", "能否远程面试", "能否开具实习证明"],
        degraded=True,
        model="template",
    )


# ---------------------------------------------------------------- 主入口


def generate(
    job,
    profile,
    items,
    pref,
    *,
    analysis_text: str = "",
    client: LLMClient | None = None,
) -> GreetingResult:
    """生成招呼语。模型失败自动降级，绝不把异常抛给界面。"""
    try:
        cli = client or get_client()
        user = prompts.GREETING_USER.format(
            profile=profile_to_text(profile, items),
            company=job.company,
            title=job.title,
            city=job.city,
            jd=job.jd_text[:4000],
            analysis=analysis_text or "（无）",
        )
        data = cli.chat_json(
            prompts.GREETING_SYSTEM,
            user,
            required_keys=("variants",),
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001
        res = _fallback(job, profile, items, pref, analysis_text)
        res.error = f"{type(exc).__name__}: {exc}"[:300]
        return res

    facts_text = "\n".join(allowed_texts(items))
    allow = allowed_texts(items)

    variants: list[Variant] = []
    raw_variants = data.get("variants") or []
    if not isinstance(raw_variants, list):
        raw_variants = []

    for v in raw_variants[:3]:
        if isinstance(v, str):
            content, rationale = v, ""
        elif isinstance(v, dict):
            content = str(v.get("content", "")).strip()
            rationale = str(v.get("rationale", "")).strip()
        else:
            continue
        if not content:
            continue
        if len(content) > _MAX_EXPORT_LEN:
            content = content[:_MAX_EXPORT_LEN]

        issues = checker.check_text(content, facts_text)
        ok, msg = checker.verdict(issues)
        variants.append(Variant(content=content, rationale=rationale,
                                issues=issues, ok=ok, verdict_text=msg))

    # 模型自报引用的素材也要核一遍
    used = data.get("used_facts") or []
    if isinstance(used, list):
        fact_issues = checker.check_used_facts([str(u) for u in used], allow)
        if fact_issues and variants:
            variants[0].issues.extend(fact_issues)
            variants[0].ok, variants[0].verdict_text = checker.verdict(variants[0].issues)

    if not variants:
        res = _fallback(job, profile, items, pref, analysis_text)
        res.error = "模型返回的 variants 为空，已降级到模板"
        return res

    pending = data.get("pending_questions") or []
    return GreetingResult(
        variants=variants,
        used_facts=[str(u) for u in used] if isinstance(used, list) else [],
        pending_questions=[str(q) for q in pending] if isinstance(pending, list) else [],
        degraded=False,
        model=getattr(client, "model", "") or "",
    )


def today_str() -> str:
    return date.today().isoformat()
