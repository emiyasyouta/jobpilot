"""BOSS 直聘页面解析。纯函数：HTML 字符串进，RawJob 出，不联网。

选择器按 2026-11 的真实页面校准过（fixtures/boss/real/ 是用登录态抓下来的快照），
不是照着网上教程猜的：

    搜索结果页
      li.job-card-box                      一张卡片（注意它直接挂在 ul 下，中间夹着 div）
        a.job-name                         职位名 + 详情链接（相对路径）
        span.job-salary                    薪资——DOM 里是自定义字体的私用区字符，见下方说明
        ul.tag-list > li                   经验 / 学历 / 技能标签，混在一起
        a.boss-info > span.boss-name       公司名
        span.company-location              南昌·红谷滩区·红角洲

    详情页
      h1                                   职位名（全页只有一个）
      .salary                              薪资，这里是真数字
      .info-primary span.text-experiece    经验
      .info-primary span.text-degree       学历
      .job-sec-text                        职位描述（第二个是公司介绍，带 fold-text）
      .job-boss-info                       HR 名字 / 活跃度 / 头衔

两条底线：
1. **宁可少一个字段，也不猜错**。猜错的公司名会直接污染招呼语，事后没人发现。
2. 列表页的薪资一律不信（原因见 _strip_pua），等详情页的真实文本。
"""

from __future__ import annotations

import re

from . import dom
from .base import RawJob, clean_text, parse_salary, split_area, split_tags

# 卡片容器，按精度排序。真实页是 job-card-box；后两个是旧结构，留着当兜底。
_CARD_HINTS = ("job-card-box", "job-card-wrapper", "job-list-item")
_WRAPPER_HINTS = ("job-card-wrap", "card-area")  # 包住卡片的壳，取公司名时可能用得着

_JD_SECTION_HINTS = ("job-sec-text", "job-detail-section", "job-sec", "job-desc")

# BOSS 把薪资数字映射成自定义字体，DOM 里取出来是 U+E031–U+E03A 这类私用区码点。
# 映射表随请求变化（每次下发不同字体文件），硬编码一张表就是把「偶尔对」当正确。
# 本项目本来就逐条进详情页，那里是明文——所以列表页的薪资直接丢弃，不做半吊子还原。
_PUA = re.compile(r"[\ue000-\uf8ff]")


# BOSS 把水印包在随机 class 的 span 里（这次叫 pEABwKayCsdi，下次就换），
# 内容有「BOSS直聘」「直聘」「来自BOSS直聘」几种写法。
# 早期按固定词表删，真实页面立刻打脸：「验<span>来自BOSS直聘</span>证」里的
# 「来自BOSS直聘」不在表里，于是一整段 JD 变成「验来自证」——
# 词被劈开，关键词匹配对不上，话术还会带着错字进简历。
# 现在按「短文本 + 以「直聘」结尾」识别，变体再多也是同一个后缀。
_WATERMARK_EXTRA = {"BOSS", "看准网", "我是BOSS", "升级VIP"}
_WATERMARK_TAGS = {"span", "b", "i", "em", "font", "label"}


