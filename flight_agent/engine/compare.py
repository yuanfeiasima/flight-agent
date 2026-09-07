"""比价决策引擎:约束过滤 → 去重 → 价格排序 → 推荐。

全是纯函数,便于单元测试;不 import playwright,可离线验证。
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Optional

from flight_agent.models import Flight, Recommendation, SearchQuery, SiteResult


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
    arr_before = hhmm_to_min(q.arr_before)
    kept: list[Flight] = []
    drops: list[str] = []
    for f in flights:
        reasons: list[str] = []
        if f.stops > q.max_stops:
            reasons.append(f"经停{f.stops}次超过上限{q.max_stops}")
        try:
            if f.dep_time and hhmm_to_min(f.dep_time) < dep_after:
                reasons.append(f"出发早于{q.dep_after}")
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


def dedupe_flights(flights: Iterable[Flight]) -> list[Flight]:
    """同一物理航班(同航班号/起降地/经停)多渠道时取最低价。"""
    best: dict[tuple, Flight] = {}
    for f in flights:
        if f.price is None:
            continue
        k = f.key()
        cur = best.get(k)
        if cur is None or (f.price, f.dep_time) < (cur.price, cur.dep_time):
            best[k] = f
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
    """把一个或多个渠道的结果汇总、过滤、去重、排序,给出最低价推荐。"""
    warnings: list[str] = []
    flights: list[Flight] = []
    for r in site_results:
        flights.extend(r.flights)
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
