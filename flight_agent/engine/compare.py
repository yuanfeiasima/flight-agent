"""比价决策引擎:约束过滤 → 去重 → 价格排序 → 推荐。

全是纯函数,便于单元测试;不 import playwright,可离线验证。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Iterable, Optional

from flight_agent.models import Flight, Quote, Recommendation, SearchQuery, SiteResult


def hhmm_to_min(t: str) -> int:
    """'07:30' -> 450。格式异常抛 ValueError。"""
    h, m = (int(x) for x in str(t).split(":"))
    if not (0 <= h <= 24 and 0 <= m <= 59):
        raise ValueError(f"非法时刻: {t}")
    return h * 60 + m


def apply_constraints(
    flights: Iterable[Flight], q: SearchQuery
) -> tuple[list[Flight], list[str]]:
    """按查询约束过滤。返回 (合格航班, 每条被丢弃的说明)。"""
    dep_after = hhmm_to_min(q.dep_after)
    dep_before = hhmm_to_min(getattr(q, "dep_before", "23:59") or "23:59")
    arr_before = hhmm_to_min(q.arr_before)
    kept: list[Flight] = []
    drops: list[str] = []
    for f in flights:
        reasons: list[str] = []
        if f.stops > q.max_stops:
            reasons.append(f"经停{f.stops}次超过上限{q.max_stops}")
        try:
            if f.dep_time:
                dep_min = hhmm_to_min(f.dep_time)
                if dep_min < dep_after:
                    reasons.append(f"出发早于{q.dep_after}")
                elif dep_min > dep_before:
                    reasons.append(f"出发晚于{q.dep_before}")
            if f.arr_time and hhmm_to_min(f.arr_time) > arr_before:
                reasons.append(f"到达晚于{q.arr_before}")
        except ValueError:
            reasons.append("时刻格式异常")
        if q.cabin_keyword and q.cabin_keyword not in (f.cabin or ""):
            reasons.append(f"舱位不符({f.cabin or '未知'})")
        if reasons:
            drops.append(f"{f.flight_no}: " + "；".join(reasons))
        else:
            kept.append(f)
    return kept, drops


def _merge_quotes(existing: Iterable[Quote], incoming: Iterable[Quote]) -> list[Quote]:
    """把两批报价合并成“每渠道一条”,同渠道取最低价,按价格升序。

    同一渠道可能出现多条(重复卡片 / 不同运价),对用户来说只有最低的那条有意义。
    """
    by_site: dict[str, Quote] = {}
    for q in [*existing, *incoming]:
        cur = by_site.get(q.site)
        if cur is None:
            by_site[q.site] = q
            continue
        cheaper = q.price is not None and (cur.price is None or q.price < cur.price)
        if cheaper:
            by_site[q.site] = replace(q, url=q.url or cur.url)
        elif q.url and not cur.url:
            by_site[q.site] = replace(cur, url=q.url)
    return sorted(
        by_site.values(),
        key=lambda x: (x.price is None, x.price or 0.0, x.site),
    )


def _quotes_of(f: Flight) -> list[Quote]:
    """取一条记录携带的报价。

    适配器直接产出的 Flight 通常还没带 quotes(quotes 由 recommend() 按渠道回填),
    这里兜底成“自己的 site+price 一条”,保证单独调用 dedupe_flights 也能工作。
    """
    return list(f.quotes) if f.quotes else [Quote(site=f.site, price=f.price)]


def dedupe_flights(flights: Iterable[Flight]) -> list[Flight]:
    """同一物理航班(同航班号 + 起降时刻)多渠道时取最低价,并保留各渠道报价。

    判据见 Flight.key():机场名各渠道写法不同,不能用来判重。
    同价时保留展示信息更完整的一条,避免同一航班出现两行。
    """
    best: dict[tuple, Flight] = {}
    for f in flights:
        if f.price is None:
            continue
        k = f.key()
        cur = best.get(k)
        if cur is None:
            best[k] = replace(f, quotes=_merge_quotes([], _quotes_of(f)))
            continue
        quotes = _merge_quotes(cur.quotes, _quotes_of(f))
        if f.price < cur.price or (f.price == cur.price and f.is_richer_than(cur)):
            best[k] = replace(f, quotes=quotes)      # 更便宜/信息更全的那条做代表
        else:
            best[k] = replace(cur, quotes=quotes)
    return list(best.values())


def rank(flights: Iterable[Flight]) -> list[Flight]:
    """按 (价格, 出发时刻) 升序;无价航班排最后。"""
    priced = [f for f in flights if f.price is not None]
    no_price = [f for f in flights if f.price is None]
    priced.sort(key=lambda f: (f.price, f.dep_time))
    return priced + no_price


def recommend(
    site_results: Iterable[SiteResult], q: SearchQuery
) -> Recommendation:
    """把一个或多个渠道的结果汇总、过滤、去重、排序,给出最低价推荐。

    这里会给每条记录挂上来源渠道的报价(Quote:渠道 + 价格 + 该渠道结果页 URL),
    于是同一航班被多个渠道抓到时,比价明细就能显示“携程 ¥400 / 去哪儿 ¥388”。
    """
    warnings: list[str] = []
    flights: list[Flight] = []
    for r in site_results:
        for f in r.flights:
            flights.append(
                replace(f, quotes=[Quote(site=r.site or f.site, price=f.price, url=r.url)])
            )
        warnings.extend(r.warnings)

    kept, drops = apply_constraints(flights, q)
    if drops:
        stat = Counter(d.split(": ", 1)[1].split("；")[0] if ": " in d else d
                       for d in drops)
        top = "、".join(f"{k}×{v}" for k, v in stat.most_common(5))
        warnings.append(f"按约束丢弃 {len(drops)} 条: {top}")

    deduped = dedupe_flights(kept)
    ranked = rank(deduped)
    dropped_no_price = sum(1 for f in kept if f.price is None)
    if dropped_no_price:
        warnings.append(f"{dropped_no_price} 条解析出但无价格(登录墙/售罄/字段缺失)")

    best = ranked[0] if ranked and ranked[0].price is not None else None
    if best is None and ranked:
        warnings.append("没有拿到任何价格,无法推荐最低价")
    return Recommendation(best=best, ranked=ranked,
                          dropped_no_price=dropped_no_price, warnings=warnings)