def _is_watermark_text(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 10:
        return False
    if t in _WATERMARK_EXTRA:
        return True
    return t.endswith("直聘")


def _drop_watermarks(node: dom.Node) -> None:
    kept: list = []
    for ch in node.children:
        if isinstance(ch, dom.Node):
            if ch.tag in _WATERMARK_TAGS and ch.get("class") and _is_watermark_text(ch.text()):
                continue
            _drop_watermarks(ch)
        kept.append(ch)
    node.children = kept


def parse_list_page(html: str) -> list[RawJob]:
    """解析搜索结果页，返回职位列表（不含完整 JD）。"""
    root = dom.parse(html)
    _drop_watermarks(root)
    jobs: list[RawJob] = []
    seen: set[str] = set()

    for card in _cards(root):
        rj = _read_card(card)
        if not _looks_like_job(rj):
            continue
        key = rj.dedup_key()
        if key in seen:
            continue
        seen.add(key)
        jobs.append(rj)

    if jobs:
        return jobs

    # 一个职位卡片都没找到，说明改版了（或页面是未登录/验证页）。
    # 退化成「按详情链接找」，至少能把标题和网址拿回来，用户能跳转手填。
    return _fallback_by_links(root)


def _cards(root: dom.Node) -> list[dom.Node]:
    """找所有职位卡片。

    只认卡片本身，不在外层壳上做匹配——壳（card-area）会同时命中多张卡片，
    用壳会把 15 条读成 1 条。找不到就逐级退到旧的类名。
    """
    for hint in _CARD_HINTS:
        found = list(root.find_all(cls=hint))
        if found:
            return found
    return []


# 页面上跟职位无关的链接文案。未登录页和搜索框区域常出现，误当成职位会污染列表。
_UI_WORDS = ("职位搜索", "搜索职位", "查看更多", "立即登录", "登录", "注册", "首页", "公司")


def _looks_like_job(rj: RawJob) -> bool:
    """判是不是一条真职位。

    只靠 class 命中不够：页面上的搜索框、导航链接也可能带 job-name 或指向 job_detail。
    三个必要条件——标题像职位名、且至少有「链接 / 公司 / 薪资」之一。
    宁可漏一条，也别把功能元素写成职位：假职位会被打分、进列表，用户还得自己发现。
    """
    t = rj.title.strip()
    if not t or len(t) > 40:
        return False
    if t in _UI_WORDS:
        return False
    return bool(rj.url or rj.company or rj.salary_min is not None)


def _strip_pua(text: str) -> str:
    """去掉字体反爬塞进来的私用区字符。

    剥完只要还剩不下一个阿拉伯数字，就说明这段文本里的数字全是字体映射出来的，
    留着只会变成「-K」这种垃圾，不如留空让详情页来填。
    """
    cleaned = _PUA.sub("", text or "")
    return cleaned if re.search(r"\d", cleaned) else ""


def _read_card(card: dom.Node) -> RawJob:
    name_node = card.find(cls="job-name")
    title = name_node.text() if name_node else ""

    salary_raw = ""
    sal = card.find(cls="job-salary") or card.find(cls="salary")
    if sal:
        salary_raw = _strip_pua(sal.text())
    lo, hi, unit = parse_salary(salary_raw)

    area_node = card.find(cls="company-location") or card.find(cls="job-area")
    city, district = split_area(area_node.text() if area_node else "")
    # company-location 是「南昌·红谷滩区·红角洲」三段，第二批也塞进 district
    if district and "·" in district:
        district = district.replace("·", " ")

    company_node = (
        card.find(cls="boss-name")
        or card.find(cls="company-name")
        or card.find(cls="brand-name")
    )
    company = company_node.text() if company_node else ""
    if not company:
        link = card.find(cls="boss-info")
        company = link.text() if link else ""

    # 标签列表里经验/学历/技能混排，靠词表区分，不看顺序
    tags = [clean_text(li.text()) for li in card.find_all(tag="li")]
    edu_req, exp_req = split_tags(tags)

    url = ""
    if name_node:
        url = name_node.get("href")
    if not url:
        for a in card.find_all(tag="a"):
            if "/job_detail/" in a.get("href"):
                url = a.get("href")
                break
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://www.zhipin.com" + url

    if not title and url:
        # 标题真取不到时退到链接的 title 属性，仍未命中就交给 _looks_like_job 丢掉
        title = (name_node.get("title") if name_node else "") or ""

    return RawJob(
        company=clean_text(company).strip(),
        title=clean_text(title).strip(),
        city=city,
        district=district,
        salary_raw=salary_raw.strip(),
        salary_min=lo,
        salary_max=hi,
        salary_unit=unit,
        edu_req=edu_req,
        exp_req=exp_req,
        url=url,
        source="boss",
        raw={"tags": tags},
    )


def _fallback_by_links(root: dom.Node) -> list[RawJob]:
    out: list[RawJob] = []
    seen: set[str] = set()
    for a in root.find_all(tag="a"):
        href = a.get("href")
        if "/job_detail/" not in href:
            continue
        title = (a.get("title") or a.text().split("\n")[0] or "").strip()
        if not title or title in _UI_WORDS or len(title) > 40:
            continue
        rj = RawJob(title=title, url=href if href.startswith("http") else "https://www.zhipin.com" + href,
                    source="boss", raw={"fallback": True})
        k = rj.dedup_key()
        if k in seen:
            continue
        seen.add(k)
        out.append(rj)
    return out


# ---------------------------------------------------------------- 详情页


def parse_detail_page(html: str) -> dict:
    """解析职位详情页。

    返回的 dict 直接用于补全 RawJob：{"jd_text", "hr_name", "company", "title", "salary_raw", ...}
    缺字段就留空，不抛异常——列表页的信息已经够入库，详情只是加分项。
    """
    root = dom.parse(html)
    _drop_watermarks(root)
    out: dict = {}

    title = _detail_title(root)
    if title:
        out["title"] = title

    sal = root.find(cls="salary")
    if sal:
        raw = _strip_pua(sal.text())
        if raw:
            out["salary_raw"] = raw
            lo, hi, unit = parse_salary(raw)
            out["salary_min"], out["salary_max"], out["salary_unit"] = lo, hi, unit

    info = root.find(cls="info-primary")
    if info:
        exp = info.find(cls="text-experiece")
        if exp:
            # 这个字段不是「经验要求」——BOSS 把工时也塞在这里：
            # 「6天/周 6个月」「1天/周 1个月」是上班天数和时长，
            # 「在校/应届」「3-5年」才是经验。不分流会把工时当经验，
            # 而工时恰好是判断单休最硬的证据（比 JD 里的措辞可靠）。
            txt = exp.text().strip()
            if _WORK_SCHEDULE.search(txt):
                out["schedule"] = txt
            else:
                out["exp_req"] = txt
        edu = info.find(cls="text-degree")
        if edu:
            out["edu_req"] = edu.text().strip()
        city = _city_of(info.text())
        if city:
            out["city"] = city

    jd = _extract_jd(root)
    if jd:
        out["jd_text"] = jd

    hr = root.find(cls="job-boss-info")
    if hr:
        # 名字优先取 .name；取不到就取首行的第一段——
        # 「黄婕 在线」里的「在线」是活跃状态，不是名字的一部分
        nm = hr.find(cls="name")
        cand = nm.text().strip() if nm else ""
        if cand:
            # .name 里常连着活跃标签（「黄婕 在线」），按首段截断
            out["hr_name"] = cand.split()[0]
        else:
            lines = [ln.strip() for ln in hr.text().split("\n") if ln.strip()]
            if lines:
                out["hr_name"] = lines[0].split()[0]
            # 头衔从后往前找：活跃度/公司·招聘者这类都不是头衔
            for ln in reversed(lines[1:] if len(lines) > 1 else []):
                if _looks_like_hr_title(ln):
                    out["hr_title"] = ln
                    break

    company = _detail_company(root)
    if company:
        out["company"] = company

    # 出口统一清洗：反爬水印和部首替换可能出现在任何字段里
    return {k: (clean_text(v) if isinstance(v, str) else v)
            for k, v in out.items() if v not in ("", None)}


def _detail_title(root: dom.Node) -> str:
    """详情页职位名。

    优先限定在 banner / primary 里找 h1；限定不到就用全页第一个 h1——
    真实详情页整页只有一个 h1（页头导航用的是 div），退到全页也不会串。
    超过 40 字多半是抓到了别的块，宁可不填。
    """
    for box_hint in ("job-banner", "job-primary"):
        box = root.find(cls=box_hint)
        if box:
            h1 = box.find(tag="h1")
            if h1 and 0 < len(h1.text()) <= 40:
                return h1.text()
    h1 = root.find(tag="h1")
    if h1:
        t = h1.text()
        if 0 < len(t) <= 40:
            return t
    return ""


_WORK_SCHEDULE = re.compile(r"\d\s*天\s*/\s*周|\d{1,2}\s*个月")


def _city_of(info_text: str) -> str:
    """从「南昌 3-5年 本科」这行里取城市。

    只认第一段里不含年限/学历关键词的那个词——「3-5年」也常排在最前面。
    """
    for line in (info_text or "").split("\n"):
        parts = line.split()
        if len(parts) < 2:
            continue
        head = parts[0]
        if re.search(r"\d|[年月]|本科|硕士|大专|博士|应届|在校", head):
            continue
        if 1 < len(head) <= 8:
            return head
    return ""


# 「在线 / 刚刚活跃 / 3日内活跃」——这是活跃状态，不是头衔
_ACTIVE = re.compile(r"在线|活跃|刚刚|分钟前|小时前|天前|本周|本月|周内|日内|个月|半年前")


def _looks_like_hr_title(t: str) -> bool:
    return (1 < len(t) <= 12 and not _ACTIVE.search(t)
            and "·" not in t and "招聘者" not in t)


# 公司侧栏里混着规模、融资、按钮文案，这些都不是公司名
_COMPANY_SKIP = re.compile(r"(^公司$|^企业$|人$|融资|查看|基本信息|介绍|招聘中?$)")


def _detail_company(root: dom.Node) -> str:
    """公司名：优先取指向 /gongsi/ 的链接里的公司名，退到侧栏「公司基本信息」。"""
    for a in root.find_all(tag="a"):
        if "/gongsi/" not in a.get("href"):
            continue
        nm = a.find(cls="boss-name")
        t = (nm.text() if nm else a.text()).split("\n")[0].strip()
        if 2 < len(t) <= 40 and not _COMPANY_SKIP.search(t):
            return t
    side = root.find(cls="sider-company")
    if side:
        lines = [ln.strip() for ln in side.text().split("\n") if ln.strip()]
        # 第 1 行是「公司基本信息」这个小标题，名字在它后面
        for ln in lines[1:]:
            if 2 < len(ln) <= 40 and not _COMPANY_SKIP.search(ln):
                return ln
    return ""


def _extract_jd(root: dom.Node) -> str:
    """取职位描述正文。

    按 hints 的精度顺序逐个试，命中够长就返回——不取「全局最长」，
    否则外层的 section 容器（含小标题）总是比正文长，会多带一行"职位描述"。
    """
    for hint in _JD_SECTION_HINTS:
        best = ""
        for node in root.find_all(cls=hint):
            # 详情页里「公司介绍」也常用 job-sec-text，混进来会把公司简介当 JD。
            # 它的特征是折叠（fold-text）或挂在 company-info 里。
            if node.has_class("fold-text"):
                continue
            if node.closest("company-info") or node.closest("company-detail"):
                continue
            t = node.text()
            if len(t) > len(best):
                best = t
        if len(best) >= 40:
            return best

    # 兜底：按「职位描述 / 岗位职责」关键词截取到「公司介绍」之前
    body = root.find(tag="body")
    text = body.text() if body else root.text()
    return _slice_by_keywords(text)


_START = re.compile(r"(职位描述|岗位职责|工作职责|岗位职责：)")
_END = re.compile(r"(公司介绍|公司简介|工商信息|工作地址|企业介绍)")


def _slice_by_keywords(text: str) -> str:
    m = _START.search(text)
    if not m:
        return ""
    tail = text[m.start():]
    e = _END.search(tail)
    chunk = tail[: e.start()] if e else tail[:3000]
    return chunk.strip()


# ---------------------------------------------------------------- 合并


def merge_detail(job: RawJob, detail: dict) -> RawJob:
    """把详情页解析结果补进列表页结果。只补空、不覆盖——列表页的字段是职位归属，
    详情页偶尔会把「相关职位」的公司名带进来。

    薪资例外：按「有没有解析出数值」判断，而不是「字符串空不空」——
    列表页的薪资是字体混淆后的残渣，字符串非空但数值为 None。
    """
    if not job.jd_text and detail.get("jd_text"):
        job.jd_text = detail["jd_text"]
    if not job.hr_name and detail.get("hr_name"):
        job.hr_name = detail["hr_name"]
    if not job.company and detail.get("company"):
        job.company = detail["company"]
    if job.salary_min is None and detail.get("salary_raw"):
        job.salary_raw = detail["salary_raw"]
        job.salary_min = detail.get("salary_min")
        job.salary_max = detail.get("salary_max")
        job.salary_unit = detail.get("salary_unit", job.salary_unit)
    if not job.exp_req and detail.get("exp_req"):
        job.exp_req = detail["exp_req"]
    # 工时字段（「6天/周 6个月」）只在详情页有，列表页拿不到——漏传这一句，
    # 单休岗就只剩「JD 没写工时」这一条低风险提示，会不会投全看运气
    if not job.schedule and detail.get("schedule"):
        job.schedule = detail["schedule"]
    if not job.edu_req and detail.get("edu_req"):
        job.edu_req = detail["edu_req"]
    if not job.city and detail.get("city"):
        job.city = detail["city"]
    return job
