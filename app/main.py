"""FastAPI 应用与路由。

P0 只做一条闭环：档案 → 粘贴 JD → 打分 → 生成话术 → 导出简历。
刻意不上 Vue / 不上前端构建，原因是单用户本地工具只有四个屏，
SPA 的收益抵不过「clone 下来要 npm install 五分钟」的成本——
DEMO 模式能不能一条命令跑起来，比技术栈是否时髦重要得多。
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import asks, config, greeter, pipeline, resume_docx, review, scoring
from . import fetch as fetch_jobs
from .db import SessionLocal, get_db, init_db
from .llm import get_client
from .models import (
    Alert, Greeting, Job, Match, Preference, Profile, ProfileItem, Resume, Review,
)
from .sources import boss_live
from .sources.base import FetchError, FetchSpec, NeedHumanError, SetupError


# ---------------------------------------------------------------- 启动


def _seed(db: Session) -> None:
    """首次启动初始化档案。

    优先读 data/profile.json（真实档案，已被 .gitignore 排除），
    没有则回落到 data/profile.example.json（脱敏示例，随仓库分发）。
    """
    if db.query(Profile).first():
        return
    for name in ("profile.json", "profile.example.json"):
        path = config.DATA_DIR / name
        if path.exists():
            break
    else:
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    p = Profile(**data["profile"])
    db.add(p)
    db.flush()
    for it in data["items"]:
        db.add(ProfileItem(profile_id=p.id, **it))
    db.add(Preference(profile_id=p.id, **data["preference"]))
    db.commit()


def _load_demo_jobs(db: Session) -> int:
    path = config.DEMO_DIR / "jobs.json"
    if not path.exists():
        return 0
    jobs = json.loads(path.read_text(encoding="utf-8"))
    n = 0
    for j in jobs:
        if db.query(Job).filter(Job.company == j["company"], Job.title == j["title"]).first():
            continue
        db.add(Job(**j))
        n += 1
    db.commit()
    return n


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        _seed(db)
        # 只在空库时灌示例数据。库里已经有真实抓取的职位时，DEMO=1 启动
        # 会把 7 条「示例·XX」混进真实投递列表里——示例数据是用来看的，不是用来混的。
        if config.DEMO_MODE and db.query(Job).count() == 0:
            _load_demo_jobs(db)
        # P3 之前 mark_sent 只改 Greeting、不写 Message。不补的话，那些岗位在跟进板上
        # 会显示成「一条消息都没有」，于是被当成「还没投」重新催一遍。
        pipeline.backfill_sent_greetings(db)
        # 启动时先扫一遍：服务是常驻的，但人不会一直开着页面。
        # 冷启动这次扫描保证「昨天发的招呼今天该催了」在第一次打开时就是准的。
        pipeline.raise_alerts(db)
    finally:
        db.close()
    yield


app = FastAPI(title="JobPilot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(config.TEMPLATE_DIR))


# ---------------------------------------------------------------- 工具


def _alert_count() -> int:
    """导航角标：现在挂着几条没处理的提醒。

    **只读，不在这里做检查。** 检查（raise_alerts）要写库，而这个函数被每个页面
    模板调用、跑在路由自己的事务里；另开一个连接去写同一张 SQLite，会在提交时
    等主连接释放读锁。本地工具卡五秒比角标不准难受得多。

    真正的检查放在三处：启动时、打开跟进页时、`python -m app.remind`。
    """
    try:
        db = SessionLocal()
        try:
            return db.query(Alert).filter(Alert.ack_at.is_(None)).count()
        finally:
            db.close()
    except Exception:  # noqa: BLE001  库还没建好时不该把整个页面拖挂
        return 0


def _ctx(request: Request, **kw) -> dict:
    base = {
        "request": request,
        "demo_mode": config.DEMO_MODE,
        "model_name": config.LLM_MODEL,
        "css_version": config.CSS_VERSION,
        "alert_count": _alert_count(),
    }
    base.update(kw)
    return base


# 评分链路在 scoring.py：网页入库和 CLI 抓取入库必须走同一条，否则同一个职位
# 从哪个入口进来会得到两个分数。
_get_profile = scoring.get_profile
_get_pref = scoring.get_pref


def _rescore(db: Session, job: Job) -> Match:
    return scoring.evaluate_job(db, job)


def _analysis_for_greeting(match: Match) -> str:
    d = match.dimension_scores_json or {}
    parts = [f"总分 {match.score:.0f}"]
    for key, val in d.items():
        if isinstance(val, dict):
            parts.append(f"{key}: {val.get('score')} 分 —— {val.get('reason', '')}")
    if match.blocked_reason:
        parts.append(f"硬约束：{match.blocked_reason}")
    return "\n".join(parts)


# ---------------------------------------------------------------- 页面


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    profile = _get_profile(db)
    jobs = db.query(Job).order_by(Job.id.desc()).all()
    total = len(jobs)
    scored = [j for j in jobs if j.latest_match]
    blocked = sum(1 for j in jobs if j.status == "blocked")
    good = sum(1 for j in scored if not j.latest_match.blocked and j.latest_match.score >= 60)
    # 「跟进中」按 Job.status 算，不按 Greeting 的已发标记算——
    # 后者是草稿版本上的一个勾，前者才是这条岗位真实的投递阶段。
    following = sum(1 for j in jobs if j.status in ("greeted", "replied", "interview"))
    # 只取总数：工作台要显示「有几条等你确认」，不需要那 20 条明细
    _, pending = scoring.review_queue(db, limit=0)
    # 「该催 / 等你回」只能靠时间账算，所以走一遍看板。35 条的量级，成本可忽略，
    # 换来的是首页那个数字和 /inbox 里的分组永远一致——两处各算一套必然会分叉。
    board = pipeline.board(db, profile=profile, pref=_get_pref(db, profile))
    pstats = pipeline.stats(board)
    # 首页也顺手做一次提醒检查：这是人一天里最可能打开的一页。
    alert_rows = pipeline.nudges(db)

    ok, msg = (False, "未检测")
    if not config.DEMO_MODE:
        try:
            ok, msg = get_client().ping()
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, f"{type(exc).__name__}: {exc}"

    return templates.TemplateResponse(request, "index.html", _ctx(
        request,
        profile=profile,
        stats={"total": total, "good": good, "blocked": blocked,
               "following": following, "pending": pending,
               "due_me": pstats["due_me"], "chase": pstats["chase"],
               "alerts": len(alert_rows)},
        nudges=alert_rows[:3],
        llm_ok=ok,
        llm_msg=msg,
        recent=jobs[:8],
    ))


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db)):
    profile = _get_profile(db)
    pref = _get_pref(db, profile) if profile else None
    items = list(profile.items) if profile else []
    return templates.TemplateResponse(request, "profile.html", _ctx(
        request, profile=profile, pref=pref, items=items,
    ))


@app.post("/profile")
async def profile_save(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    profile = _get_profile(db)
    if not profile:
        profile = Profile()
        db.add(profile)
        db.flush()

    scalar = [
        "name", "phone", "email", "birth", "ethnicity", "hometown", "city", "politics",
        "school", "college", "major", "degree", "edu_start", "edu_end",
    ]
    for f in scalar:
        if f in form:
            setattr(profile, f, str(form[f]).strip())

    pref = _get_pref(db, profile)
    for f in ("available_from", "available_to", "remote_available_to"):
        if f in form:
            setattr(pref, f, str(form[f]).strip())
    for f in ("must_weekend_off", "must_remote_interview", "must_certificate"):
        setattr(pref, f, f in form)

    db.commit()
    return RedirectResponse("/profile", status_code=303)


@app.post("/profile/item/{item_id}/verify")
def toggle_verify(item_id: int, db: Session = Depends(get_db)):
    item = db.get(ProfileItem, item_id)
    if item:
        item.verified = not item.verified
        db.commit()
    return RedirectResponse("/profile", status_code=303)


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, db: Session = Depends(get_db)):
    jobs = db.query(Job).order_by(Job.id.desc()).all()
    rows = []
    for j in jobs:
        m = j.latest_match
        rows.append({
            "job": j,
            "score": round(m.score, 1) if m else None,
            "blocked": bool(m and m.blocked),
            "blocked_reason": m.blocked_reason if m else "",
            "reason": m.reason_text if m else "尚未评估",
        })
    rows.sort(key=lambda r: (r["blocked"], -(r["score"] or 0)))
    return templates.TemplateResponse(request, "jobs.html", _ctx(request, rows=rows))


@app.post("/jobs/new")
async def job_new(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    jd = str(form.get("jd_text", "")).strip()
    title = str(form.get("title", "")).strip()
    if not jd or not title:
        return RedirectResponse("/jobs?err=missing", status_code=303)

    def _num(key: str) -> float | None:
        raw = str(form.get(key, "")).strip()
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    job = Job(
        company=str(form.get("company", "")).strip(),
        title=title,
        city=str(form.get("city", "")).strip(),
        district=str(form.get("district", "")).strip(),
        salary_min=_num("salary_min"),
        salary_max=_num("salary_max"),
        salary_unit=str(form.get("salary_unit", "day")),
        edu_req=str(form.get("edu_req", "")).strip(),
        exp_req=str(form.get("exp_req", "")).strip(),
        days_per_week=int(_num("days_per_week") or 0) or None,
        months_req=int(_num("months_req") or 0) or None,
        jd_text=jd,
        hr_name=str(form.get("hr_name", "")).strip(),
        url=str(form.get("url", "")).strip(),
        source="manual",
        status="new",
    )
    db.add(job)
    db.commit()
    _rescore(db, job)
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@app.post("/jobs/{job_id}/rescore")
def job_rescore(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job:
        _rescore(db, job)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        return RedirectResponse("/jobs", status_code=303)
    m = job.latest_match
    if m is None:
        m = _rescore(db, job)

    dims = m.dimension_scores_json or {}
    # findings 不落库，重跑一次规则取证据（规则是纯函数，重算成本可忽略）。
    # 走 scoring.findings_for 而不是直接 rules.analyze：两处的 months_available
    # 必须一致，否则详情页会显示一条打分时根本没命中的「时长过长」。
    findings = scoring.findings_for(db, job)
    verdict = review.resolve(db, job.id)
    ignored_labels = [f.label for f in findings if f.rule_id in verdict.ignored]
    greetings = db.query(Greeting).filter(Greeting.job_id == job.id).order_by(Greeting.id.desc()).all()

    # 会话与可用动作：详情页是「投出去之后」唯一能操作的地方，
    # 跟进板只负责告诉你哪条该动，动手在详情页。
    turns = pipeline.thread(db, job)
    last_in = pipeline._last(turns, "in")
    return templates.TemplateResponse(request, "job_detail.html", _ctx(
        request,
        job=job,
        match=m,
        dims=dims,
        findings=findings,
        verdict=verdict,
        decision_cn=review.DECISION_CN,
        ignored=verdict.ignored,
        ignored_labels=ignored_labels,
        greetings=greetings,
        turns=turns,
        last_in=last_in,
        intent=pipeline.intent_of(last_in.content) if last_in else "",
        intent_cn=pipeline.INTENT_CN,
        facts=pipeline.extract(last_in.content) if last_in else [],
        hr_facts=job.hr_facts_json or {},
        clk=pipeline.clock(db, job),
        stage_cn=pipeline.STAGE_CN,
        next_stages=pipeline.stage_actions(job.status),
        draft=pipeline.draft_reply(
            job,
            pipeline.intent_of(last_in.content) if last_in else "",
            profile=_get_profile(db),
            pref=_get_pref(db, _get_profile(db)),
        ),
    ))


@app.get("/inbox", response_class=HTMLResponse)
def inbox_page(request: Request, db: Session = Depends(get_db)):
    profile = _get_profile(db)
    groups = pipeline.board(db, profile=profile, pref=_get_pref(db, profile))
    # nudges() 内部先 raise 再读，所以打开这一页就等于做了一次检查——
    # 这是「自动提醒」在你没挂定时任务时的兜底路径。
    rows = pipeline.nudges(db)
    return templates.TemplateResponse(request, "inbox.html", _ctx(
        request,
        groups=groups,
        stats=pipeline.stats(groups),
        nudges=rows,
        anomalies=pipeline.audit(db),
        intent_cn=pipeline.INTENT_CN,
        stage_cn=pipeline.STAGE_CN,
        followup_hours=pipeline.FOLLOWUP_HOURS,
        cold_hours=pipeline.COLD_HOURS,
        reply_due=pipeline.REPLY_DUE_HOURS,
        snooze_hours=pipeline.SNOOZE_HOURS,
    ))


@app.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: int, request: Request, db: Session = Depends(get_db)):
    """「知道了」。默认安静 12 小时（ALERT_SNOOZE_HOURS），不是永久静音。

    `?forever=1` 用于「这件事已经不存在了」，直接关死。
    """
    a = db.get(Alert, alert_id)
    if a is None or a.ack_at is not None:
        return RedirectResponse(_back(request, "/"), status_code=303)
    forever = request.query_params.get("forever") in ("1", "true", "yes")
    pipeline.ack_alert(db, a, hours=0 if forever else None)
    return RedirectResponse(_back(request, "/"), status_code=303)


def _back(request: Request, default: str) -> str:
    """回哪儿去。跟进板上的操作不该把人弹到详情页——40 条岗位一条条跳太累。"""
    ref = request.headers.get("referer") or ""
    return "/inbox" if "/inbox" in ref else default


@app.post("/jobs/{job_id}/reply")
def job_reply(
    job_id: int,
    request: Request,
    content: str = Form(...),
    db: Session = Depends(get_db),
):
    """把 HR 的回话粘进来。**粘贴是唯一的录入方式**——没有合规的自动抓取路径。

    记完之后状态自动推进到「对方已回」，因为这是一件客观发生过的事，
    不该再由谁点一次「标记为已回复」——要人手动维护的状态，三天后一定是错的。
    但回复里藏的事实**不会自动采信**：抽出来只是待确认项，见 /jobs/{id}/adopt。
    """
    job = db.get(Job, job_id)
    if not job or not content.strip():
        return RedirectResponse(_back(request, "/inbox"), status_code=303)
    pipeline.log_in(db, job, content)
    return RedirectResponse(_back(request, f"/jobs/{job.id}") + "#thread", status_code=303)


def _typed(key: str, raw: str):
    """表单值转成事实该有的类型。转不了就返回 None，让调用方拒绝——不猜。"""
    if key in ("days_per_week", "months_req"):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    if key == "certificate":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return raw


@app.post("/jobs/{job_id}/adopt")
def job_adopt(
    job_id: int,
    request: Request,
    key: str = Form(...),
    value: str = Form(...),
    message_id: int = Form(0),
    quote: str = Form(""),
    db: Session = Depends(get_db),
):
    """采信一条 HR 口述的事实，然后重算。

    为什么要单独一步而不是抽到就生效：HR 说「双休」和 JD 写着「单休」时，
    谁对不是代码能决定的。采信是判断，判断必须由人做出、并且**留下是听谁说的**。
    所以写进去的不是 `days_per_week=5`，而是「5 天/周，来自 12 号那条消息，原话是什么」。
    """
    job = db.get(Job, job_id)
    if not job:
        return RedirectResponse(_back(request, "/inbox"), status_code=303)
    typed = _typed(key, value)
    if typed is None:
        return RedirectResponse(_back(request, f"/jobs/{job.id}"), status_code=303)

    facts = dict(job.hr_facts_json or {})
    facts[key] = {
        "value": typed,
        "quote": (quote or "").strip()[:200],
        "message_id": message_id or None,
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    job.hr_facts_json = facts
    db.commit()
    # 采信完立刻重算：不重算的话页面分数还是旧的，而「点了按钮分数不动」
    # 是这个项目里最难查的一类 bug（P2 已经踩过一次）。
    _rescore(db, job)
    return RedirectResponse(_back(request, f"/jobs/{job.id}") + "#thread", status_code=303)


@app.post("/jobs/{job_id}/adopt/forget")
def job_adopt_forget(
    job_id: int,
    request: Request,
    key: str = Form(...),
    db: Session = Depends(get_db),
):
    """撤销采信，回到只看 JD 和平台字段。"""
    job = db.get(Job, job_id)
    if not job:
        return RedirectResponse(_back(request, "/inbox"), status_code=303)
    facts = dict(job.hr_facts_json or {})
    if facts.pop(key, None) is not None:
        job.hr_facts_json = facts
        db.commit()
        _rescore(db, job)
    return RedirectResponse(_back(request, f"/jobs/{job.id}") + "#thread", status_code=303)


@app.post("/jobs/{job_id}/stage")
def job_stage(
    job_id: int,
    request: Request,
    stage: str = Form(...),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if job:
        # 不合法的转移静默拒绝：界面上按钮本来只给合法的那些，能走到这儿
        # 多半是旧页面重放，把人留在原处比抛 500 好。
        pipeline.set_stage(db, job, stage)
    return RedirectResponse(_back(request, f"/jobs/{job.id}") + "#thread", status_code=303)


@app.post("/jobs/{job_id}/jd")
async def job_update_jd(job_id: int, request: Request, db: Session = Depends(get_db)):
    """补录/修正 JD 正文。抓取只拿到列表页时，这是把分数救回来的唯一入口。"""
    job = db.get(Job, job_id)
    if not job:
        return RedirectResponse("/jobs", status_code=303)
    form = await request.form()
    jd = str(form.get("jd_text", "")).strip()
    if jd and jd != job.jd_text:
        job.jd_text = jd
        db.commit()
        # 人工判断是**对着旧正文**下的结论：换成新 JD 之后，那条「我认了」
        # 不再有任何依据（新正文里可能写着单休）。所以记一条 reset 让这条岗位
        # 重新回到待确认——旧判断留在库里，翻得到，但不生效。
        verdict = review.resolve(db, job.id)
        if verdict.review is not None:
            review.record(db, job, review.RESET,
                          note="JD 正文已更新，原先的人工判断作废")
        _rescore(db, job)
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


# ---------------------------------------------------------------- 人工确认

REVIEW_LIMIT = 20


@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request, ask: int | None = None, db: Session = Depends(get_db)):
    items, total = scoring.review_queue(db, limit=REVIEW_LIMIT)

    # 「问 HR」话术是按需生成的，不落库：它由规则命中项决定，纯函数、可复现，
    # 存下来反而会和后来的规则改动不一致。?ask=<job_id> 只决定这一页显示哪一条。
    ask_panel = None
    ask_job = None
    if ask:
        job = db.get(Job, ask)
        profile = scoring.get_profile(db)
        if job and profile is not None:
            ask_job = job
            ask_panel = asks.build(
                job, profile, scoring.get_pref(db, profile),
                scoring.findings_for(db, job),
            )

    groups = []
    for key, meta in review.BUCKETS.items():
        rows = [it for it in items if it.bucket == key]
        if rows:
            groups.append({"key": key, "cn": meta["cn"], "why": meta["why"], "rows": rows})

    done = (
        db.query(Review)
        .filter(review.Review.decision.in_((review.PASS, review.BLOCK, review.ACK)))
        .order_by(review.Review.id.desc())
        .limit(12)
        .all()
    )
    done_rows = [{"rv": rv, "job": db.get(Job, rv.job_id)} for rv in done]
    overturned = (
        db.query(Review).filter(review.Review.decision == review.PASS).count()
    )

    # 「等回复」单独一列：它们已经不在待确认队列里了，但事没完——工时还欠 HR 一句话。
    # 只取每岗最新一条，被后续判断或 reset 覆盖的不该还挂在这里。
    newest: dict[int, Review] = {}
    for rv in db.query(Review).order_by(Review.id).all():
        newest[rv.job_id] = rv
    waiting = []
    for rv in (r for r in newest.values() if r.decision == review.WAIT):
        job = db.get(Job, rv.job_id)
        if job is None:
            continue
        days = max(0, (datetime.now() - rv.at).days) if rv.at else 0
        waiting.append({"rv": rv, "job": job, "days": days})
    # 按分数排，不按等待时长：等得久不代表重要，分数高才代表值得先催。
    waiting.sort(key=lambda w: -((w["job"].latest_match.score if w["job"].latest_match else 0.0)))

    return templates.TemplateResponse(request, "review.html", _ctx(
        request,
        groups=groups,
        total=total,
        shown=len(items),
        limit=REVIEW_LIMIT,
        done_rows=done_rows,
        done=len(done_rows),
        overturned=overturned,
        decision_cn=review.DECISION_CN,
        ask_panel=ask_panel,
        ask_job_id=ask_job.id if ask_job else None,
        waiting=waiting,
    ))


@app.post("/review/{job_id}")
def review_decide(
    job_id: int,
    decision: str = Form(...),
    note: str = Form(""),
    bucket: str = Form(""),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if not job:
        return RedirectResponse("/review", status_code=303)
    if decision in (review.PASS, review.BLOCK, review.ACK, review.WAIT):
        scoring.review_apply(db, job, decision, note=note, bucket=bucket)
    return RedirectResponse("/review", status_code=303)


@app.post("/review/{job_id}/reset")
def review_reset(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        return RedirectResponse("/review", status_code=303)
    scoring.review_apply(db, job, review.RESET)
    # 撤销后回到待确认队列，就直接带人回去；从详情页撤销的也回详情页更顺手
    back = request.headers.get("referer") or ""
    return RedirectResponse(f"/jobs/{job.id}" if f"/jobs/{job.id}" in back else "/review",
                            status_code=303)


@app.post("/jobs/{job_id}/greet")
def job_greet(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        return RedirectResponse("/jobs", status_code=303)
    profile = _get_profile(db)
    pref = _get_pref(db, profile)
    m = job.latest_match

    result = greeter.generate(
        job, profile, list(profile.items), pref,
        analysis_text=_analysis_for_greeting(m) if m else "",
    )

    # 清掉同一个 job 之前未发送的草稿，避免列表越滚越长
    db.query(Greeting).filter(Greeting.job_id == job.id, Greeting.is_sent.is_(False)).delete()

    for i, v in enumerate(result.variants, start=1):
        db.add(Greeting(
            job_id=job.id,
            content=v.content,
            rationale=v.rationale + (
                f"\n\n【校验】{v.verdict_text}" if v.verdict_text else ""
            ),
            model=result.model or ("template" if result.degraded else config.LLM_MODEL),
            variant=i,
            is_sent=False,
            confirmed_by_user=False,
        ))
    if job.status in ("new", "scored"):
        job.status = "greeted"
    db.commit()
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@app.post("/greetings/{greeting_id}/mark_sent")
def mark_sent(greeting_id: int, db: Session = Depends(get_db)):
    g = db.get(Greeting, greeting_id)
    if not g:
        return RedirectResponse("/jobs", status_code=303)
    g.is_sent = True
    g.confirmed_by_user = True
    g.sent_at = datetime.now()
    # 会话流是「投了什么、什么时候投的」的唯一真相。Greeting 只是草稿版本，
    # 发出去那一刻它就变成一条消息——同一句话存两份，两份会各自漂移。
    if g.job is None:
        db.commit()
        return RedirectResponse("/jobs", status_code=303)
    pipeline.log_out(db, g.job, g.content, greeting_id=g.id)
    # 用状态机判断能不能推到「已发等回」：对方已经回过你（replied）时，
    # 你再发一条，语义上又回到「等对方」，所以这条转移是合法的。
    if pipeline.can_go(g.job.status, pipeline.GREETED):
        g.job.status = pipeline.GREETED
    db.commit()
    return RedirectResponse(f"/jobs/{g.job_id}", status_code=303)


@app.post("/jobs/{job_id}/resume")
def job_resume(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    profile = _get_profile(db)
    if not profile:
        return RedirectResponse("/", status_code=303)
    path = resume_docx.build(profile, list(profile.items), job=job)
    db.add(Resume(job_id=job.id if job else None, file_path=str(path),
                  md_text=resume_docx.to_markdown(profile, list(profile.items), job=job),
                  used_items_json=[i.title for i in profile.items if i.verified]))
    db.commit()
    return FileResponse(
        path,
        filename=Path(path).name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.post("/resume")
def resume_plain(db: Session = Depends(get_db)):
    profile = _get_profile(db)
    if not profile:
        return RedirectResponse("/", status_code=303)
    path = resume_docx.build(profile, list(profile.items))
    return FileResponse(
        path,
        filename=Path(path).name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.post("/demo/load")
def demo_load(db: Session = Depends(get_db)):
    _load_demo_jobs(db)
    for j in db.query(Job).all():
        if j.latest_match is None:
            _rescore(db, j)
    return RedirectResponse("/jobs", status_code=303)


@app.get("/health")
def health():
    return {"ok": True, "demo": config.DEMO_MODE, "model": config.LLM_MODEL}


# ---------------------------------------------------------------- 抓取

FIXTURES = config.BASE_DIR / "fixtures" / "boss"


def _probe(cdp: str) -> tuple[bool, str]:
    """探测调试端口。不抛异常——页面要把失败原因显示出来，而不是 500。"""
    try:
        boss_live.probe(cdp)
        return True, ""
    except SetupError as exc:
        return False, str(exc).splitlines()[0]


def _salary_text(job: Job) -> str:
    if job.salary_min is None and job.salary_max is None:
        return "面议"
    unit = {"day": "元/天", "month": "元/月", "year": "元/年"}.get(job.salary_unit, "")
    lo = f"{job.salary_min:.0f}" if job.salary_min is not None else "?"
    hi = f"{job.salary_max:.0f}" if job.salary_max is not None else "?"
    return (f"{lo}" if lo == hi else f"{lo}-{hi}") + unit


def _row_view(job: Job, m) -> dict:
    return {
        "id": job.id,
        "company": job.company,
        "title": job.title,
        "score": round(m.score, 1) if m else None,
        "blocked": bool(getattr(m, "blocked", False)),
        "blocked_reason": m.blocked_reason if m else "",
        "has_jd": bool(job.jd_text.strip()),
        "place": f"{job.city}{job.district}",
        "salary": _salary_text(job),
    }


def _render_fetch(request: Request, **kw):
    cdp = kw.pop("cdp", boss_live.DEFAULT_CDP)
    ok, msg = _probe(cdp)
    params = {
        "cities": list(config.CITY_CODES.keys()),
        "cdp": cdp,
        "probe_ok": ok,
        "probe_msg": msg,
        "keyword": "",
        "city": "南昌",
        "pages": 1,
        "source": "snapshot" if config.DEMO_MODE else "live",
        # 默认进详情页：没有 JD 的职位分数上限只有 45，默认开才能看到真实判断
        "with_detail": True,
        "rows": [],
        "dup": 0,
        "error": "",
        "error_hint": "",
    }
    params.update(kw)
    return templates.TemplateResponse(request, "fetch.html", _ctx(request, **params))


@app.get("/fetch", response_class=HTMLResponse)
def fetch_page(request: Request):
    # DEMO 模式默认走快照，因为演示环境一定没有开着调试端口的浏览器
    return _render_fetch(request)


@app.post("/fetch")
def fetch_run(
    request: Request,
    keyword: str = Form(""),
    city: str = Form("南昌"),
    pages: int = Form(1),
    source: str = Form("live"),
    cdp: str = Form(""),
    with_detail: str | None = Form(None),
    db: Session = Depends(get_db),
):
    cdp = (cdp or "").strip() or boss_live.DEFAULT_CDP
    detail = bool(with_detail)
    state = dict(keyword=keyword, city=city, pages=pages, source=source,
                 cdp=cdp, with_detail=detail)

    try:
        if source == "snapshot":
            detail_paths = [FIXTURES / "detail_page.html"] if detail else None
            jobs = boss_live.fetch_from_snapshot(FIXTURES / "list_page.html", detail_paths)
        else:
            if not keyword.strip():
                raise ValueError("关键词不能为空")
            spec = FetchSpec(
                keyword=keyword.strip(),
                city=city,
                pages=max(1, min(3, pages)),
                with_detail=detail,
                limit=45,
            )
            jobs = fetch_jobs.collect(spec, cdp_url=cdp, verbose=False,
                                      skip_keys=fetch_jobs.known_keys())
    except SetupError as exc:
        return _render_fetch(request, error=str(exc), error_hint="", **state)
    except NeedHumanError as exc:
        return _render_fetch(request, error=str(exc),
                             error_hint="脚本不碰验证码——在浏览器里手动过验证，再点一次开始。", **state)
    except (FetchError, ValueError) as exc:
        return _render_fetch(request, error=str(exc), error_hint="", **state)

    if not jobs:
        return _render_fetch(request, error="没有解析到职位",
                             error_hint="换个关键词试试；若确定有结果，说明页面结构改了，用命令行 --dump 存一份快照。",
                             **state)

    try:
        rows, dup = fetch_jobs.ingest(jobs, db=db, dry=False)
    except RuntimeError as exc:
        return _render_fetch(request, error=str(exc), error_hint="先到「档案」页填一份再抓。", **state)

    return _render_fetch(request, rows=[_row_view(j, m) for j, m in rows], dup=dup, **state)
