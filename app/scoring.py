"""评估落库。从 main.py 抽出来，因为抓取 CLI 也要用同一条评分链路。

抽出来的理由不是「代码整洁」，是**一致性**：如果 CLI 入库和网页入库各有一套
打分逻辑，那么同一个职位从哪进来就会得到两个分数，界面上看起来像 bug。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from . import greeter, matcher, review, rules
from .models import Job, Match, Preference, Profile, Review


def get_profile(db: Session) -> Profile | None:
    return db.query(Profile).first()


def get_pref(db: Session, profile: Profile) -> Preference:
    pref = db.query(Preference).filter(Preference.profile_id == profile.id).first()
    if pref is None:
        pref = Preference(profile_id=profile.id)
        db.add(pref)
        db.commit()
    return pref


def months_available(pref: Preference) -> int:
    """按到岗时间与结束时间粗算可提供月数。"""

    def _d(s: str) -> date | None:
        try:
            y, m, d = (int(x) for x in s.split("-"))
            return date(y, m, d)
        except (ValueError, AttributeError):
            return None

    a, b = _d(pref.available_from), _d(pref.available_to)
    if not a or not b:
        return 3
    return max(1, round((b - a).days / 30))


def _schedule_text(job: Job) -> str:
    """把工时还原成规则认得的写法（「6天/周 6个月」）。

    Job 里存的是拆开的两个整数，规则里只认文本。中间这一层转换放在这里，
    而不是给 rules 加两个数字参数——规则库是「读文本」的，不该认识 ORM。

    三处来源，优先级从高到低：采信过的 HR 回复 > 平台字段 > JD 正文（在 text 里）。
    HR 排在最前，是因为它是唯一能覆盖平台标注的：平台写 6 天/周、HR 口头说双休，
    以谁为准不该由代码决定，但**一旦人采信了 HR，打分就必须跟着走**——
    否则采信按钮等于没生效，而那种「点了没反应」的按钮最难查。
    """
    parts = []
    facts = job.hr_facts_json or {}
    hr_days = (facts.get("days_per_week") or {}).get("value")
    days = hr_days if hr_days else job.days_per_week
    if days:
        parts.append(f"{days}天/周")
    if job.months_req:
        parts.append(f"{job.months_req}个月")
    return " ".join(parts)


def score_only(db: Session, job: Job) -> "matcher.MatchResult":
    """只算分不落库。CLI 的 --dry-run 用它，这样试跑一次不会污染库。

    人工判断在这里注入：`review.resolve()` 给出「哪几条规则被推翻了」以及
    「人是否直接否决」。**不把 override 写进 Match 记录**，是为了让「重新评估」
    不会把人的判断冲掉——重算每天都在发生，人工结论不能只活到下一次重算。
    """
    profile = get_profile(db)
    if profile is None:
        raise RuntimeError("档案还没初始化：先跑一次 `python -m app` 或填 data/profile.json")

    pref = get_pref(db, profile)
    verdict = review.resolve(db, job.id)
    return matcher.evaluate(
        job.jd_text,
        title=job.title,
        company=job.company,
        edu_req=job.edu_req,
        exp_req=job.exp_req,
        schedule=_schedule_text(job),
        months_available=months_available(pref),
        can_remote=bool(pref.remote_available_to),
        capabilities=greeter.build_capabilities(list(profile.items)),
        ignored_rules=verdict.ignored,
        manual_block_reason=verdict.manual_block_reason,
    )


def evaluate_job(db: Session, job: Job) -> Match:
    """跑一次评估并落库。新增职位、手动重算、抓取入库都走这里。"""
    result = score_only(db, job)
    m = Match(
        job_id=job.id,
        score=result.score,
        dimension_scores_json=result.to_dict()["dimensions"],
        blocked_reason=result.blocked_reason,
        reason_text=result.reason_text,
    )
    db.add(m)
    # 「已投递之后」是历史事实，不该被一次重算抹掉。
    # 场景：HR 回话说每周 6 天，采信后规则判阻断——但消息已经发出去了，
    # 把状态从 replied 改成 blocked 等于假装没投过，跟进板上这条会凭空消失。
    # 所以阻断只改**还没发出去**的岗位的状态；已经发出去的，阻断信息留在
    # Match.blocked_reason 里（详情页照样显示），状态由 pipeline 决定。
    # 这里用字面量而不是 import pipeline：scoring 是被 pipeline 依赖的下层，
    # 反过来 import 会绕成环，而且状态名是稳定的对外契约。
    delivered = ("greeted", "replied", "interview", "offer", "rejected", "ghosted")
    if result.blocked and job.status not in delivered:
        job.status = "blocked"
    elif not result.blocked and job.status in ("new", "scored", "blocked"):
        # 撤销人工否决后要能退回 scored，否则状态会永远停在 blocked，
        # 而分数已经恢复——界面上的标记和分数互相打脸
        job.status = "scored"
    db.commit()
    # 必须 refresh：matches 是 lazy="selectin" 的集合，一旦访问过就缓存在 job 上，
    # 之后再 add 新 Match，`job.latest_match` 仍然读到旧的最后一条。
    # 单测里表现为「改判后分数没变」，线上表现为「点了重新评估，页面还是旧分」——
    # 请求级 session 掩盖了这个问题，同一个 session 里连续评估两次才会露出来。
    db.refresh(job)
    m._findings = result.findings  # type: ignore[attr-defined]
    return m


# ---------------------------------------------------------------- 人工确认


def findings_for(db: Session, job: Job) -> list[rules.Finding]:
    """规则命中。**不做 override 过滤**——展示与人工确认都要看到全部命中。"""
    profile = get_profile(db)
    months = months_available(get_pref(db, profile)) if profile else 3
    return rules.analyze(
        job.jd_text, title=job.title, company=job.company, months_available=months,
        schedule=_schedule_text(job),
    )


def review_queue(db: Session, limit: int = 20) -> tuple[list[review.ReviewItem], int]:
    """待人工确认的岗位，按误判代价排序。返回 (前 limit 条, 总数)。

    已经人工处理过的（最新一条判断不是 reset）不再进队列——同一条不该反复问。
    一次把所有 reviews 读进内存再按 job 归并，是为了避免 N 条职位 N 次查询。
    """
    profile = get_profile(db)
    if profile is None:
        return [], 0
    months = months_available(get_pref(db, profile))

    handled: set[int] = set()
    for rv in db.query(Review).order_by(Review.id).all():
        if rv.decision == review.RESET:
            handled.discard(rv.job_id)      # 撤销 = 回到待确认
        else:
            handled.add(rv.job_id)

    items: list[review.ReviewItem] = []
    for job in db.query(Job).order_by(Job.id.desc()).all():
        if job.id in handled:
            continue
        m = job.latest_match
        if m is None:
            continue
        dims = m.dimension_scores_json or {}
        direction = float((dims.get("direction") or {}).get("score") or 0.0)
        findings = rules.analyze(
            job.jd_text,
            title=job.title,
            company=job.company,
            months_available=months,
            # schedule 必须一起传：平台把「3天/周」「6天/周」放在字段里而不是正文里，
            # 漏传就会把「平台上写着的工时」判成「JD 未写工作时间」，
            # 于是队列里冒出三条本该有结论的岗位。口径要和 findings_for 保持一致。
            schedule=_schedule_text(job),
        )
        item = review.build_item(job, findings, direction, round(m.score, 1), bool(m.blocked))
        if item is not None:
            items.append(item)

    items.sort(key=lambda it: (
        review.BUCKETS[it.bucket]["priority"], -it.direction, -(it.score or 0.0),
    ))
    return items[:limit], len(items)


def review_apply(
    db: Session, job: Job, decision: str, *, note: str = "", bucket: str = "",
) -> Review:
    """记一条人工判断，然后立刻按新规则重算。

    重算放在这里而不是路由里：否则「记了判断但没重算」会让界面分数和 reviews 表
    对不上，而这种不一致极难被发现——页面看着正常，只是数字旧了。

    decision=pass 时，被推翻的是**此刻全部命中**的规则 id。语义是「这条岗位的
    风险判断整体不成立」，而不是逐条勾选；规则以后新增的命中不在这个名单里，
    所以照样拦得住。
    """
    rule_ids = [f.rule_id for f in findings_for(db, job)] if decision == review.PASS else []
    rv = review.record(db, job, decision, rule_ids=rule_ids, bucket=bucket, note=note)
    evaluate_job(db, job)
    return rv
