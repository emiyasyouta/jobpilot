"""抓取入口：把 BOSS 搜索结果变成库里的待投职位。

用法（三条路，从安全到方便排）

1. 离线复现——不联网、不开浏览器，验证解析器改对了没：
     python -m app.fetch --snapshot fixtures/boss/list_page.html --dry-run

2. 读你自己登录好的浏览器（推荐）：
     msedge.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\\edge-cdp"
     （在那个窗口里登录 BOSS，然后）
     python -m app.fetch -k "AI应用开发" --city 南昌 --pages 2

3. 只存快照不入库，用来抓一次真实结构、给解析器留下回归样本：
     python -m app.fetch -k "实习" --dump fixtures/boss/real

设计约束：脚本只读页面，不做登录、不碰验证码、不模拟点击「立即沟通」。
投递动作始终留给人——这既是合规要求，也是产品立场（见 README「边界」）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import config, scoring
from .db import SessionLocal, init_db
from .models import Job
from .sources import boss_live
from .sources.base import FetchError, FetchSpec, NeedHumanError, RawJob, SetupError

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _city_code(city: str) -> str:
    return config.CITY_CODES.get(city, "")


def _existing_keys(db) -> set[str]:
    keys = set()
    for j in db.query(Job).all():
        keys.add(RawJob(company=j.company, title=j.title, city=j.city, url=j.url).dedup_key())
    return keys


_DAYS_PER_WEEK = re.compile(r"(\d)\s*天\s*/\s*周")
_MONTHS_REQ = re.compile(r"(\d{1,2})\s*个月")


def _job_kwargs(rj: RawJob) -> dict:
    days = _DAYS_PER_WEEK.search(rj.schedule or "")
    months = _MONTHS_REQ.search(rj.schedule or "")
    return {
        "company": rj.company,
        "title": rj.title,
        "city": rj.city,
        "district": rj.district,
        "salary_min": rj.salary_min,
        "salary_max": rj.salary_max,
        "salary_unit": rj.salary_unit,
        "edu_req": rj.edu_req,
        "exp_req": rj.exp_req,
        "days_per_week": int(days.group(1)) if days else None,
        "months_req": int(months.group(1)) if months else None,
        "jd_text": rj.jd_text,
        "hr_name": rj.hr_name,
        "url": rj.url,
        "source": rj.source,
        "raw_json": {"salary_raw": rj.salary_raw, **rj.raw},
    }


def _print_report(rows: list[tuple[Job, object]], dup: int, dry: bool) -> None:
    blocked = [r for r in rows if getattr(r[1], "blocked", False)]
    open_rows = [r for r in rows if not getattr(r[1], "blocked", False)]
    open_rows.sort(key=lambda r: -(r[1].score if r[1] else 0))

    print()
    print(f"{DIM}新增 {len(rows)} 条，跳过重复 {dup} 条"
          f"{'（dry-run，未入库）' if dry else ''}{RESET}")

    no_jd = [r for r in rows if not r[0].jd_text.strip()]
    no_company = [r for r in rows if not r[0].company.strip()]
    if no_jd:
        print(f"{YELLOW}其中 {len(no_jd)} 条没有 JD 正文，分数仅供参考（上限 45）——"
              f"重跑时加 --detail 再抓一次{RESET}")
    if no_company:
        print(f"{YELLOW}其中 {len(no_company)} 条没解析到公司名，投递前请人工确认{RESET}")
    print()

    for job, m in open_rows:
        marks = []
        if not job.jd_text.strip():
            marks.append(f"{YELLOW}无JD{RESET}")
        if not job.company.strip():
            marks.append(f"{YELLOW}无公司{RESET}")
        suffix = ("  " + " ".join(marks)) if marks else ""
        if m is None:
            print(f"  {'--':>5}  {job.company or '（未知公司）'}  {job.title}{suffix}")
            continue
        print(f"  {m.score:5.1f}  {job.company or '（未知公司）'}  {job.title}"
              f"  {DIM}{job.city}{job.district}{RESET}{suffix}")
    for job, m in blocked:
        print(f"  {RED}阻断{RESET}  {job.company or '（未知公司）'}  {job.title}"
              f"  {DIM}{m.blocked_reason}{RESET}")


def _to_json(rows: list[tuple[Job, object]]) -> str:
    out = []
    for job, m in rows:
        out.append({
            "id": job.id, "company": job.company, "title": job.title,
            "city": job.city, "district": job.district,
            "salary": [job.salary_min, job.salary_max, job.salary_unit],
            "url": job.url,
            "score": round(m.score, 1) if m else None,
            "blocked": bool(getattr(m, "blocked", False)),
            "blocked_reason": m.blocked_reason if m else "",
            "has_jd": bool(job.jd_text),
        })
    return json.dumps(out, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 可复用入口
# CLI 和网页共用这两个函数。UI 不要自己再写一遍入库逻辑——
# 两条路径各写一份，迟早会出现「同一个职位在网页里 60 分、在 CLI 里 55 分」。


def collect(spec: FetchSpec, cdp_url: str = boss_live.DEFAULT_CDP,
            dump_dir: Path | None = None, verbose: bool = True,
            skip_keys: set[str] | None = None) -> list[RawJob]:
    """取数据。抓取失败抛 FetchError 子类，调用方决定怎么提示。"""
    return boss_live.fetch(
        spec,
        city_code=_city_code(spec.city),
        cdp_url=cdp_url,
        dump_dir=dump_dir,
        verbose=verbose,
        skip_keys=skip_keys,
    )


def known_keys() -> set[str]:
    """库里已有的去重键。单独一个函数，方便调用方在抓取前先算一次。"""
    init_db()
    db = SessionLocal()
    try:
        return _existing_keys(db)
    finally:
        db.close()


def ingest(jobs: list[RawJob], db=None, dry: bool = False) -> tuple[list[tuple[Job, object]], int]:
    """去重入库并打分。返回 ([(job, match)], 跳过条数)。"""
    init_db()
    own = db is None
    db = db or SessionLocal()
    try:
        existing = _existing_keys(db)
        rows: list[tuple[Job, object]] = []
        dup = 0
        for rj in jobs:
            key = rj.dedup_key()
            if key in existing:
                dup += 1
                continue
            existing.add(key)

            job = Job(**_job_kwargs(rj), status="new")
            if dry:
                m = scoring.score_only(db, job)
            else:
                db.add(job)
                db.commit()
                m = scoring.evaluate_job(db, job)
            rows.append((job, m))
        return rows, dup
    finally:
        if own:
            db.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.fetch",
        description="从 BOSS 直聘抓取职位并打分入库（只读页面，不自动投递）",
    )
    p.add_argument("-k", "--keyword", help="搜索关键词，如「AI应用开发 实习」")
    p.add_argument("--city", default="南昌", help="城市名，需在 config.CITY_CODES 里")
    p.add_argument("--pages", type=int, default=1, help="翻几页（每页约 15 条）")
    p.add_argument("--limit", type=int, default=30, help="最多入库多少条")
    p.add_argument("--no-detail", action="store_true", help="不进详情页，只用列表页信息（快但 JD 不全）")
    p.add_argument("--cdp", default=boss_live.DEFAULT_CDP, help="浏览器调试地址")
    p.add_argument("--dump", metavar="DIR", help="把抓到的 HTML 存到该目录，供离线复现")
    p.add_argument("--snapshot", metavar="HTML", help="离线：解析已保存的搜索结果页")
    p.add_argument("--snapshot-detail", metavar="HTML", action="append", default=[],
                   help="离线：配套的详情页快照，可多次指定")
    p.add_argument("--dry-run", action="store_true", help="只打分不入库")
    p.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    args = build_parser().parse_args(argv)

    # ---------- 取数据 ----------
    try:
        if args.snapshot:
            jobs = boss_live.fetch_from_snapshot(
                Path(args.snapshot), [Path(p) for p in args.snapshot_detail]
            )
            if not args.json:
                print(f"离线解析 {args.snapshot} → {len(jobs)} 条")
        else:
            if not args.keyword:
                print("需要 --keyword，或用 --snapshot 走离线模式")
                return 2
            spec = FetchSpec(
                keyword=args.keyword,
                city=args.city,
                pages=max(1, min(args.pages, config.CRAWL_MAX_PAGES_PER_RUN)),
                with_detail=not args.no_detail,
                limit=max(1, args.limit),
            )
            if not args.json:
                print(f"抓取「{spec.keyword}」· {spec.city} · {spec.pages} 页"
                      + (" · 含详情" if spec.with_detail else ""), flush=True)
            jobs = collect(
                spec,
                cdp_url=args.cdp,
                dump_dir=Path(args.dump) if args.dump else None,
                verbose=not args.json,
                skip_keys=known_keys(),
            )
    except SetupError as exc:
        print(f"{RED}环境没准备好{RESET}：{exc}", file=sys.stderr)
        return 3
    except NeedHumanError as exc:
        print(f"{YELLOW}需要人处理{RESET}：{exc}", file=sys.stderr)
        return 4
    except FetchError as exc:
        print(f"{RED}抓取失败{RESET}：{exc}", file=sys.stderr)
        return 5

    if not jobs:
        print("没有解析到职位。可能是关键词太窄，也可能页面结构改版了——"
              "用 --dump 存一份快照，再跑一次离线解析看看。")
        return 0

    # ---------- 入库 ----------
    rows, dup = ingest(jobs, dry=args.dry_run)

    if args.json:
        print(_to_json(rows))
    else:
        _print_report(rows, dup, args.dry_run)
        if not args.dry_run and rows:
            print(f"\n{DIM}下一步：python -m app  打开 http://127.0.0.1:8000/jobs 看打分详情{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
