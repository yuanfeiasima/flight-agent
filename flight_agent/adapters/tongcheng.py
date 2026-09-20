"""同程旅行机票适配器。

同程网址: https://www.ly.com
机票页: https://www.ly.com/flights/home
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from flight_agent.adapters.base import SiteAdapter, clean_price, guess_flight_no, load_until_stable
from flight_agent.models import Flight, SearchQuery, SiteResult

log = logging.getLogger("flight-agent.adapters.tongcheng")


class TongchengAdapter(SiteAdapter):
    """同程旅行机票抓取适配器。"""

    site = "tongcheng"

    def build_url(self, q: SearchQuery) -> str:
        """构造同程航班搜索 URL。

        同程使用三字码和城市名称（URL编码）进行搜索。
        格式：/flights/itinerary/oneway/DEP-ARR?date=YYYY-MM-DD&from=出发城市&to=到达城市
        """
        dep = q.dep_code
        arr = q.arr_code
        date = q.dep_date_str
        from_city = quote(q.dep_city)
        to_city = quote(q.arr_city)

        return (
            f"https://www.ly.com/flights/itinerary/oneway/{dep}-{arr}"
            f"?date={date}&from={from_city}&to={to_city}"
            f"&fromairport=&toairport=&p=&childticket=0,0"
        )

    def search(self, session, q: SearchQuery, settings) -> SiteResult:
        """在同程搜索页抓取航班列表。

        注意: 同程页面结构可能需要登录才能完整显示价格。
        页面结构可能会变化，需要根据实际情况调整选择器。
        """
        url = self.build_url(q)
        page = session.new_page()
        warnings = []
        flights = []

        try:
            self._focus(page)
            log.info("同程: 打开 %s", url)
            page.goto(url, wait_until="domcontentloaded", timeout=settings.page_timeout_ms)
            page.wait_for_timeout(settings.settle_ms)

            # TODO: 根据实际页面结构调整选择器
            # 同程页面需要实际调试后确定正确的选择器
            # 这里提供框架，具体实现需要补充

            warnings.append("同程适配器尚未完成，需要根据实际页面调试选择器")
            log.warning("同程适配器为占位实现，需要补充具体解析逻辑")

        except Exception as exc:
            log.exception("同程抓取失败")
            warnings.append(f"同程页面加载或解析失败: {exc}")
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass

        return SiteResult(site=self.site, url=url, flights=flights, warnings=warnings)
