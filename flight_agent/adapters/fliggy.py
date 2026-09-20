"""飞猪机票适配器(阿里旅行)。

飞猪网址: https://www.fliggy.com
搜索页: https://s.taobao.com/search?...
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from flight_agent.adapters.base import SiteAdapter, clean_price, guess_flight_no, load_until_stable
from flight_agent.models import Flight, SearchQuery, SiteResult

log = logging.getLogger("flight-agent.adapters.fliggy")


class FliggyAdapter(SiteAdapter):
    """飞猪机票抓取适配器。"""

    site = "fliggy"

    def build_url(self, q: SearchQuery) -> str:
        """构造飞猪航班搜索 URL。

        飞猪搜索依赖淘宝搜索页，格式较为复杂。
        这里提供基础实现，实际可能需要根据页面结构调整。
        """
        dep = quote(q.dep_city)
        arr = quote(q.arr_city)
        date = q.dep_date_str.replace("-", "")
        return (
            f"https://www.fliggy.com/flight/search/search.htm"
            f"?depCity={dep}&arrCity={arr}&depDate={date}"
        )

    def search(self, session, q: SearchQuery, settings) -> SiteResult:
        """在飞猪搜索页抓取航班列表。

        注意: 飞猪页面结构可能需要登录才能完整显示，
        并且页面结构可能会变化，需要根据实际情况调整选择器。
        """
        url = self.build_url(q)
        page = session.new_page()
        warnings = []
        flights = []

        try:
            self._focus(page)
            log.info("飞猪: 打开 %s", url)
            page.goto(url, wait_until="domcontentloaded", timeout=settings.page_timeout_ms)
            page.wait_for_timeout(settings.settle_ms)

            # TODO: 根据实际页面结构调整选择器
            # 飞猪页面需要实际调试后确定正确的选择器
            # 这里提供框架，具体实现需要补充

            warnings.append("飞猪适配器尚未完成，需要根据实际页面调试选择器")
            log.warning("飞猪适配器为占位实现，需要补充具体解析逻辑")

        except Exception as exc:
            log.exception("飞猪抓取失败")
            warnings.append(f"飞猪页面加载或解析失败: {exc}")
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass

        return SiteResult(site=self.site, url=url, flights=flights, warnings=warnings)
