"""简历导出（.docx）。

排版按「极简」来：不用彩色、不加色块、不加边框、不用图标符号。
一份简历里最该抢眼的是内容，不是装饰——招聘方看简历的平均时长是十几秒，
任何视觉噪音都在消耗这十几秒。

字体处理有个坑：python-docx 设 run.font.name 只作用于西文，
中文必须另外设 rFonts 的 eastAsia，否则设置不生效、导出还是默认字体。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, Cm

from .config import OUTPUT_DIR

_BODY_FONT = "宋体"
_TITLE_FONT = "黑体"
_SECTION_ORDER = ("教育背景", "实习经历", "项目经历", "专业技能", "证书")


def _set_run(run, *, font: str = _BODY_FONT, size: float = 12, bold: bool = False) -> None:
    run.font.name = font
    run.font.size = Pt(size)
    run.bold = bold
    # 中文字体必须单独设 eastAsia，否则中文回落到默认字体
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), font)
    rfonts.set(qn("w:ascii"), font)


def _para(doc, text: str = "", *, font=_BODY_FONT, size=12, bold=False,
          align=None, space_after=2.0, line_spacing=1.5):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.line_spacing = line_spacing
    if align is not None:
        p.alignment = align
    if text:
        _set_run(p.add_run(text), font=font, size=size, bold=bold)
    return p


def _section(doc, title: str) -> None:
    """章节标题：加粗 + 一点上间距。不用下划线、不用底纹。"""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(3)
    _set_run(p.add_run(title), font=_TITLE_FONT, size=13, bold=True)


def _relevance(item, jd: str) -> int:
    """按 JD 关键词命中数排项目顺序。定制简历的核心就这一步——
    不是改内容，是改顺序：招聘方最想看的应该在最前面。"""
    if not jd:
        return 0
    low = jd.lower()
    kws = (item.content_json or {}).get("keywords", [])
    return sum(1 for k in kws if str(k).lower() in low)


def _slug(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fa5]+", "", text or "")[:20] or "resume"


def _ym(value: str | None) -> str:
    """2026-08-12 / 2026-08 / 2026.8 → 2026.08。简历上不需要精确到日。"""
    if not value:
        return ""
    m = re.match(r"(\d{4})[-./](\d{1,2})", str(value))
    return f"{m.group(1)}.{int(m.group(2)):02d}" if m else str(value)


def _period(start: str | None, end: str | None) -> str:
    """起止相同的（同月完成的项目）只写一个，避免 2026.11 – 2026.11。"""
    a = _ym(start)
    b = _ym(end)
    if not a:
        return ""
    if not b or a == b:
        return a
    return f"{a} – {b}"


def _bullets(content: dict) -> list[str]:
    """把素材拆成简历条目。

    刻意不输出 pain：那是「为什么做」的背景，适合写在招呼语里说明我懂这个岗位，
    但放进简历会变成一句需求陈述，读起来不像成果。做法按分号拆成多条，
    结果单独一条并加前缀，保证每条都以「我做了什么」或「做到什么程度」开头。
    """
    out: list[str] = []
    approach = (content.get("approach") or "").strip()
    if approach:
        out += [p.strip() for p in re.split(r"[；;]", approach) if p.strip()]
    elif (content.get("pain") or "").strip():
        out.append(content["pain"].strip())
    result = (content.get("result") or "").strip()
    if result:
        out.append(f"结果：{result}")
    return out


def build(
    profile,
    items,
    *,
    job=None,
    out_path: Path | None = None,
) -> Path:
    """生成简历。只使用 verified=True 的素材。"""
    doc = Document()
    for s in doc.sections:
        s.top_margin = Cm(2.0)
        s.bottom_margin = Cm(2.0)
        s.left_margin = Cm(2.2)
        s.right_margin = Cm(2.2)

    # 默认样式也改成宋体，避免空段落回落到 Calibri
    normal = doc.styles["Normal"]
    normal.font.name = _BODY_FONT
    normal.font.size = Pt(12)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), _BODY_FONT)

    # ---- 抬头 ----
    _para(doc, profile.name, font=_TITLE_FONT, size=18, bold=True,
          align=WD_ALIGN_PARAGRAPH.CENTER, space_after=4)
    contact = "｜".join(x for x in [
        profile.phone, profile.email,
        f"{profile.city}" if profile.city else "",
        f"{_ym(profile.birth)}" if profile.birth else "",
        f"{profile.politics}" if profile.politics else "",
    ] if x)
    _para(doc, contact, size=10.5, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=6)

    visible = [it for it in items if it.verified]

    # 求职意向：有目标岗位时写具体岗位，没有就不写（不编）
    if job is not None:
        _para(doc, f"求职意向：{job.title}（{job.city}）", size=11, space_after=2)

    # ---- 教育背景 ----
    if profile.school:
        _section(doc, "教育背景")
        line = f"{profile.school}　{profile.college}　{profile.major}　{profile.degree}"
        _para(doc, line, size=11.5, bold=True, space_after=1)
        _para(doc, _period(profile.edu_start, profile.edu_end), size=10.5, space_after=1)
        courses = [it for it in visible if it.type == "course"]
        for c in courses:
            detail = (c.content_json or {}).get("detail", "")
            if detail:
                _para(doc, f"主修课程：{detail}", size=10.5)

    # ---- 经历（实习 / 项目），按与 JD 的相关度排序 ----
    def _render(group_type: str, title: str) -> None:
        group = [it for it in visible if it.type == group_type]
        if not group:
            return
        if job is not None:
            group = sorted(group, key=lambda x: _relevance(x, job.jd_text), reverse=True)
        _section(doc, title)
        for it in group:
            head = it.title
            if it.org:
                head = f"{it.org}　{it.title}"
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(1)
            p.paragraph_format.line_spacing = 1.4
            _set_run(p.add_run(head), size=11.5, bold=True)
            period = _period(it.start_date, it.end_date)
            if period:
                _set_run(p.add_run(f"　{period}"), size=10)
            c = it.content_json or {}
            if c.get("subtitle"):
                _para(doc, str(c["subtitle"]), size=10.5, space_after=1, line_spacing=1.4)
            for line in _bullets(c):
                _para(doc, f"· {line}", size=10.5, space_after=1, line_spacing=1.4)

    _render("internship", "实习经历")
    _render("project", "项目经历")

    # ---- 专业技能 ----
    skills = [it for it in visible if it.type == "skill"]
    if skills:
        _section(doc, "专业技能")
        for it in skills:
            detail = (it.content_json or {}).get("detail", "")
            if detail:
                _para(doc, f"{it.title}　{detail}" if it.title else detail, size=10.5)

    # ---- 证书 ----
    certs = [it for it in visible if it.type == "cert"]
    if certs:
        _section(doc, "证书")
        for it in certs:
            detail = (it.content_json or {}).get("detail", "")
            _para(doc, detail or it.title, size=10.5)

    # ---- 落盘 ----
    if out_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = "简历"
        if job is not None:
            target = f"简历_{_slug(job.company)}_{_slug(job.title)}"
        out_path = OUTPUT_DIR / f"{target}_{stamp}.docx"

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path


def to_markdown(profile, items, *, job=None) -> str:
    """纯文本版本，给界面预览用（也方便塞进 git 里看 diff）。"""
    lines = [f"# {profile.name}", ""]
    contact = "｜".join(x for x in [profile.phone, profile.email, profile.city] if x)
    lines.append(contact)
    lines.append("")
    if job is not None:
        lines += [f"**求职意向**：{job.title}（{job.city}）", ""]
    if profile.school:
        lines.append("## 教育背景")
        lines.append(f"{profile.school}　{profile.college}　{profile.major}　{profile.degree}　"
                     f"{_period(profile.edu_start, profile.edu_end)}")
        lines.append("")
    for group in _SECTION_ORDER[1:]:
        mapping = {"实习经历": "internship", "项目经历": "project",
                   "专业技能": "skill", "证书": "cert"}
        t = mapping.get(group)
        if not t:
            continue
        group_items = [it for it in items if it.verified and it.type == t]
        if not group_items:
            continue
        lines.append(f"## {group}")
        for it in group_items:
            c = it.content_json or {}
            head = f"{it.org}　{it.title}" if it.org else it.title
            period = _period(it.start_date, it.end_date)
            if period:
                head += f"　{period}"
            lines.append(f"- **{head}**")
            if c.get("subtitle"):
                lines.append(f"  {c['subtitle']}")
            if t in ("internship", "project"):
                for line in _bullets(c):
                    lines.append(f"  - {line}")
            else:
                lines.append(f"  - {c.get('detail', '')}")
        lines.append("")
    return "\n".join(lines)
