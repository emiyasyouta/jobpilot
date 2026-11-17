"""极简 DOM：只为职位页解析服务。

为什么不用 bs4 / lxml：本项目要解析的结构只有「职位卡片」和「详情正文」两种，
标准库 html.parser 写一百行足够，而且 DEMO 用户 clone 下来不用多装一个包。
代价是没有选择器引擎——所以上层只按「class 子串」找节点，不做复杂 CSS 查询。

另一个好处是它只吃字符串：真实页面能存成 HTML 快照，离线回归测试不需要联网。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# 自闭合标签：出现在 HTML 里通常不带 "/"，解析器要自己收尾
VOID = {
    "br", "img", "input", "meta", "link", "hr", "source", "area",
    "base", "col", "embed", "param", "track", "wbr",
}

# 块级标签：取文本时前后各断一行，否则一整段会连成一串
BLOCK = {
    "div", "p", "li", "ul", "ol", "tr", "td", "th", "table", "section",
    "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
    "dt", "dd", "dl", "pre", "blockquote", "form", "nav", "main",
}

_SPACES = re.compile(r"[ \t\r\f\v]+")


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict | None = None, parent: "Node | None" = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list["Node | str"] = []
        self.parent = parent

    # ---------------- 属性

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def has_class(self, hint: str) -> bool:
        """class 子串命中。

        故意用子串而不是全等：BOSS 这类站点的 class 常常带哈希后缀
        （job-card-wrapper-3f2a），全等匹配一改版就全废。
        """
        return any(hint in c for c in self.classes)

    def get(self, name: str, default: str = "") -> str:
        return self.attrs.get(name) or default

    # ---------------- 查找

    def walk(self):
        for ch in self.children:
            if isinstance(ch, Node):
                yield ch
                yield from ch.walk()

    def find_all(self, cls: str | None = None, tag: str | None = None):
        for n in self.walk():
            if tag and n.tag != tag:
                continue
            if cls and not n.has_class(cls):
                continue
            yield n

    def find(self, cls: str | None = None, tag: str | None = None) -> "Node | None":
        return next(self.find_all(cls, tag), None)

    def closest(self, cls: str) -> "Node | None":
        n = self.parent
        while n is not None:
            if n.has_class(cls):
                return n
            n = n.parent
        return None

    # ---------------- 文本

    def text(self) -> str:
        out: list[str] = []
        _collect(self, out)
        return normalize("".join(out))

    def own_text(self) -> str:
        out = [ch for ch in self.children if isinstance(ch, str)]
        return normalize("".join(out))

    def __repr__(self) -> str:  # 调试用
        return f"<{self.tag} class={' '.join(self.classes)[:40]}>"


def _collect(node: Node, out: list[str]) -> None:
    for ch in node.children:
        if isinstance(ch, str):
            out.append(ch)
        elif ch.tag == "br":
            out.append("\n")
        elif ch.tag in BLOCK:
            out.append("\n")
            _collect(ch, out)
            out.append("\n")
        elif ch.tag in ("script", "style"):
            continue
        else:
            _collect(ch, out)


def normalize(text: str) -> str:
    """压空白但保留段落换行——JD 正文的换行是有信息量的（分条职责/要求）。"""
    text = text.replace("\xa0", " ").replace("\u200b", "")
    lines = [_SPACES.sub(" ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: (v or "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        # 容忍坏标签：向上找同名祖先，找不到就忽略这个闭合标签
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        if data and not data.isspace():
            self.cur.children.append(data)
        elif data:
            self.cur.children.append(" ")


def parse(html: str) -> Node:
    b = _Builder()
    try:
        b.feed(html)
    except Exception:  # noqa: BLE001 —— 真实页面常有残缺标签，能解析多少算多少
        pass
    return b.root
