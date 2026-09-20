"""比价决策引擎单元测试(纯逻辑,无需浏览器/网络)。"""

from datetime import date

from flight_agent.engine.compare import (
    apply_constraints,
    dedupe_flights,
    hhmm_to_min,
    rank,
    recommend,
)
from flight_agent.models import Flight, SearchQuery, SiteResult

TODAY = date.today()


def _f(flight_no="CA1501", dep="07:00", arr="09:15", stops=0, price=760.0,
       cabin="经济舱", airline="中国国航", dep_airport="北京", arr_airport="上海",
       site="ctrip", stop_info="直飞") -> Flight:
    return Flight(
        airline=airline, flight_no=flight_no,
        dep_airport=dep_airport, arr_airport=arr_airport,
        dep_time=dep, arr_time=arr, stops=stops, stop_info=stop_info,
        cabin=cabin, price=price, site=site,
    )


def _q(**kw) -> SearchQuery:
    base = dict(dep_city="北京", arr_city="上海", dep_code="BJS", arr_code="SHA",
                dep_date=TODAY)
    base.update(kw)
    return SearchQuery(**base)


# --------------------------------------------------------------------------- #
def test_hhmm_to_min():
    assert hhmm_to_min("07:30") == 450
    assert hhmm_to_min("23:59") == 1439


def test_constraints_filters_stops_time_cabin():
    flights = [
        _f("CA1", stops=1, price=300),            # 中转 → 丢
        _f("CA2", dep="05:00", arr="07:00"),      # 早于 06:00 → 丢
        _f("CA3", dep="10:00", arr="23:30"),      # 晚于 22:00 → 丢
        _f("CA4", cabin="公务舱"),                 # 舱位不符 → 丢
        _f("CA5", dep="08:00", arr="10:00", price=500),  # 合格
    ]
    q = _q(max_stops=0, cabin_keyword="经济", dep_after="06:00", arr_before="22:00")
    kept, drops = apply_constraints(flights, q)
    assert [f.flight_no for f in kept] == ["CA5"]
    assert len(drops) == 4


def test_departure_window_upper_bound():
    """出发时间窗是闭区间:18:00~23:00 之外的都要丢掉。"""
    flights = [
        _f("MU1", dep="17:55", arr="20:30", price=400),   # 早于 18:00 → 丢
        _f("MU2", dep="18:00", arr="20:35", price=410),   # 边界,保留
        _f("MU3", dep="21:30", arr="23:59", price=420),   # 窗内,保留
        _f("MU4", dep="23:00", arr="23:59", price=430),   # 边界,保留
        _f("MU5", dep="23:05", arr="23:59", price=440),   # 晚于 23:00 → 丢
    ]
    q = _q(dep_after="18:00", dep_before="23:00")
    kept, drops = apply_constraints(flights, q)
    assert [f.flight_no for f in kept] == ["MU2", "MU3", "MU4"]
    assert len(drops) == 2
    assert any("出发晚于23:00" in d for d in drops)


def test_dedupe_keeps_cheapest():
    same = _f("MU5137", price=560, site="ctrip")
    cheaper = _f("MU5137", price=520, site="airline-official")
    deduped = dedupe_flights([same, cheaper])
    assert len(deduped) == 1
    assert deduped[0].price == 520.0


def test_dedupe_across_sites_with_different_airport_naming():
    """回归:同一航班跨渠道机场写法不同,也必须合并成一条并取最低价。

    真实观测:携程“大兴国际机场/虹桥国际机场T2” vs 去哪儿“北京大兴机场/虹桥机场T2”。
    """
    ctrip = _f("CZ8803", dep="20:00", arr="22:10", price=400, site="ctrip",
               dep_airport="大兴国际机场", arr_airport="虹桥国际机场T2")
    qunar = _f("CZ8803", dep="20:00", arr="22:10", price=388, site="qunar",
               dep_airport="北京大兴机场", arr_airport="虹桥机场T2")
    deduped = dedupe_flights([ctrip, qunar])
    assert len(deduped) == 1
    assert deduped[0].price == 388.0          # 取跨渠道最低价
    assert deduped[0].site == "qunar"


def test_dedupe_same_price_prefers_richer_display():
    """同价时保留机场/航司名更具体的一条,而不是先到先得。"""
    short = _f("CA8341", dep="22:00", arr="23:45", price=400, site="qunar",
               dep_airport="北京大兴机场", arr_airport="浦东机场T2")
    long_ = _f("CA8341", dep="22:00", arr="23:45", price=400, site="ctrip",
               dep_airport="大兴国际机场", arr_airport="浦东国际机场T2")
    deduped = dedupe_flights([short, long_])
    assert len(deduped) == 1
    assert deduped[0].dep_airport == "大兴国际机场"
    assert deduped[0].site == "ctrip"


