"""对抗集自检：python selftest_adversarial.py

与 selftest.py 的分工：
- selftest.py 证明「正常流程没被我改坏」
- 这个脚本证明「换个说法也绕不过去」

23 条样本：20 条对抗（培训机构/销售包装/届别/工时）+ 3 条正常岗对照。
对照组比对抗组更重要——规则越调越激进，最容易死在误伤上。
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import rules  # noqa: E402
from tests.adversarial_cases import CASES  # noqa: E402

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


def main() -> int:
    print("\n=== 对抗集：换个说法也不能绕过去 ===")
    stats: Counter[str] = Counter()

    for case in CASES:
        findings = rules.analyze(
            case["jd"],
            title=case["title"],
            industry=case["industry"],
            user_grad_year=2027,
        )
        ids = {f.rule_id for f in findings}
        block = rules.hard_blocked(findings)
        expect = case["expect_level"]
        level_cn = {"hard_block": "硬阻断", "high": "高风险", "medium": "提示", "none": "不误伤"}[expect]

        if expect == "none":
            bad = [f for f in findings if f.level in ("hard_block", "high")]
            ok = not bad
            got = "误伤：" + "、".join(f.label for f in bad) if bad else "干净"
        elif expect == "hard_block":
            ok = block is not None and case["expect_rule"] in ids
            got = block.label if block else "未阻断"
        else:
            ok = case["expect_rule"] in ids and block is None
            got = "；".join(f.label for f in findings if f.rule_id == case["expect_rule"]) or "未命中"

        flag = f"{GREEN}OK{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  {flag} {case['id']:<3} [{level_cn}] {case['title']}  ->  {got}")
        if case.get("note"):
            print(f"        {DIM}{case['note']}{RESET}")

        check(f"{case['id']} {case['title']}", ok,
              f"期望 {case['expect_rule'] or '不误伤'}，实际命中 {sorted(ids) or '无'}")
        if ok:
            stats[case["category"]] += 1

    print("\n=== 分类通过率 ===")
    for cat in dict.fromkeys(c["category"] for c in CASES):
        total = sum(1 for c in CASES if c["category"] == cat)
        print(f"  {cat:<16} {stats[cat]}/{total}")

    print(f"\n{'='*46}")
    color = GREEN if failed == 0 else RED
    print(f"{color}通过 {passed} / 失败 {failed}{RESET}")
    print(f"{'='*46}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
