"""站点适配器抽象与公共解析工具。"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Optional

from flight_agent.models import SearchQuery, SiteResult

_PRICE_RE = re.compile(r"¥\s*([0-9][0-9,]*)")
_FLIGHT_NO_RE = re.compile(r"([A-Z]{2}\d{3,4})")


def clean_price(text: str) -> Optional[float]:
    """从文本中取出首个 ¥ 金额,如 '¥560起' -> 560.0。"""
    m = _PRICE_RE.search(text or "")
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def guess_flight_no(text: str) -> Optional[str]:
    """在文本中找航班号(两位字母 + 3-4 位数字,如 CA1831)。"""
    m = _FLIGHT_NO_RE.search(text or "")
    return m.group(1) if m else None


class SiteAdapter(ABC):
    """每个机票网站实现一个子类。新增渠道:继承本类 + 在 __init__.py 注册。"""

    site: str = ""

    @abstractmethod
    def build_url(self, q: SearchQuery) -> str:
        """构造直达搜索结果页 URL。"""

    @abstractmethod
    def search(self, session, q: SearchQuery, settings) -> SiteResult:
        """驱动页面搜索并抽取航班列表,产出统一 SiteResult。"""
