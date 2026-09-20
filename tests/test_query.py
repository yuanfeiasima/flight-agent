from datetime import date

import pytest

from flight_agent.query import make_search_query, query_to_dict


def test_query_defaults_and_normalization():
    q = make_search_query(
        {"dep": "北京", "arr": "上海", "date": "2026-09-20", "cabin": "商务舱"},
        today=date(2026, 9, 9),
    )
    assert (q.dep_code, q.arr_code) == ("BJS", "SHA")
    assert q.cabin_keyword == "公务"
    assert q.max_stops == 0
    assert (q.dep_after, q.arr_before) == ("00:00", "23:59")
    assert query_to_dict(q)["date"] == "2026-09-20"


@pytest.mark.parametrize(
    "data, message",
    [
        ({"dep": "北京", "arr": "北京"}, "不能相同"),
        ({"dep": "北京", "arr": "上海", "date": "2020-01-01"}, "不能早于"),
        ({"dep": "北京", "arr": "上海", "max_stops": 3}, "0 到 2"),
        ({"dep": "北京", "arr": "上海", "dep_after": "25:00"}, "不合法"),
    ],
)
def test_query_rejects_invalid_values(data, message):
    with pytest.raises(ValueError, match=message):
        make_search_query(data, today=date(2026, 9, 9))
