"""抓取层的公共契约。

分界线很重要：**取页面**（网络、浏览器、风控）和**解析页面**（纯字符串 → 结构化）
必须是两件事。解析函数只吃 HTML 字符串，所以：

- 抓取失败/被封时不阻塞解析层的单元测试
- 真实页面能存成快照，线上结构一改就在本地复现
- 换站点只写新的 parser，不改入库链路
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field


class FetchError(RuntimeError):
    """抓取不可继续。子类区分「环境没配好」和「需要人介入」。"""


class SetupError(FetchError):
    """依赖/端口/浏览器没准备好，用户自己就能修。"""


class NeedHumanError(FetchError):
    """撞上登录墙或安全验证。脚本不该自己硬闯——绕验证是账号被封的最快路径。"""


@dataclass
class FetchSpec:
    keyword: str
    city: str = "南昌"
    pages: int = 1
    with_detail: bool = True
    limit: int = 30


@dataclass
class RawJob:
    """一个职位的原始结构化结果，与数据库 Job 解耦。

    解析层不认识 SQLAlchemy，入库时才转成 Job —— 这样解析器可以单独
    在 REPL 里跑，也能被 selftest 直接 import。
    """

    company: str = ""
    title: str = ""
    city: str = ""
    district: str = ""
    salary_raw: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    salary_unit: str = "day"
    edu_req: str = ""
    exp_req: str = ""
    schedule: str = ""      # 平台工时字段，如「6天/周 6个月」
    hr_name: str = ""
    url: str = ""
    jd_text: str = ""
    source: str = "boss"
    raw: dict = field(default_factory=dict)

    def dedup_key(self) -> str:
        """去重键。

        网址优先（同一职位在不同关键词下会重复出现）；
        没有网址时退化成「公司+标题+城市」，比只用标题稳——
        同名岗位在不同城市很常见。
        """
        if self.url:
            return _normalize_url(self.url)
        basis = f"{self.company}|{self.title}|{self.city}"
        return "h:" + hashlib.md5(basis.encode("utf-8")).hexdigest()[:16]


def _normalize_url(url: str) -> str:
    """只留 /job_detail/<id>.html 这段，去掉跟踪参数。

    BOSS 同一个职位会带不同的 securityId / lid 参数，原样比会判成两个。
    """
    m = re.search(r"/job_detail/([0-9A-Za-z_-]+)\.html", url or "")
    if m:
        return f"job:{m.group(1)}"
    return _strip_query(url)


def _strip_query(url: str) -> str:
    return (url or "").split("?", 1)[0].rstrip("/")


# ---------------------------------------------------------------- 文本清洗

# BOSS 会把「BOSS直聘」四个字插进 JD 正文中间当水印，肉眼看不见、复制得出来。
# 不清掉的话它会混进话术和简历，也可能让关键词匹配对不上（"公BOSS直聘司直招"）。
_WATERMARK = re.compile(r"BOSS\s*直聘|看准网|kanzhun", re.I)

# 另一层是字符替换：把常用汉字换成康熙部首区的「长得一样」的码位
# （走→⾛ U+2F9B、无→⽆ U+2F45、比→⽐ U+2F50）。NFKC 能把这些还原成标准汉字。
# 只对部首区做，不整段 NFKC —— 整段会把全角标点压成半角，中文 JD 里那样更难看。
_RADICAL_RANGES = ((0x2E80, 0x2EFF), (0x2F00, 0x2FDF))


def clean_text(text: str) -> str:
    """还原反爬替换的字符，去掉站点水印。"""
    if not text:
        return ""
    out = []
    for ch in text:
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in _RADICAL_RANGES):
            norm = unicodedata.normalize("NFKC", ch)
            out.append(norm if len(norm) == 1 else ch)
        else:
            out.append(ch)
    return _WATERMARK.sub("", "".join(out))


# ---------------------------------------------------------------- 薪资

_UNIT_HINTS = (
    ("年", "year"),
    ("月", "month"),
    ("天", "day"),
    ("日", "day"),
    ("时", "hour"),
)

_SCALE = {"万": 10000, "w": 1000, "W": 1000, "千": 1000, "k": 1000, "K": 1000}

_NUM_RANGE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(万|千|[kKwW])?\s*(?:[-~—至]\s*(\d+(?:\.\d+)?)\s*(万|千|[kKwW])?)?"
)


def parse_salary(text: str) -> tuple[float | None, float | None, str]:
    """「150-200元/天」「6-9K·13薪」「1.5-2万/月」→ (min, max, unit)。

    只取第一个数字区间。「·13薪」这类后缀不影响日/月单位的判断，
    所以顺手丢掉——把它们揉进月薪会算出一个不存在的数。
    """
    raw = (text or "").strip()
    if not raw or "面议" in raw or "议" in raw:
        return None, None, "day"

    unit = "month"
    for hint, u in _UNIT_HINTS:
        if hint in raw:
            unit = u
            break
    else:
        # 没写单位时：带 K/万 的多数是月薪，纯数字多是日薪（实习岗常见）
        if re.search(r"[万kK千]", raw):
            unit = "month"
        else:
            unit = "day"

    m = _NUM_RANGE.search(raw.split("·")[0])
    if not m:
        return None, None, unit

    def _val(num: str, scale: str | None) -> float:
        v = float(num)
        if scale:
            v *= _SCALE.get(scale, 1)
        return v

    # "3-6K" 只有右边带量级，左边要沿用——否则左边会被当成 3 元
    scale_lo, scale_hi = m.group(2), m.group(4)
    scale_lo = scale_lo or scale_hi
    scale_hi = scale_hi or scale_lo

    lo = _val(m.group(1), scale_lo)
    hi = _val(m.group(3), scale_hi) if m.group(3) else lo
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi, unit


# ---------------------------------------------------------------- 标签分类

_DEGREES = ("大专", "本科", "硕士", "博士", "中专", "高中", "学历不限", "初中")
_EXPS = ("在校", "应届", "经验不限", "实习", "1年以内", "1-3年", "3-5年", "5-10年", "10年以上")


def split_tags(tags: list[str]) -> tuple[str, str]:
    """从卡片标签里挑出学历与经验。剩下的（福利、行业）丢弃。

    标签顺序不固定，所以按词表判而不是按位置取。
    """
    edu = exp = ""
    for t in tags:
        t = (t or "").strip()
        if not t:
            continue
        if not edu and any(d in t for d in _DEGREES):
            edu = t
        elif not exp and any(e in t for e in _EXPS):
            exp = t
    return edu, exp


def split_area(area: str) -> tuple[str, str]:
    """「南昌·青山湖区」→ ("南昌", "青山湖区")。"""
    raw = (area or "").strip()
    if not raw:
        return "", ""
    for sep in ("·", "-", " "):
        if sep in raw:
            a, b = raw.split(sep, 1)
            return a.strip(), b.strip()
    return raw, ""
