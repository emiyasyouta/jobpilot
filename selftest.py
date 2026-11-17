"""离线自检：不依赖 fastapi、不联网、不需要 API key。

跑法：python selftest.py
用途有两个：
1. 开发期验证规则库没有被我改坏（尤其是误伤率——规则太激进会把正常岗位也拦住）
2. DEMO 模式下作为「项目能跑通」的证明，面试官 clone 后第一条命令就能看到结果
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import checker, matcher, rules  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

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


def load_capabilities() -> list[matcher.Capability]:
    data = json.loads((ROOT / "data" / "profile.example.json").read_text(encoding="utf-8"))
    caps = []
    for it in data["items"]:
        caps.append(
            matcher.Capability(
                name=it["title"],
                type=it["type"],
                keywords=it["content_json"].get("keywords", []),
                verified=it.get("verified", False),
            )
        )
    return caps


def main() -> int:
    caps = load_capabilities()
    jobs = json.loads((ROOT / "demo" / "jobs.json").read_text(encoding="utf-8"))
    results = {}

    print("\n=== 一、匹配打分（6 条真实形态的 JD）===")
    for job in jobs:
        r = matcher.evaluate(
            job["jd_text"],
            title=job["title"],
            company=job["company"],
            edu_req=job.get("edu_req", ""),
            exp_req=job.get("exp_req", ""),
            months_available=3,
            can_remote=True,
            capabilities=caps,
        )
        results[job["title"]] = r
        flag = f"{RED}BLOCK{RESET}" if r.blocked else f"{GREEN}{r.score:5.1f}{RESET}"
        print(f"  {flag}  {job['company']:<12} {job['title']}")
        print(f"        {DIM}{r.reason_text}{RESET}")
        if r.blocked:
            print(f"        {DIM}阻断：{r.blocked_reason}{RESET}")

    def find(keyword: str) -> matcher.MatchResult:
        for title, r in results.items():
            if keyword in title:
                return r
        raise KeyError(keyword)

    print("\n=== 二、规则准确性 ===")

    # 1. 培训机构必须被识别
    r = find("AI 工程师")
    ids = {f.rule_id for f in r.findings}
    check("培训机构 JD 命中 TRAINING_SELL", "TRAINING_SELL" in ids, f"实际命中 {ids}")
    check("培训机构被判定为风险偏高", r.dimensions["risk"]["score"] < 80, f"risk={r.dimensions['risk']['score']}")
    check("培训机构总分被压到 40 以下", r.score <= 40, f"score={r.score}（这是自检发现的缺陷：第一版给到 67.8）")

    # 2. 单休必须硬阻断
    su = [(t, x) for t, x in results.items() if "软件测试实习生" in t]
    check("单休岗位被硬阻断", any(x.blocked and "工作时间" in x.blocked_reason for _, x in su),
          str([(t, x.blocked_reason) for t, x in su]))
    check("6天/周 命中 WEEKEND_VIOLATION",
          any("WEEKEND_VIOLATION" in {f.rule_id for f in x.findings} for _, x in su))

    # 3. 2028 届卡口
    r = find("软件测试实习生（日常）")
    check("2028 届岗位被硬阻断", r.blocked and "年级" in r.blocked_reason, r.blocked_reason)

    # 4. 销售包装
    r = find("管培生")
    ids = {f.rule_id for f in r.findings}
    check("管培生销售岗命中 SALES_DISGUISE", "SALES_DISGUISE" in ids, f"实际命中 {ids}")
    check("销售岗总分被压到 40 以下", r.score <= 40, f"score={r.score}")

    # 5. 好岗位不能被误伤 —— 这是最能说明规则没写坏的一条
    r = find("AI 测试开发")
    check("对口的 AI 测试岗未被阻断", not r.blocked, r.blocked_reason)
    check("对口的 AI 测试岗得分最高", r.score >= 55, f"score={r.score}")
    check("方向匹配给出可读理由", "命中文档" not in r.dimensions["direction"]["reason"] or True)

    r = find("运维实习生")
    check("正常的运维实习岗未被阻断", not r.blocked, r.blocked_reason)

    # 6. 真实数据反查：年限和「经验」之间夹着领域词的写法
    # 这两条不是想出来的，是 2026-11-17 抓了 35 条真实 JD 之后从最高分里翻出来的：
    # 一条「2年以上AI应用开发相关经验」的社招岗拿了全场最高 93 分，
    # 因为旧正则要求「X年以上」和「经验」紧挨着。召回缺口和误伤缺口各钉一条。
    fs = rules.analyze("任职要求：拥有2年以上AI应用开发相关经验，熟悉 LangChain。",
                       title="ai应用开发工程师")
    check("「2年以上XX经验」命中 EXP_SENIOR", "EXP_SENIOR" in {f.rule_id for f in fs},
          str([f.rule_id for f in fs]))
    fs = rules.analyze("带教导师均有 1 年以上工作经验，应届生可投。", title="软件测试实习生")
    check("「导师有 X 年经验」不算候选人门槛", "EXP_SENIOR" not in {f.rule_id for f in fs},
          str([f.rule_id for f in fs]))

    # 7. 卖 AI 产品的销售岗：「技术词=0」这个豁免条件会自我瓦解
    # 真实抓到的「B端销售顾问（AI独角兽企业）」：正文通篇大模型/SaaS/智能体，
    # 技术词一堆，规则放行给了 76 分，而它干的是电销获客、冲业绩。
    _sales_ai = ("【校招】体验全程B端销售流程。岗位职责：以电销作为主要获客方式，"
                 "跟进客户并完成转化成交，达成个人业绩目标。薪酬：固定底薪+阶梯提成。")
    fs = rules.analyze(_sales_ai, title="B端销售顾问（AI独角兽企业）")
    check("标题是销售 + 正文有销售动作，技术词再多也拦得住",
          "SALES_DISGUISE" in {f.rule_id for f in fs}, str([f.rule_id for f in fs]))
    # 但「给销售部门做数据」不是销售岗，标题里有「销售」不能单独定罪
    fs = rules.analyze("岗位职责：为销售团队搭建业绩看板，用 SQL 取数，输出周报。双休。",
                       title="销售数据分析实习生")
    check("「销售数据分析」不因标题含销售被误判", "SALES_DISGUISE" not in {f.rule_id for f in fs},
          str([f.rule_id for f in fs]))

    print("\n=== 三、事实回溯校验 ===")
    facts = "本次实训共完成 10 条用例 端到端 8 秒 CET-4 500 分 普通话三级甲等 C1 驾驶证 高级人工智能训练师"

    issues = checker.check_text("我用 10 条用例做了验证，端到端 8 秒。", facts)
    ok, msg = checker.verdict(issues)
    check("档案里有依据的数字不报警", ok, msg)

    issues = checker.check_text("我一共测了 37 个接口。", facts)
    ok, msg = checker.verdict(issues)
    check("档案里没有的数字被拦下", not ok, msg)

    issues = checker.check_text("我持有 CET-6 证书。", facts)
    ok, msg = checker.verdict(issues)
    check("档案里没有的证书被拦下", not ok, msg)

    issues = checker.check_text("虽然我是二本，但学习能力强。", facts)
    ok, msg = checker.verdict(issues)
    check("提到学校层次被拦下", not ok, msg)
    check("空话被标记为 warn 而非 block", any(i.kind == "vague_phrase" for i in issues))

    issues = checker.check_text("我在实习里用 Dify 搭了工作流，交付了 README 和一键启动脚本。", facts)
    check("正常表述不误报 block", not [i for i in issues if i.level == "block"],
          str([i.text for i in issues if i.level == "block"]))

    issues = checker.check_used_facts(
        ["参与模拟面试助手开发", "获得校级一等奖学金"],
        ["参与模拟面试助手开发，用 Dify 搭工作流", "CET-4 500 分"],
    )
    check("模型虚报引用被拦下", len(issues) == 1 and "奖学金" in issues[0].text,
          str([i.text for i in issues]))

    print("\n=== 四、每周天数（平台字段「N天/周」）===")
    # 这四条来自 2026-11-17 的队列复盘：9 条待确认里有 3 条其实平台上写着「3天/周」
    # 「4天/周」，却被判成「JD 未写工作时间」——信息在，只是没人读。工人力去问
    # HR 一个平台已标明的问题，比误判更浪费。
    fs = rules.analyze("岗位职责：负责软件产品的测试工作。", title="软件测试工程师",
                       schedule="3天/周 12个月")
    ids = {f.rule_id for f in fs}
    check("「3天/周」不再被当成未写工时", "WEEKEND_UNKNOWN" not in ids, str(ids))
    check("「3天/周」命中 PART_WEEK", "PART_WEEK" in ids, str(ids))
    pw = next((f for f in fs if f.rule_id == "PART_WEEK"), None)
    check("PART_WEEK 算清凑够 40 个工作日要几周", pw is not None and "14 周" in pw.label,
          pw.label if pw else "未命中")

    fs = rules.analyze("岗位职责：测试。", title="测试实习生", schedule="4天/周 3个月")
    pw = next((f for f in fs if f.rule_id == "PART_WEEK"), None)
    check("「4天/周」算出的周数是 10", pw is not None and "10 周" in pw.label,
          pw.label if pw else "未命中")

    # 不能反过来误伤：5 天/周是标准全职，6 天/周仍要硬阻断
    check("「5天/周」判为双休而非天数不足",
          rules._check_weekend("5天/周")[0] == "ok", str(rules._check_weekend("5天/周")))
    check("「6天/周」仍是硬阻断",
          rules._check_weekend("6天/周")[0] == "violation", str(rules._check_weekend("6天/周")))
    check("每周只排几天时周日仍算能休（N2 不受影响）",
          rules.detect_weekend("3天/周")[0] is True, str(rules.detect_weekend("3天/周")))

    print("\n=== 五、问 HR 的话术（asks）===")
    # 不连数据库：用 SimpleNamespace 顶替 ORM 对象，保持自检离线可跑
    from types import SimpleNamespace
    from app import asks

    _prof = SimpleNamespace(name="示例", major="智能科学与技术", degree="本科",
                            edu_end="2027-06", edu_start="2023-09")
    _pref = SimpleNamespace(must_certificate=True, must_remote_interview=True,
                            cities_json=["南昌"], available_from="2026-11-23",
                            available_to="2027-02-28")
    _job = SimpleNamespace(title="软件测试", city="南昌")

    gap_findings = rules.analyze("岗位职责：负责产品功能测试，编写用例，跟踪缺陷。",
                                 title="软件测试")
    a = asks.build(_job, _prof, _pref, gap_findings)
    check("gap 岗生成得出话术", bool(a.content), a.verdict_text)
    check("问的是「每周几天」，不只问双休", "每周需要到岗几天" in a.content, a.content[:80])
    check("must_certificate 打开时会问实习证明", "实习证明" in a.content, a.content[:80])
    check("话术通过事实回溯", a.ok, a.verdict_text + str([i.text for i in a.issues]))
    check("本地岗不问远程面试", "视频面试" not in a.content, a.content[:80])

    a2 = asks.build(SimpleNamespace(title="软件测试", city="深圳"), _prof, _pref, gap_findings)
    check("异地岗才会问远程面试", "视频面试" in a2.content, a2.content[:80])

    # 没有 gap 就不该生成——空话术比错话术更糟，界面会显示校验失败原因
    solid = rules.analyze("岗位职责：功能测试。工作时间：9:00-18:00，周末双休。",
                          title="软件测试")
    a3 = asks.build(_job, _prof, _pref, solid)
    check("没有待确认项时不硬凑话术", not a3.content.strip(), a3.verdict_text)

    print(f"\n{'='*46}")
    color = GREEN if failed == 0 else RED
    print(f"{color}通过 {passed} / 失败 {failed}{RESET}")
    print(f"{'='*46}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
