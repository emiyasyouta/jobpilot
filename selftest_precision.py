"""精度集自检：python selftest_precision.py

三套自检的分工，一句话说清：

    selftest.py            正常流程没被改坏
    selftest_adversarial.py 换个说法也绕不过去（测召回）
    selftest_precision.py   正常岗位没被误伤（测精度）

前两套都绿但第三套红过一次：把漏判从 100% 压到 0 的那一版，
19 条正常 JD 全部被误伤。所以这套必须单独存在。

脚本里有两组断言：
1. 19 条正常 JD 不得产生 high / hard_block（允许 medium/low 提示）
2. 4 条「孪生样本」——同一模板，只把关键词换成真违规，必须仍然拦住

第 2 组是关键：只看第 1 组的话，把所有规则删光就能全绿。
精度必须和召回一起验，否则「变准了」和「变松了」在测试上无法区分。
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import rules  # noqa: E402
from tests.false_positive_cases import CASES  # noqa: E402

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


# 孪生样本：与上面某条同模板，只把关键词换成真违规。
# 它们证明这一版是「判据更准」，不是「门槛更松」
TWINS: list[dict] = [
    {
        "id": "T1", "mirror": "P1", "expect_rule": "WEEKEND_VIOLATION", "expect_level": "hard_block",
        "title": "后端开发实习生",
        "jd": "岗位职责：\n1. 参与接口开发与联调。\n任职要求：\n1. 熟悉 Python；\n2. 工作时间：实行单休，9:00-18:00。",
        "note": "同一模板去掉否定，「实行单休」必须拦住",
    },
    {
        "id": "T2", "mirror": "P6", "expect_rule": "TRAINING_SELL", "expect_level": "high",
        "title": "Java 开发实习生",
        "jd": "岗位职责：\n1. 参与后端模块开发与联调。\n任职要求：\n1. 熟悉 Spring Boot；\n2. 入职需缴纳培训费 19800 元，可分期。",
        "note": "同一模板去掉否定，「缴纳培训费」必须拦住",
    },
    {
        "id": "T3", "mirror": "P18", "expect_rule": "SALES_DISGUISE", "expect_level": "high",
        "title": "市场专员实习生",
        "jd": "岗位职责：\n1. 负责门店客户拜访与陌拜开发；\n2. 完成月度销售目标与业绩考核。\n任职要求：\n1. 能吃苦；\n2. 薪资：底薪 3500 + 提成。",
        "note": "同一模板补上硬证据，「门店」不再独自承重",
    },
    {
        "id": "T4", "mirror": "P7", "expect_rule": "GRADE_MISMATCH", "expect_level": "hard_block",
        "title": "会展执行实习生",
        "jd": "岗位职责：\n1. 参与展会执行与物料整理。\n任职要求：\n1. 本岗位仅面向 2028 届在校生；\n2. 每周到岗 3 天，双休。",
        "note": "同一模板把活动届次换成毕业届别，必须拦住",
    },
]


def main() -> int:
    print("\n=== 精度集：不该拦的不能拦 ===")
    stats: Counter[str] = Counter()
    bug_cn = {"negation": "否定失明", "referent": "指代错位", "evidence": "证据不分轻重"}

    for case in CASES:
        findings = rules.analyze(case["jd"], title=case["title"], user_grad_year=2027)
        bad = [f for f in findings if f.level in ("hard_block", "high")]
        stats[case["category"]] += 1
        label = f"{case['id']} {bug_cn[case['bug']]} · {case['title']}"
        if bad:
            detail = "误伤：" + "、".join(f"{f.rule_id}({f.level}) 命中 {f.evidence[:3]}" for f in bad)
            check(label, False, detail)
        else:
            soft = [f for f in findings if f.level in ("medium", "low")]
            tail = f"（仅提示 {'、'.join(f.rule_id for f in soft)}）" if soft else ""
            check(label, True, "")
            if tail:
                print(f"       {DIM}{tail}{RESET}")

    print(f"\n=== 孪生对照：同模板换成真违规，仍然要拦住 ===")
    for twin in TWINS:
        findings = rules.analyze(twin["jd"], title=twin["title"], user_grad_year=2027)
        hit = next((f for f in findings if f.rule_id == twin["expect_rule"]), None)
        label = f"{twin['id']} ↔ {twin['mirror']} {twin['title']}"
        if hit and hit.level == twin["expect_level"]:
            check(label, True, "")
        else:
            got = [(f.rule_id, f.level) for f in findings]
            check(label, False, f"期望 {twin['expect_rule']}/{twin['expect_level']}，实际 {got}")

    print("\n=== 分类（正常岗不应被误伤）===")
    for cat, n in stats.items():
        print(f"  {cat:<12} {n} 条")

    print("\n" + "=" * 46)
    color = GREEN if failed == 0 else RED
    print(f"{color}通过 {passed} / 失败 {failed}{RESET}")
    print("=" * 46 + "\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
