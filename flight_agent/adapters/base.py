"""站点适配器抽象与公共解析工具。"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Optional

from flight_agent.models import SearchQuery, SiteResult

_PRICE_RE = re.compile(r"¥\s*([0-9][0-9,]*)")
_FLIGHT_NO_RE = re.compile(r"((?:[A-Z]{2}|[A-Z]\d|\d[A-Z])\d{3,4})")


def clean_price(text: str) -> Optional[float]:
    """从文本中取出首个 ¥ 金额,如 '¥560起' -> 560.0。"""
    m = _PRICE_RE.search(text or "")
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def guess_flight_no(text: str) -> Optional[str]:
    """在文本中找航班号，如 CA1831、3U8888 或 9C8921。"""
    m = _FLIGHT_NO_RE.search(text or "")
    return m.group(1) if m else None


def load_until_stable(
    page,
    count_fn: Callable[[], int],
    step_fn: Callable[[], None],
    *,
    settle_ms: int,
    timeout_ms: int,
    stable_rounds: int = 3,
) -> int:
    """反复“触发加载 → 观察卡片数”,直到**连续若干轮**不再增长或超时;返回最终数量。

    为什么不能只看一轮:懒加载列表在两次批量渲染之间存在平台期 —— 数量会连续几秒
    不变,然后突然又长出一批。原来“一轮不涨就 break”的写法在页面冷启动时会过早收手,
    实测携程只抓到 13 条(完整列表 185 条)。要求“连续 stable_rounds 轮不涨”才算加载完,
    同时用 timeout_ms 兜底,兼顾完整性与耗时。
    """
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
    prev, stable = -1, 0
    while True:
        try:
            step_fn()
        except Exception:  # noqa: BLE001 滚动失败不该中断抓取
            pass
        page.wait_for_timeout(settle_ms)
        try:
            n = count_fn()
        except Exception:  # noqa: BLE001
            n = 0
        if n > prev:
            prev, stable = n, 0        # 又长出新卡片:重新计数
        else:
            stable += 1
            if stable >= stable_rounds:
                break                  # 连续多轮不变,认为加载完了
        if time.monotonic() >= deadline:
            break
    return max(prev, 0)


class SiteAdapter(ABC):
    """每个机票网站实现一个子类。新增渠道:继承本类 + 在 __init__.py 注册。"""

    site: str = ""

    @staticmethod
    def _focus(page) -> None:
        """把标签页切到前台再抓取。

        为什么必须这么做:Chrome 会**限制后台标签页的定时器与渲染**,而航班列表靠
        滚动懒加载触发。用 CDP 新开的页面默认不是活动标签,于是滚动常常只加载出首屏
        十几条就停住 —— 表现为同一查询时而 13 条、时而 185 条。bring_to_front() 让
        页面成为活动标签,懒加载才会正常推进。
        """
        try:
            page.bring_to_front()
        except Exception:  # noqa: BLE001 个别环境不支持也不该中断抓取
            pass

    @abstractmethod
    def build_url(self, q: SearchQuery) -> str:
        """构造直达搜索结果页 URL。"""

    @abstractmethod
    def search(self, session, q: SearchQuery, settings) -> SiteResult:
        """驱动页面搜索并抽取航班列表,产出统一 SiteResult。"""