def test_dedupe_does_not_merge_different_times():
    """同航班号但起降时刻不同 = 不同航班,不能合并。"""
    morning = _f("CA1501", dep="07:00", arr="09:15", price=500)
    evening = _f("CA1501", dep="19:00", arr="21:15", price=700)
    assert len(dedupe_flights([morning, evening])) == 2


def test_norm_airport_folds_international_suffix():
    from flight_agent.models import norm_airport
    assert norm_airport("浦东国际机场 T2") == "浦东机场t2"
    assert norm_airport("浦东机场T2") == "浦东机场t2"


# --------------------------------------------------------------------------- #
# 比价明细:同一航班保留各渠道报价
def test_dedupe_keeps_per_site_quotes():
    ctrip = _f("CZ8803", dep="20:00", arr="22:10", price=400, site="ctrip",
               dep_airport="大兴国际机场", arr_airport="虹桥国际机场T2")
    qunar = _f("CZ8803", dep="20:00", arr="22:10", price=388, site="qunar",
               dep_airport="北京大兴机场", arr_airport="虹桥机场T2")
    (merged,) = dedupe_flights([ctrip, qunar])

    assert merged.price == 388.0                     # 代表价 = 最低价
    assert [q.site for q in merged.priced_quotes] == ["qunar", "ctrip"]   # 按价格升序
    assert [q.price for q in merged.priced_quotes] == [388.0, 400.0]
    assert merged.spread == 12.0
    assert merged.site_count == 2


def test_quotes_keep_only_lowest_price_per_site():
    """同一渠道的多条(重复卡片/不同运价)只保留最低价那条。"""
    a = _f("CA1501", price=560, site="ctrip")
    b = _f("CA1501", price=520, site="ctrip")
    (merged,) = dedupe_flights([a, b])
    assert merged.price == 520.0
    assert len(merged.priced_quotes) == 1
    assert merged.priced_quotes[0].price == 520.0


def test_single_site_has_no_spread():
    (merged,) = dedupe_flights([_f("MU5137", price=500, site="ctrip")])
    assert merged.site_count == 1
    assert merged.spread == 0.0
    assert merged.priced_quotes[0].site == "ctrip"


def test_recommend_attaches_quotes_with_site_url():
    """recommend 要给每条记录挂上渠道报价和该渠道结果页 URL。"""
    ctrip = SiteResult(site="ctrip", url="https://flights.ctrip.com/list/bjs-sha",
                       flights=[_f("CZ8803", dep="20:00", arr="22:10", price=400, site="ctrip")])
    qunar = SiteResult(site="qunar", url="https://flight.qunar.com/list/bjs-sha",
                       flights=[_f("CZ8803", dep="20:00", arr="22:10", price=388, site="qunar")])
    rec = recommend([ctrip, qunar], _q())
    assert len(rec.ranked) == 1
    best = rec.ranked[0]
    assert best.price == 388.0
    urls = {q.site: q.url for q in best.priced_quotes}
    assert urls["qunar"].endswith("/list/bjs-sha") and "qunar.com" in urls["qunar"]
    assert "ctrip.com" in urls["ctrip"]
    # 序列化后明细要能落到 JSON(界面/存档都依赖它)
    assert len(best.to_dict()["quotes"]) == 2


def test_rank_orders_by_price_then_dep_time():
    flights = [
        _f("A1", dep="12:00", price=600),
        _f("A2", dep="08:00", price=600),
        _f("A3", dep="09:00", price=400),
        _f("A4", dep="10:00", price=None),   # 无价排最后
    ]
    ordered = rank(flights)
    assert [f.flight_no for f in ordered] == ["A3", "A2", "A1", "A4"]


def test_recommend_picks_lowest_and_counts_drops():
    flights = [
        _f("CA1", dep="07:00", price=760),
        _f("MU2", dep="09:30", price=510),
        _f("9C3", dep="06:20", price=399),
        _f("ZH4", stops=1, price=330),       # 中转,约束丢弃
        _f("HU5", dep="08:00", price=None),  # 无价
    ]
    q = _q(max_stops=0)
    rec = recommend([SiteResult(site="ctrip", url="u", flights=flights)], q)
    assert rec.best is not None
    assert rec.best.flight_no == "9C3"
    assert rec.best.price == 399.0
    assert [f.flight_no for f in rec.ranked] == ["9C3", "MU2", "CA1"]
    assert rec.dropped_no_price == 1
    assert any("丢弃" in w for w in rec.warnings)


def test_recommend_empty():
    rec = recommend([SiteResult(site="ctrip", url="u", flights=[])], _q())
    assert rec.best is None
    assert rec.ranked == []
