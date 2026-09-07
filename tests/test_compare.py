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


def test_dedupe_keeps_cheapest():
    same = _f("MU5137", price=560, site="ctrip")
    cheaper = _f("MU5137", price=520, site="airline-official")
    deduped = dedupe_flights([same, cheaper])
    assert len(deduped) == 1
    assert deduped[0].price == 520.0


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
