"""用 CDP 接管「你自己已经登录好的浏览器」来读职位页。

为什么不自己起一个干净的浏览器去登录：
BOSS 的登录要么短信要么扫码，还要过滑块。脚本模拟登录等于同时踩风控和《网络安全法》
两条线，账号被封是小事，拿这个当作品集交付是大事。所以这里只做一件事——
**把真人已经打开、已经登录的页面读下来**，登录态、cookie、验证全都不碰。

使用前提（README 有完整命令）：Edge/Chrome 以 --remote-debugging-port=9222 启动，
并在那个窗口里手动登录 BOSS。底层用 `cdp.py` 直接说协议，不依赖 playwright。
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from urllib.parse import quote

from .. import config
from . import boss_html, cdp, dom
from .base import FetchError, FetchSpec, NeedHumanError, RawJob, SetupError

DEFAULT_CDP = config.CRAWL_CDP_URL
# 注意是 jobs（复数）。写成 job 时 BOSS 会重定向回首页，页面能打开、也带着登录态，
# 但一张职位卡片都不渲染——"能打开"和"是对的那个页面"是两件事，这个坑实测踩过一次。
# 末尾不带 page：搜索页的 ?page=N 实测不生效（第 2 页和第 1 页内容完全一样），
# 列表靠滚动加载，「翻页」在下面的循环里实现成「滚到够数」。
SEARCH_URL = "https://www.zhipin.com/web/geek/jobs?query={q}&city={city}"

# BOSS 搜索页首屏 15 条，之后每次滚动追加一批。用它把 --pages 换算成目标条数。
PER_PAGE = 15

# 渲染判据：这些选择器出现才说明「数据回来了」，不是「骨架到了」。
_CARD_SEL = "li.job-card-box"
_DETAIL_SEL = ".job-sec-text"

# 页面上出现这些字样说明撞墙了，不该硬闯。
# 刻意不收「登录后查看」——列表页底部常挂这句，收进来会把正常页面判成登录墙。
_WALL_HINTS = ("安全验证", "请完成验证", "拖动滑块", "操作过于频繁", "访问异常")


def probe(cdp_url: str = DEFAULT_CDP) -> dict:
    """确认调试端口活着。不 import 任何浏览器框架就能给出可读的报错。"""
    try:
        return cdp.version(cdp_url)
    except Exception as exc:  # noqa: BLE001
        raise SetupError(
            f"连不上 {cdp_url}：{type(exc).__name__}。\n"
            "先用这个命令起一个带调试端口的 Edge，并在里面登录 BOSS：\n"
            '  msedge.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\\edge-cdp"'
        ) from exc


def _head_text(html: str, limit: int = 1500) -> str:
    body = dom.parse(html).find(tag="body")
    return (body.text() if body else "")[:limit]


def _guard(html: str) -> None:
    """撞上验证码/风控就停，让人去处理。"""
    head = _head_text(html)
    for hint in _WALL_HINTS:
        if hint in head:
            raise NeedHumanError(
                f"页面出现「{hint}」。请在浏览器里手动过验证，然后重跑——脚本不碰验证码。"
            )


def _polite_pause() -> None:
    """节流。不是为了骗过风控，是为了不把人家服务器当压测机。"""
    time.sleep(random.uniform(*config.CRAWL_DELAY_RANGE))


def fetch(
    spec: FetchSpec,
    city_code: str = "",
    cdp_url: str = DEFAULT_CDP,
    dump_dir: Path | None = None,
    verbose: bool = True,
    skip_keys: set[str] | None = None,
) -> list[RawJob]:
    """完整抓取：搜索页滚到够数 → 逐条进详情页补 JD → 返回 RawJob 列表。

    「翻页」是滚出来的：BOSS 搜索页的 ?page 参数不生效，列表靠滚动追加。
    所以 spec.pages 被换算成目标条数，一次导航滚够即可——省下的不只是导航次数。

    dump_dir 给定时把 HTML 存下来，用于「站点改版后离线复现」。
    skip_keys 是库里已有的去重键：列表阶段就滤掉，省下的不是时间，是详情页请求数——
    重复跑一次不该把人家整站再问一遍。
    """
    probe(cdp_url)

    jobs: list[RawJob] = []
    target = max(PER_PAGE, spec.pages * PER_PAGE)
    url = SEARCH_URL.format(q=quote(spec.keyword), city=city_code)
    if verbose:
        print(f"  [列表] {url}（滚到 {target} 条）", flush=True)
    try:
        html = cdp.capture_html(cdp_url, url, expect=_CARD_SEL, target=target,
                                render_timeout=90)
    except cdp.CDPError as exc:
        text = str(exc)
        if "navigated" in text.lower() or "closed" in text.lower():
            raise NeedHumanError(
                "页面在读取过程中被跳转了，多半是反爬验证。"
                "请用正常窗口（不是无头模式）打开、手动过验证后再重跑。"
            ) from exc
        raise FetchError(f"取搜索页失败：{exc}") from exc

    _guard(html)
    if dump_dir:
        dump_dir.mkdir(parents=True, exist_ok=True)
        (dump_dir / "list.html").write_text(html, encoding="utf-8")

    jobs = boss_html.parse_list_page(html)
    if verbose:
        print(f"    解析到 {len(jobs)} 条", flush=True)
    if not jobs:
        # 没卡片有三种可能：没结果 / 未登录（BOSS 的列表要登录后才渲染）/ 结构改版。
        # 前两种能区分，最后一种只能靠 --dump 存快照回来看
        head = _head_text(html, 3000)
        if "登录/注册" in head or "登录注册后可以" in head:
            raise NeedHumanError(
                "页面显示未登录。BOSS 的职位列表要登录后才渲染，"
                "请在调试窗口里登录，再重跑。"
            )
        if verbose:
            print("    页面里一张卡片都没解析出来", flush=True)

    jobs = _dedupe(jobs)
    if skip_keys:
        before = len(jobs)
        jobs = [j for j in jobs if j.dedup_key() not in skip_keys]
        if verbose and before != len(jobs):
            print(f"    跳过 {before - len(jobs)} 条库里已有的", flush=True)
    jobs = jobs[: spec.limit]

    if spec.with_detail:
        for n, job in enumerate(jobs, 1):
            if not job.url:
                continue
            if verbose:
                print(f"  [详情 {n}/{len(jobs)}] {job.title} @ {job.company or '?'}", flush=True)
            try:
                html = cdp.capture_html(cdp_url, job.url, ready_timeout=20, render_timeout=45,
                                        expect=_DETAIL_SEL)
                _guard(html)
                if dump_dir:
                    (dump_dir / f"detail_{n:02d}.html").write_text(html, encoding="utf-8")
                boss_html.merge_detail(job, boss_html.parse_detail_page(html))
            except NeedHumanError:
                raise
            except Exception as exc:  # noqa: BLE001
                # 单条详情失败不该毁掉整轮抓取：列表页信息已经够入库了
                if verbose:
                    print(f"    详情失败，保留列表信息：{type(exc).__name__}", flush=True)
            _polite_pause()

    return jobs


def _dedupe(jobs: list[RawJob]) -> list[RawJob]:
    out: list[RawJob] = []
    seen: set[str] = set()
    for j in jobs:
        k = j.dedup_key()
        if k in seen:
            continue
        seen.add(k)
        out.append(j)
    return out


def fetch_from_snapshot(list_path: Path, detail_paths: list[Path] | None = None) -> list[RawJob]:
    """离线：从保存下来的 HTML 快照解析，不联网也不碰浏览器。"""
    if not list_path.exists():
        raise FetchError(f"快照不存在：{list_path}")
    jobs = boss_html.parse_list_page(list_path.read_text(encoding="utf-8"))
    for i, job in enumerate(jobs):
        if detail_paths and i < len(detail_paths) and detail_paths[i].exists():
            boss_html.merge_detail(
                job, boss_html.parse_detail_page(detail_paths[i].read_text(encoding="utf-8"))
            )
    return jobs
