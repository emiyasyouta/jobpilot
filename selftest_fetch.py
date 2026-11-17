"""抓取解析层的离线自检：不联网、不开浏览器、不需要数据库。

跑法：python selftest_fetch.py

为什么单独一份：抓取是最容易「看起来跑通了其实解析全空」的一环。
线上页面改版、class 加哈希、DOM 嵌套变化，都会让解析器静默返回空列表——
不报错，只是没抓到东西。所以这里对字段逐个断言，而不是只看条数。

快照来源：fixtures/boss/list_page.html、detail_page.html 是 2026-11-17 用
登录态从 BOSS 真实抓下来的页面（只把账号昵称换成了占位符）。上一版这里是
手写的假结构，而手写结构永远不会「恰好」长成真实页面的样子：
真实卡片是 li.job-card-box 直接挂在 ul 下，中间还夹着一层 div；
薪资数字被换成了自定义字体的私用区字符；JD 正文里插了「BOSS直聘」水印，
还有一批汉字被替换成康熙部首区的同形字。这些全靠真实快照才暴露出来。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.sources import boss_html  # noqa: E402
from app.sources.base import RawJob, parse_salary, split_area, split_tags  # noqa: E402

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  {GREEN}PASS{RESET} {name}")
    else:
        failed += 1
        print(f"  {RED}FAIL{RESET} {name}")
        if detail:
            print(f"       {DIM}{detail}{RESET}")


LIST_HTML = (ROOT / "fixtures" / "boss" / "list_page.html").read_text(encoding="utf-8")
DETAIL_HTML = (ROOT / "fixtures" / "boss" / "detail_page.html").read_text(encoding="utf-8")


print("\n[1] 薪资解析")
for raw, want in [
    ("150-200元/天", (150.0, 200.0, "day")),
    ("180元/天", (180.0, 180.0, "day")),
    ("3-6K", (3000.0, 6000.0, "month")),
    ("6-9K·13薪", (6000.0, 9000.0, "month")),
    ("1.5-2万/月", (15000.0, 20000.0, "month")),
    ("面议", (None, None, "day")),
]:
    got = parse_salary(raw)
    check(f"{raw} → {want}", got == want, f"实际 {got}")

print("\n[2] 标签与地区拆分")
check("标签分学历/经验", split_tags(["本科", "在校/应届", "互联网"]) == ("本科", "在校/应届"))
check("公司标签不误判", split_tags(["互联网", "100-499人", "不需要融资"]) == ("", ""))
check("地区拆分", split_area("南昌·青山湖区") == ("南昌", "青山湖区"))
check("地区无分隔", split_area("南昌") == ("南昌", ""))

print("\n[3] 列表页解析（真实快照）")
jobs = boss_html.parse_list_page(LIST_HTML)
check("解析出 15 条", len(jobs) == 15, f"实际 {len(jobs)}")

first = jobs[0] if jobs else RawJob()
check("标题", first.title == "AI豆包助理（可实习5k）", first.title)
check("公司", first.company == "南昌阅山河运营管理", first.company)
check("城市", first.city == "南昌", first.city)
check("区域（去掉三级地址的分隔点）", first.district == "青云谱区 坛子口", first.district)
check("经验", first.exp_req == "经验不限", first.exp_req)
check("学历", first.edu_req == "学历不限", first.edu_req)
check("链接补全为绝对地址", first.url.startswith("https://www.zhipin.com/job_detail/"), first.url)
check("链接指向详情页", first.url.endswith(".html"), first.url)

check("标题里没有 PUA 乱码", not any(0xE000 <= ord(c) <= 0xF8FF for j in jobs for c in j.title),
      str([j.title for j in jobs if any(0xE000 <= ord(c) <= 0xF8FF for c in j.title)]))

print("\n[4] 字体反爬：列表页薪资不能留垃圾")
# BOSS 把薪资数字映射成自定义字体的私用区字符（U+E031–U+E03A），映射表每次下发都不同。
# 硬还原一张表属于「偶尔对」，所以这里的选择是：列表页薪资留空，等详情页的明文。
check("薪资字符已被剥离（不是「-K」这种残渣）",
      all(j.salary_raw == "" for j in jobs[:5]),
      str([j.salary_raw for j in jobs[:5]]))
check("薪资数值置空而不是猜", all(j.salary_min is None for j in jobs),
      str([(j.title, j.salary_min) for j in jobs if j.salary_min is not None][:3]))

print("\n[5] 去重键")
a = RawJob(company="X", title="Y", url="https://www.zhipin.com/job_detail/abc.html?securityId=1&lid=2")
b = RawJob(company="X", title="Y", url="https://www.zhipin.com/job_detail/abc.html?securityId=9")
check("同一职位不同跟踪参数 → 同键", a.dedup_key() == b.dedup_key(), a.dedup_key() + " vs " + b.dedup_key())
c = RawJob(company="X", title="Y", city="南昌")
d = RawJob(company="X", title="Y", city="赣州")
check("同名岗位不同城市 → 不同键", c.dedup_key() != d.dedup_key())

print("\n[6] 详情页解析（真实快照）")
detail = boss_html.parse_detail_page(DETAIL_HTML)
jd = detail.get("jd_text", "")
check("取到 JD 正文", len(jd) >= 80, f"{len(jd)} 字")
check("JD 是这家公司的正文", "品牌拓展" in jd, jd[:60])
check("没有把公司简介当 JD", "法律服务" not in jd and "专注" not in jd[:40], jd[:80])
check("JD 保留分条换行", jd.count("\n") >= 3, f"换行数 {jd.count(chr(10))}")
check("HR 姓名（不带「在线」这类活跃标签）", detail.get("hr_name") == "黄婕", str(detail.get("hr_name")))
check("公司名", detail.get("company") == "南昌阅山河运营管理", str(detail.get("company")))
check("标题", detail.get("title") == "AI豆包助理（可实习5k）", str(detail.get("title")))
check("薪资是明文数字", (detail.get("salary_min"), detail.get("salary_max")) == (4000.0, 6000.0),
      str(detail.get("salary_raw")))
check("薪资单位按月", detail.get("salary_unit") == "month", str(detail.get("salary_unit")))
check("经验", detail.get("exp_req") == "经验不限", str(detail.get("exp_req")))
check("学历", detail.get("edu_req") == "学历不限", str(detail.get("edu_req")))
# 这条的工时字段是「经验不限」，不是「6天/周」——不该被误判成排班信息
check("工时字段不被误当排班", "schedule" not in detail, str(detail.get("schedule")))

print("\n[7] 反爬文本清洗")
check("JD 里没有「BOSS直聘」水印", "BOSS直聘" not in jd and "直聘" not in jd,
      [s for s in ("BOSS直聘", "直聘") if s in jd])
check("水印删掉后能拼回原词", "公司直招" in jd, jd[:30])
check("康熙部首已还原成标准汉字",
      not any(0x2E80 <= ord(ch) <= 0x2FDF for ch in jd),
      str([ch for ch in jd if 0x2E80 <= ord(ch) <= 0x2FDF][:8]))
check("还原后的词读得通", "接受无经验" in jd or "接受小白" in jd, jd[:60])

# 水印不止「BOSS直聘」一种写法。真实页面里插的是「来自BOSS直聘」，夹在词中间：
# 「验<span>来自BOSS直聘</span>证」→ 早期固定词表删不掉，JD 里留下「验来自证」。
# 这条用一小段 HTML 直接钉住变体，不依赖某份快照恰好包含它。
_SPLIT_HTML = """<html><body><div class="job-sec-text">岗位职<span class="x1">来自BOSS直聘</span>责：
1. 负<span class="x2">BOSS直聘</span>责日常测试与验<span class="x3">来自BOSS直聘</span>证；
2. 输出测试报<span class="x4">直聘</span>告。</div></body></html>"""
_sp = boss_html.parse_detail_page(_SPLIT_HTML).get("jd_text", "")
check("水印变体「来自BOSS直聘」也被删掉", "直聘" not in _sp, _sp[:40])
check("被水印劈开的词能拼回去", "负责日常测试与验证" in _sp, _sp[:60])

print("\n[8] 详情合并")
merged = boss_html.merge_detail(jobs[0], detail)
check("JD 已并入", merged.jd_text == jd)
check("不覆盖列表页公司名", merged.company == "南昌阅山河运营管理", merged.company)
# 列表页薪资是空的（字体混淆已剥离），详情页的明文要能补上
check("列表页薪资为空时用详情页补", (merged.salary_min, merged.salary_max) == (4000.0, 6000.0),
      f"{merged.salary_min} {merged.salary_max}")
check("详情页的工时字段不会覆盖经验", merged.exp_req == "经验不限", merged.exp_req)

print("\n[9] 容错")
check("空 HTML 返回空列表", boss_html.parse_list_page("") == [])
check(
    "残缺标签不抛异常",
    (lambda r: len(r) == 1 and r[0].title == "前端实习生")(
        boss_html.parse_list_page(
            '<div class="job-card-wrapper"><span class="job-name">前端实习生</span>'
            '<span class="job-salary">180元/天</span>'
        )
    ),
)

# 页面上的功能元素不能被当成职位：真实未登录页里出现过把「职位搜索」解析成岗位的情况
junk = boss_html.parse_list_page(
    '<html><body><div class="job-name">职位搜索</div>'
    '<a href="/job_detail/zz9.html" title="职位搜索">职位搜索</a></body></html>'
)
check("功能元素不算职位", junk == [], str([(j.title, j.url) for j in junk]))

fallback = boss_html.parse_list_page(
    '<html><body><ul><li><a href="/job_detail/zz9.html" title="产品运营实习生">产品运营实习生</a></li></ul></body></html>'
)
check("无卡片时按链接兜底", len(fallback) == 1 and fallback[0].title == "产品运营实习生",
      str([(j.title, j.url) for j in fallback]))

print("\n[10] 快照加载")
from app.sources import boss_live  # noqa: E402

snap = boss_live.fetch_from_snapshot(
    ROOT / "fixtures" / "boss" / "list_page.html",
    [ROOT / "fixtures" / "boss" / "detail_page.html"],
)
check("快照入口复用同一解析器", len(snap) == 15, f"实际 {len(snap)}")
check("详情快照合并到第一条", "品牌拓展" in snap[0].jd_text, snap[0].jd_text[:40])
check("合并后薪资来自详情页", snap[0].salary_min == 4000.0, str(snap[0].salary_min))

print()
print(f"{GREEN}通过 {passed} / 失败 {failed}{RESET}")
sys.exit(1 if failed else 0)
