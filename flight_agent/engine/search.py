"""多渠道抓取编排。"""

from __future__ import annotations

import logging
from collections.abc import Callable

from flight_agent.adapters import ADAPTERS
from flight_agent.engine.compare import apply_constraints
from flight_agent.models import SearchQuery, SiteResult

log = logging.getLogger("flight-agent.search")


def search_sites(
    session,
    query: SearchQuery,
    settings,
    *,
    on_result: Callable[[SiteResult], None] | None = None,
) -> list[SiteResult]:
    """按配置查询所有渠道，汇总后返回。

    注意：Playwright session 不能在多线程间共享，所以即使 stop_after_first_success=False，
    也是顺序查询，但会查询所有配置的网站。
    """
    results: list[SiteResult] = []

    log.info(f"顺序查询 {len(settings.sites)} 个网站: {', '.join(settings.sites)}")

    for site in settings.sites:
        adapter_cls = ADAPTERS.get(site)
        if adapter_cls is None:
            log.warning(f"未知渠道 {site!r}；可用渠道: {', '.join(ADAPTERS)}")
            results.append(SiteResult(site=site, url="", flights=[],
                                    warnings=[f"未知渠道: {site}"]))
            continue

        try:
            log.info(f"[{site}] 开始查询...")
            result = adapter_cls().search(session, query, settings)
            log.info(f"[{site}] 完成，获得 {len(result.flights)} 条航班")
            results.append(result)
            if on_result:
                on_result(result)
        except Exception as exc:
            log.exception(f"[{site}] 查询失败")
            results.append(SiteResult(site=site, url="", flights=[],
                                    warnings=[f"查询失败: {exc}"]))
            if on_result:
                on_result(results[-1])

        # 如果开启了"首个成功即停"，检查是否可以提前终止
        if settings.stop_after_first_success:
            eligible, _ = apply_constraints(result.flights, query)
            if any(f.price is not None for f in eligible):
                log.info(f"[{site}] 获得有效价格，提前终止（stop_after_first_success=True）")
                break

    log.info(f"查询完成，共获得 {sum(len(r.flights) for r in results)} 条航班")
    return results
