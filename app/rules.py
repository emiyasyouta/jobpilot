"""职位风险识别规则库。

为什么用规则而不是让模型判断：**可解释、可复现、可单测**。
模型可能今天说这是培训机构、明天说不是；而规则命中了哪几个词，
界面上能直接把这句原文标出来。面试时被问"你怎么保证准确率"，
答"我把误判成本分成了硬阻断和软提示两级"，比答"我调了 prompt"扎实。

规则分两级：
- hard_block：命中即不可投（单休且休不到周日、年级卡口）
- 软提示：high / medium / low，影响分数但仍可投

---- 第二轮重写说明（这一版为什么改判据）----

用 20 条真实形态的对抗 JD 跑第一版，漏判率 100%。复盘后发现漏的不是词，
是**判据本身**——所有规则都在"数命中几个词"，而话术换个说法就归零。
四类规则因此全部改了判据：

1. 培训机构：改为按**维度**判（先收费 / 非用工 / 承诺就业 / 教学场景 / 招生话术）。
   写法可以变，但"先收你钱 + 不签劳动合同 + 承诺推荐就业"这件事变不了。
2. 销售套壳：先剔除「开发新客户」这类伪技术词，再补一条**薪资结构**判据。
   最硬的销售证据在"底薪 3000 + 提成"里，不在职责描述里。
3. 届别：扫描全部届别写法（两位年份、括号、X 年 X 月毕业、大二大三），
   并把「明确排除」与「优先其他届别」分开定级——两者误判成本不同。
4. 工时：负面优先，不再让"双休"一票通过；单独加「休不到周日」这一条。
   用户的硬约束是**休周日**（12/06 上午考 N2），不是"双休"这两个字。

---- 第三轮重写说明（这一版为什么改语境）----

第二轮把漏判从 100% 压到 0，却把误判率推了上去。拿 19 条正常 JD 反向测，
19 条全部被误伤。问题不在词表加多了，在于规则只问"这个词出现了吗"，
不问另外三件事：

1. **前面有没有否定**。"公司不实行单休"命中"单休"、"入职不收取任何押金"
   命中"押金"——把自己的免责声明读成了罪证。现在匹配点同句内出现
   不/无/未/免/杜绝 等否定词即作废。
2. **它修饰的是谁**。"第 28 届上海国际电影节"的 28 是活动届次；
   "带教导师均有 1 年以上工作经验"的 1 年是导师资历，不是对候选人的门槛。
   现在届别要排除"第 X 届 + 活动名词"，经验要排除"导师/团队/负责人"作主语。
3. **证据有多硬**。"管培生""轮岗学习"是包装词，"提成""回款""陌拜"是
   销售动作的硬证据——原来两者同权计数，两个软词就能定案。
   现在销售要 ≥2 条硬证据；培训机构要**出现向候选人收费**，
   或者「非用工」与「承诺就业」同时出现。没有钱，就只是正常的带教或教研。

三条合起来是一句话：判据要贴着**事情**，不能贴着**字**。字会被否定、
会指别的东西、也有轻重之分。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 数据结构


@dataclass
class Finding:
    """一条规则命中。evidence 存原文片段，界面要原样展示给用户看。"""

    rule_id: str
    level: str          # hard_block / high / medium / low
    label: str
    detail: str
    evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 工时

# 命中即不可投：这些写法等价于单休
_WEEKEND_VIOLATION = [
    (r"做六休一", "做六休一"),
    (r"(?:月休|每月休)\s*([1-7])\s*天", "月休 {0} 天"),
    (r"(?:每周|一周|周)\s*(?:工作|上班)?\s*5\s*[.．]\s*5\s*天", "每周 5.5 天"),
    (r"5\s*[.．]\s*5\s*天(?:工作制|上班)", "5.5 天工作制"),
    (r"6\s*天\s*/\s*周", "6天/周"),
    (r"六天工作制", "六天工作制"),
    (r"单休", "单休"),
    (r"大小休", "大小休"),
    (r"大小周", "大小周"),
    (r"周休一天", "周休一天"),
    (r"每周\s*6\s*天", "每周6天"),
    (r"周六[^。；;\n，,]{0,10}(?:上班|例会|正常上班)", "周六需上班"),
]

# 名义上双休，但休不到周日。用户 12/06 上午要考 N2，这条比"是不是双休"更要紧
_REST_NOT_SUNDAY = [
    # 末尾的负向断言：「休息日为周六、周日」两个都休，不算违规；
    # 「休息日为周一、周二」才算。不排除的话前者会被第一个休息日骗到
    (r"(?:休息日?|轮休日?|休)\s*(?:为|是|在)?\s*(?:周|星期)[一二三四五六]"
     r"(?!\s*[、和及与至~～\-—]\s*(?:周|星期)[日天])", "休息日不在周末"),
    (r"周末正常上班", "周末正常上班"),
    (r"周日[^。；;\n]{0,8}(?:值班|轮值|排班)", "周日需值班"),
    (r"(?:值班|轮值)[^。；;\n]{0,6}周日", "周日需值班"),
]

# 双休的正面证据。注意：它只用来判定 ok，不能覆盖上面两张负面表
_WEEKEND_OK = [
    (r"双休", "双休"),
    (r"5\s*天\s*/\s*周", "5天/周"),
    (r"五天工作制", "五天工作制"),
    (r"做五休二", "做五休二"),
    (r"周休二日", "周休二日"),
]

# 每周只排 1–4 天。这是 BOSS 平台侧的真实字段（「3天/周」），不是 JD 文案。
# 它同时回答了两件事：周日一定能休（休的比两天还多），但**天数不够**——
# 之前这类岗位被当成「JD 未写工作时间」塞进待确认队列，等于让人去问 HR
# 一个平台上已经写着的问题。信息其实在，只是没人读。
_PART_WEEK = [
    r"([1-4])\s*天\s*/\s*周",
    r"(?:每周|一周|周)\s*(?:到岗|工作|上班|实习)?\s*([1-4])\s*天(?!\s*/)",
]

# 硬指标：实习材料要凑满 40 个打卡工作日。它只用来算「每周几天够不够攒」，
# 不参与打分——这不是岗位属性，是用户的约束，换个用户就是另一个数。
TARGET_WORKDAYS = 40

# 写了双休但夹着排班/值班，属于「双休但保证不了」，降级为提示而不是放行
_WEEKEND_FUZZY = [
    (r"排班", "排班"),
    (r"轮班", "轮班"),
    (r"轮休", "轮休"),
    (r"倒班", "倒班"),
    (r"值班", "值班"),
]


# 全职实习的反面。用户要在 11/23 到岗、凑满 40 个打卡工作日、开得出实习证明，
# 兼职/远程/按量结算这三件事任何一条都让这个目标落空——不是"差一点"，是根本凑不出来。
# 「时薪」不单独作为判据：全职岗也可能写时薪，它只是计薪方式。
_PART_TIME = [
    (r"线上远程兼职|远程兼职|线上兼职|居家兼职|在家兼职", "远程兼职"),
    (r"按(?:交付|件|单)量?结算|按件计酬|按单结算", "按量结算"),
]

# 每周工时低于这个数就不可能是全职（20 小时 ≈ 2.5 天）
_PART_TIME_HOURS_MAX = 20

# 这几个词里的「不/至少」表达的是下限，不是否定
_AT_LEAST = re.compile(r"不低于|不少于|至少|以上")


def _check_part_time(text: str) -> list[str]:
    """兼职/远程的证据。命中一条就够硬，不需要凑维度。"""
    ev: list[str] = []
    for pat, _label in _PART_TIME:
        for m in re.finditer(pat, text):
            if not _negated(text, m.start(), m.end()):
                ev.append(m.group(0))
                break
    for m in re.finditer(r"(?:每周|周均)[^。；，\n]{0,8}?(\d{1,2})\s*(?:个)?小时", text):
        # 「每周不低于 3 小时」里的"不"是下限语气，不是否定。
        # 通用 _negated 只认字面，这里给它一个例外，否则最典型的兼职写法反而漏掉。
        if _negated(text, m.start(), m.end()) and not _AT_LEAST.search(m.group(0)):
            continue
        if int(m.group(1)) <= _PART_TIME_HOURS_MAX:
            ev.append(m.group(0))
            break
    return ev


# ---------------------------------------------------------------- 培训机构

# 按维度收词。判据见 analyze()：**出现向候选人收费**是硬内核，
# 其余维度只在「非用工 + 承诺就业」同时出现时才够定案。
#
# 词表刻意排除了三类中性词：
# - 裸"分期"：消费分期产品的岗位职责会命中，改成"学费分期/分期付款"这种完整说法
# - "人才培养项目"、"一对一带教"、"课程体系"：正常企业的培养话术与教研岗职责，
#   拿它们当卖课证据等于把带教写得好一点的岗位全判死
_TRAINING_SIGNALS: dict[str, list[str]] = {
    "先收费": [
        "实训费", "学费", "培训费", "课程费", "报名费", "押金", "保证金",
        "先学后付", "先就业后付款", "学费分期", "分期付款", "分期缴纳", "分期缴费",
    ],
    "非用工": [
        "非直接用工", "岗前实训", "实训期", "入学签订", "学习协议",
        "培训与就业协议", "学制", "小班", "集训营", "免费重修", "免费重读", "结课",
    ],
    "承诺就业": [
        "推荐至合作企业", "推荐至合作公司", "推荐就业", "包就业", "往期学员",
        "内推池", "就业老师", "合作企业面试", "推荐面试",
    ],
    "教学场景": [
        "讲师", "导师一对一", "一对一带教", "批改", "答疑",
        "上课时间", "上课地点", "校区", "练习稿", "试听",
    ],
    "招生话术": [
        "零基础", "不限专业", "无需经验", "无经验亦可", "可培养",
        "转行", "结业作品集", "作品集", "包教包会", "报名咨询",
    ],
}


# ---------------------------------------------------------------- 销售岗

# 销售证据分两档。原来一锅端，两个包装词就能定案，
# 于是"管培生 + 轮岗学习"、"邀约嘉宾 + 拜访合作机构"全被判成销售。
#
# 硬证据：只有在销售岗上才会出现的动作，或"底薪+提成"这类计薪结构
_SALES_HARD = [
    "陌拜", "客户拜访", "电话销售", "电话邀约", "地推", "线下推广", "扫楼",
    "拉新", "发物料", "派单", "签单", "意向金", "回款", "续费", "增购",
    "招商", "加盟", "推销", "客户资源", "销售目标", "月度销售", "业绩",
    "提成", "会员卡", "渠道拓展",
]

# 软证据：正常商务、HR、活动执行、零售数据运营也会写这些词。
# 单独出现不算数，只用来佐证（并写进提示里让人自己判断）
_SALES_SOFT = [
    "拜访", "邀约", "首单", "签约", "回访", "转介绍", "引荐", "客户资料",
    "管培生", "储备干部", "从基层做起", "从公司基层开始", "轮岗学习",
    "门店", "驻店", "商务拓展",
]

# 技术词。刻意去掉裸的"数据"——销售岗 JD 里"整理客户资料录入系统"也全是数据，
# 把"数据"算技术词等于给套壳岗开后门。要命中就命中"数据分析""数据清洗"这种完整词。
_TECH_SIGNALS = [
    "测试", "开发", "算法", "运维", "接口", "数据库", "sql", "linux", "python",
    "java", "代码", "脚本", "自动化", "部署", "前端", "后端", "模型", "调试",
    "编码", "架构", "源码", "服务器", "pytest", "selenium", "postman",
    "数据分析", "数据挖掘", "数据清洗", "数据结构", "需求文档", "用例",
]

# 伪技术词：「开发新客户」里的"开发"不是技术职责。命中这些上下文就不算技术词
_TECH_FALSE_POSITIVE = {
    "开发": r"客户|市场|渠道|商户|商家|门店|新客|业务|楼盘",
}

# 外包/驻场/派遣：不是不能投，但要提醒用户核实工作地点与归属
_OUTSOURCE_MARKERS = ["外包", "驻场", "派遣", "外派", "借调", "项目制用工"]

# 社招年限要求：实习岗出现这些就是岗位写错了或实际要社招
_SENIOR_PATTERNS = [
    (r"(\d+)\s*[-~—]\s*(\d+)\s*年(?:以上)?(?:工作)?经验", "要求 {0}-{1} 年经验"),
    # 年限和「经验」之间常常夹着领域词：「2年以上AI应用开发相关经验」
    # 「3年以上测试团队管理经验」。前两条要求两者紧挨着，于是这类全部漏掉——
    # 真实数据里全场最高分（93，一家做 AI 应用开发的公司）就是这条漏的：
    # 平台字段写「经验不限」，JD 正文却要求 2 年以上。
    # 允许中间夹最多 12 个非断句字符，遇到句读就停，不跨句匹配。
    (r"(\d+)\s*年以上[^。；，\n]{0,12}?经验", "要求 {0} 年以上经验"),
]

# 经验年限的主语如果是这些人，说的是资历不是门槛：
# "带教导师均有 1 年以上工作经验" 与"你需 1 年以上经验"是两件事
_EXP_OWNER = ("导师", "团队", "负责人", "主管", "领导", "创始人", "总监", "老师", "前辈")

# 标题里的强销售信号。不含"渠道"——"渠道内容运营"是运营岗，
# 渠道拓展才是销售，而那个词在正文里会被 _SALES_HARD 抓到
_TITLE_SALES = ("销售", "商务拓展", "客户经理", "招商代表", "招商专员", "销售代表")


# ---------------------------------------------------------------- 工具

# 否定词。作用域只看同一分句：遇到逗号/分号/句号即失效，
# 免得"不接受远程，单休"里的"不"替后面的"单休"开脱
_NEG_WORDS = ("不", "无", "未", "非", "没", "免", "杜绝", "谢绝")
_CLAUSE_BREAK = "。；;！!？?\n，,"


def _negated(text: str, start: int, end: int) -> bool:
    """匹配点是否处在否定语境里。

    两种形态都要管，只查前或只查后各会漏一半：
    - 否定在匹配段内部：「周六无需上班」「值班不安排在周日」
    - 否定在匹配点左侧：「公司不实行单休」「入职不收取任何押金」
    """
    if any(w in text[start:end] for w in _NEG_WORDS):
        return True
    left = text[max(0, start - 16): start]
    cut = max(left.rfind(c) for c in _CLAUSE_BREAK)
    left = left[cut + 1:]
    # 转折之后以转折后为准，"方案正在调整"这类不算否定
    for tw in ("但", "不过", "然而", "只是"):
        i = left.rfind(tw)
        if i >= 0:
            left = left[i + 1:]
    return any(w in left for w in _NEG_WORDS)


def _hits(text: str, markers: list[str]) -> list[str]:
    """返回原文里实际出现、且**没有被否定**的词，用于界面展示证据。"""
    low = text.lower()
    out: list[str] = []
    for m in markers:
        ml = m.lower()
        if ml not in low:
            continue
        for hit in re.finditer(re.escape(ml), low):
            if not _negated(low, hit.start(), hit.end()):
                out.append(m)
                break
    # 短词被长词包含时丢掉（「客户拜访」与「拜访」），否则同一个动作
    # 在证据里数两遍，硬证据的条数门槛就被注水了
    return [w for w in out if not any(w != o and w in o for o in out)]


def _regex_hits(text: str, patterns: list[tuple[str, str]]) -> list[tuple[str, str]]:
    low = text.lower()
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pat, label_tpl in patterns:
        for m in re.finditer(pat, low):
            if _negated(low, m.start(), m.end()):
                continue
            label = label_tpl.format(*m.groups()) if m.groups() else label_tpl
            key = (label, m.group(0))
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def _tech_hits(text: str) -> list[str]:
    """技术词命中，但排除「开发新客户」这类伪技术词。"""
    low = text.lower()
    out: list[str] = []
    for word in _TECH_SIGNALS:
        w = word.lower()
        bad = _TECH_FALSE_POSITIVE.get(word)
        hit = False
        for m in re.finditer(re.escape(w), low):
            if bad:
                ctx = low[max(0, m.start() - 8): m.end() + 8]
                if re.search(bad, ctx):
                    continue
            hit = True
            break
        if hit:
            out.append(word)
    return out


def _part_week(text: str) -> str:
    """抓「每周 1–4 天」，返回规范写法「3天/周」，没抓到返回空串。"""
    for pat in _PART_WEEK:
        m = re.search(pat, text)
        if m:
            return f"{int(m.group(1))}天/周"
    return ""


def _check_weekend(text: str) -> tuple[str, list[str]]:
    """返回 (状态, 证据)。状态是排他的，顺序即优先级。

    violation      单休/大小周/月休不足 —— 硬阻断
    rest_not_sunday 名义双休但休不到周日 —— 硬阻断（N2 考在周日上午）
    fuzzy          双休但排班/值班 —— 提示
    ok             双休且无异常
    few_days       每周只排 1–4 天 —— 周日能休，但天数不够凑打卡
    unknown        JD 没写工作时间 —— 提示
    """
    bad = _regex_hits(text, _WEEKEND_VIOLATION)
    if bad:
        return "violation", [b[1] for b in bad]
    rest = _regex_hits(text, _REST_NOT_SUNDAY)
    if rest:
        return "rest_not_sunday", [r[1] for r in rest]
    # 「值班不安排在周日」「排班可避开周末」：后面跟了明确的规避说法就不再提示，
    # 否则用户会收到一条自己已经回答过的追问
    fuzzy: list[tuple[str, str]] = []
    for label, raw in _regex_hits(text, _WEEKEND_FUZZY):
        pos = text.find(raw)
        after = text[pos + len(raw): pos + len(raw) + 12] if pos >= 0 else ""
        if re.match(r"\s*(?:不安排|不排|无需|不用|不会|不需|可避|避开|不涉及)", after):
            continue
        fuzzy.append((label, raw))
    if fuzzy:
        return "fuzzy", [f[1] for f in fuzzy]
    ok = _regex_hits(text, _WEEKEND_OK)
    if ok:
        return "ok", [o[1] for o in ok]
    part = _part_week(text)
    if part:
        return "few_days", [part]
    return "unknown", []


def detect_weekend(text: str) -> tuple[bool | None, list[str]]:
    """兼容旧接口：True=双休，False=不满足，None=JD 没提。

    few_days 也返回 True：每周只排几天，周日反而更清闲。真正的问题不是
    「能不能休周日」，而是「天数够不够凑打卡」，那件事由 PART_WEEK 单说。
    """
    status, evidence = _check_weekend(text)
    if status in ("violation", "rest_not_sunday"):
        return False, evidence
    if status in ("ok", "few_days"):
        return True, evidence
    return None, evidence


def detect_months(text: str) -> int | None:
    """从 JD 里抽实习月数要求。"""
    pats = [
        r"(?:至少|连续|实习|需)\s*(\d+)\s*个月",
        r"(\d+)\s*个月(?:以上|起|及)",
        r"实习期\s*(\d+)\s*个月",
    ]
    for pat in pats:
        m = re.search(pat, text)
        if m:
            return int(m.group(1))
    return None


# ---------------------------------------------------------------- 届别


_EVENT_NOUN = (r"电影节|电影节|展会|展览|博览会|大赛|比赛|论坛|峰会|年会|会议|"
               r"活动|典礼|艺术节|音乐节|赛季|运动会")


def _declared_grades(text: str) -> list[tuple[int, str]]:
    """把 JD 里所有届别写法抽成年份。四种写法覆盖了平台 HR 的常用缩写：
    2028 届 / （2028 届） / 2028 年 6 月及以后毕业 / 27/28 届 / 28 届

    两类**不是届别**的必须排掉，否则"第 28 届上海国际电影节"的展务岗
    会因为活动届次被硬阻断：
    - 「第 X 届」是活动届次，序数词后面永远不是毕业年份
    - 届/毕业后面紧跟电影节、展会、大赛这类名词时，说的是活动
    """
    found: list[tuple[int, str]] = []
    for m in re.finditer(rf"(?<!第)(20\d{{2}})\s*届(?!\s*(?:{_EVENT_NOUN}))", text):
        found.append((int(m.group(1)), m.group(0)))
    for m in re.finditer(
        rf"(20\d{{2}})\s*年(?:\s*\d{{1,2}}\s*月)?\s*(?:及以后|以后|之后)?\s*毕业"
        rf"(?!\s*(?:季|典礼|照|设计|晚会|活动|展))", text
    ):
        found.append((int(m.group(1)), m.group(0)))
    for m in re.finditer(rf"(?<![\d第])(\d{{2}})\s*[/、,，]\s*(\d{{2}})\s*届", text):
        for g in m.groups():
            found.append((2000 + int(g), m.group(0)))
    for m in re.finditer(rf"(?<![\d第])(\d{{2}})\s*届(?!\s*(?:{_EVENT_NOUN}))", text):
        found.append((2000 + int(m.group(1)), m.group(0)))
    # 同一原文只留一条，避免「27/28 届」被拆出四条
    seen: set[str] = set()
    uniq: list[tuple[int, str]] = []
    for year, raw in found:
        if raw in seen:
            continue
        seen.add(raw)
        uniq.append((year, raw))
    return uniq


def _grade_findings(text: str, user_grad_year: int) -> list[Finding]:
    """届别卡口。

    分两档：
    - 明确排除（仅面向 2028 届 / 2028 年及以后毕业 / 大二大三在读）→ hard_block
    - 只是偏好（27/28 届均可，优先 28 届 / （2028 届）及以上）→ medium
    两档必须分开。把"优先"也硬阻断，等于把能投的岗位扔掉；
    把"仅面向"当提示，等于白投——这两种误判的成本不一样。
    """
    out: list[Finding] = []
    declared = _declared_grades(text)
    user_short = str(user_grad_year)[2:]
    has_user_grade = any(y == user_grad_year or str(y)[2:] == user_short for y, _ in declared)

    # 大二大三在读：用户是大四，通常不在范围。
    # 豁免只有两种情形，都表示用户被包含在可投范围内：
    #   ① JD 明写了用户这一届（"2027 届优先"）
    #   ② 紧跟"亦可/均可/不限"，即年级不限
    # 刻意不用"优先"作豁免词：「2028 年毕业优先」里的优先恰恰是在排除用户
    young = re.search(r"大[一二三]\s*(?:在读|年级)|大[一二]、\s*大[一二三]", text)
    if young:
        allowed = has_user_grade or bool(
            re.search(r"亦可|均可|皆可|不限",
                      text[max(0, young.start() - 10): young.end() + 20])
        )
        if not allowed and not re.search(r"大四", text):
            out.append(Finding(
                rule_id="GRADE_MISMATCH",
                level="hard_block",
                label="年级不符",
                detail=f"JD 面向大二/大三在读，你是大四在读（{user_grad_year} 届），不在范围内",
                evidence=[young.group(0)],
            ))
            return out

    later = [(y, raw) for y, raw in declared if y > user_grad_year]
    if not later:
        return out
    year, raw = max(later)
    pos = text.find(raw)
    tail = text[pos + len(raw): pos + len(raw) + 8] if pos >= 0 else ""
    ctx = text[max(0, pos - 12): pos + len(raw) + 12] if pos >= 0 else text
    # "及以上"要贴着届别才算，避免把「实习 6 个月以上」的"以上"当成届别限定
    ambiguous = bool(re.search(r"^\s*[）)]?\s*(?:及)?以上", tail)) or ("优先" in ctx) or has_user_grade

    if ambiguous:
        out.append(Finding(
            rule_id="GRADE_AMBIGUOUS",
            level="medium",
            label="届别偏好",
            detail=f"JD 提到 {year} 届但未排除 {user_grad_year} 届，属于优先顺序而非硬卡口，可投但要有心理准备",
            evidence=[raw],
        ))
    else:
        out.append(Finding(
            rule_id="GRADE_MISMATCH",
            level="hard_block",
            label="年级不符",
            detail=f"JD 要求 {year} 届，你是 {user_grad_year} 届",
            evidence=[raw],
        ))
    return out


# ---------------------------------------------------------------- 主入口


def analyze(
    jd_text: str,
    *,
    title: str = "",
    company: str = "",
    industry: str = "",
    user_grad_year: int = 2027,
    months_available: int = 3,
    schedule: str = "",
) -> list[Finding]:
    """跑一遍全部规则。text 只传 JD 正文 + 岗位名，不要把公司名混进去误伤。

    schedule 是平台侧的工时字段（「6天/周 6个月」），单独存在 Job.days_per_week 里，
    不在 JD 正文里。拼进文本是为了复用同一套正则——「6天/周」本来就是
    _WEEKEND_VIOLATION 认的写法，没必要为一个字段再写一条规则。
    """
    text = f"{title}\n{schedule}\n{jd_text}" if schedule else f"{title}\n{jd_text}"
    findings: list[Finding] = []

    # 1. 工时 —— 硬阻断
    status, weekend_ev = _check_weekend(text)
    if status == "violation":
        findings.append(Finding(
            rule_id="WEEKEND_VIOLATION",
            level="hard_block",
            label="工作时间不满足",
            detail="JD 写明单休/大小休/月休不足 8 天（或平台标注 6 天/周），与你「必须双休且休周日」的硬约束冲突（12/06 上午要考 N2）",
            evidence=weekend_ev,
        ))
    elif status == "rest_not_sunday":
        findings.append(Finding(
            rule_id="WEEKEND_NOT_SUNDAY",
            level="hard_block",
            label="工作时间不满足（休不到周日）",
            detail="名义双休，但休息日不在周末或周日要值班，你 12/06 上午考 N2 需要休周日",
            evidence=weekend_ev,
        ))

    # 1.5 兼职/远程 —— 硬阻断。
    # 真实抓取抓出来的：一条「每周不低于 3 小时、线上远程兼职」的数据标注岗，
    # 因为没有单休、没有销售词、没有届别限制，拿了全场最高分 69.4。
    # 而它连打卡 40 天这件事都做不到——分数高在这里不是好消息。
    pt_ev = _check_part_time(text)
    if pt_ev:
        findings.append(Finding(
            rule_id="PART_TIME_REMOTE",
            level="hard_block",
            label="不是全职岗（兼职/远程/按量结算）",
            detail="这条是兼职或远程按量交付，凑不满 40 个打卡工作日，也开不出实习证明",
            evidence=pt_ev,
        ))

    # 2. 届别卡口 —— 硬阻断 / 偏好
    findings.extend(_grade_findings(text, user_grad_year))

    # 3. 培训机构 —— 判据是「有没有向候选人收钱」。
    # 只看维度数量的旧判据会把"讲师 + 小班 + 不限专业"的教研实习判死，
    # 而 20 条真实卖课 JD 全部写了学费/实训费/集训营费——钱才是硬内核。
    # 不写钱的那批靠「非用工 + 承诺就业」两条同时出现兜底（如"非直接用工"+"内推池"）。
    dims = {name: _hits(text, words) for name, words in _TRAINING_SIGNALS.items()}
    hit_dims = [name for name, hits in dims.items() if hits]
    pays = bool(dims["先收费"])
    fake_job = bool(dims["非用工"]) and bool(dims["承诺就业"])
    if pays or fake_job:
        evidence = [h for name in hit_dims for h in dims[name]]
        why = "JD 里出现向候选人收取的费用" if pays else "既不是直接用工，又承诺帮你找到工作"
        detail = f"命中 {len(hit_dims)} 类招生特征（{'、'.join(hit_dims)}）：{why}——" \
                 "收钱的那一方不是雇主"
        findings.append(Finding(
            rule_id="TRAINING_SELL",
            level="high",
            label="疑似培训机构（卖课，非招聘）",
            detail=detail,
            evidence=evidence,
        ))

    # 4. 销售岗套壳 —— 要 2 条硬证据
    hard = _hits(text, _SALES_HARD)
    soft = [w for w in _hits(text, _SALES_SOFT) if w not in hard]
    tech_hits = _tech_hits(text)
    pay_is_commission = bool(re.search(r"底薪|无责底薪", text)) and "提成" in text
    title_sales = any(w in title for w in _TITLE_SALES)
    if not tech_hits and (len(hard) >= 2 or pay_is_commission or title_sales):
        findings.append(Finding(
            rule_id="SALES_DISGUISE",
            level="high",
            label="疑似销售岗包装成实习",
            detail="职责是拜访/续费/冲业绩，薪资是底薪+提成，没有任何技术职责描述——"
                   "这类岗位对「AI 应用开发」方向的履历没有加分",
            evidence=(hard[:8] or soft[:5] or ["底薪+提成"]),
        ))
    elif title_sales and len(hard) >= 2:
        # 「技术词=0」这个前提在卖 AI 产品的销售岗面前会自我瓦解：
        # 岗位名写着「B端销售顾问」，正文通篇「大模型/SaaS/智能体」——
        # 技术词一堆，规则放行，76 分。可它干的是电销获客、冲业绩。
        # 标题是招聘方自己定的岗位性质，比正文出现几个技术名词更能说明这份工作是什么；
        # 但仍要求正文有硬销售证据（提成/电销/成交/业绩目标），
        # 否则「销售数据分析实习生」这种给销售部门做数据的岗会被误伤。
        findings.append(Finding(
            rule_id="SALES_DISGUISE",
            level="high",
            label="疑似销售岗包装成实习",
            detail="岗位名就是销售，正文写的是获客与成交，技术词只是产品介绍的一部分",
            evidence=hard[:8] or soft[:5],
        ))

    # 5. 社招年限 —— 要分清说的是谁
    senior_hard: list[str] = []
    senior_soft: list[str] = []
    low = text.lower()
    for label, raw in _regex_hits(text, _SENIOR_PATTERNS):
        nums = re.findall(r"\d+", label)
        if not nums or int(nums[0]) < 1:
            continue
        pos = low.find(raw)
        # 主语是导师/团队时，说的是资历不是门槛
        if pos >= 0 and any(o in low[max(0, pos - 12): pos] for o in _EXP_OWNER):
            continue
        tail = low[pos + len(raw): pos + len(raw) + 14] if pos >= 0 else ""
        if any(w in tail for w in ("优先", "应届", "亦可", "均可", "不限")):
            senior_soft.append(raw)
        else:
            senior_hard.append(raw)
    if senior_hard:
        findings.append(Finding(
            rule_id="EXP_SENIOR",
            level="high",
            label="经验门槛偏高",
            detail="JD 要求 1 年以上经验且没写「优先/应届亦可」，实习岗出现这条通常意味着实际按社招筛人",
            evidence=senior_hard,
        ))
    elif senior_soft:
        findings.append(Finding(
            rule_id="EXP_PREFERRED",
            level="medium",
            label="经验年限是加分项而非门槛",
            detail="提到了 1 年以上经验，但同时写了优先/应届亦可，仍可投，别把它当硬门槛",
            evidence=senior_soft,
        ))

    # 6. 外包驻场 —— 中
    out_hits = _hits(text, _OUTSOURCE_MARKERS)
    if out_hits:
        findings.append(Finding(
            rule_id="OUTSOURCE",
            level="medium",
            label="外包/驻场性质",
            detail="需核实实际工作地点、项目归属与实习证明开具主体是否一致",
            evidence=out_hits,
        ))

    # 7. 时长不足 —— 中
    months = detect_months(text)
    if months and months > months_available:
        findings.append(Finding(
            rule_id="MONTHS_TOO_LONG",
            level="medium",
            label="实习时长要求超出可提供",
            detail=f"JD 要求 {months} 个月，你最多提供 {months_available} 个月，需先谈",
            evidence=[f"{months} 个月"],
        ))

    # 8. 信息缺失 —— 低。不阻断，但提醒用户自己去问
    if status == "fuzzy":
        findings.append(Finding(
            rule_id="WEEKEND_FUZZY",
            level="medium",
            label="双休但涉及排班/值班",
            detail="写了双休，但同时有排班或值班，需确认是否会轮到周日——你 12/06 上午考 N2",
            evidence=weekend_ev,
        ))
    elif status == "few_days":
        raw = weekend_ev[0] if weekend_ev else ""
        m = re.search(r"(\d+)\s*天", raw)
        n = int(m.group(1)) if m else 0
        # 向上取整：3 天/周 → 14 周，4 天/周 → 10 周
        weeks = -(-TARGET_WORKDAYS // n) if n else 0
        findings.append(Finding(
            rule_id="PART_WEEK",
            level="medium",
            label=f"每周只排 {n} 天，凑满 {TARGET_WORKDAYS} 个打卡工作日要 {weeks} 周",
            detail=(f"平台标注 {raw}，周日反而更闲，考 N2 不受影响。"
                    f"但它不是全职：按 {n} 天/周算，{TARGET_WORKDAYS} 个工作日要 {weeks} 周才攒得出来。"
                    "实习材料能等到那时就没事，等不到就得问 HR 能不能多排两天。"),
            evidence=[raw],
        ))
    elif status == "unknown":
        findings.append(Finding(
            rule_id="WEEKEND_UNKNOWN",
            level="low",
            label="JD 未写工作时间",
            detail="无法判断是否双休，发招呼语时必须把这条列进待确认问题",
            evidence=[],
        ))

    return findings


def hard_blocked(findings: list[Finding]) -> Finding | None:
    """只要有任意一条 hard_block，这个岗位就不该投。"""
    for f in findings:
        if f.level == "hard_block":
            return f
    return None
