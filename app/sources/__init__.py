"""数据源注册表。

现在只有 BOSS，但抓取/解析/入库三段是分开的，加实习僧、拉勾只需要
再写一个 parse_xxx + 一个 fetch_xxx，入库链路不动。
"""

from __future__ import annotations

from .base import FetchError, FetchSpec, NeedHumanError, RawJob, SetupError, parse_salary

__all__ = [
    "FetchError", "FetchSpec", "NeedHumanError", "RawJob", "SetupError", "parse_salary",
]
