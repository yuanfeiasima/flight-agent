"""查询参数的统一校验与序列化，供 CLI、Web 和大模型入口共用。"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Mapping

from flight_agent.city_codes import resolve_code
from flight_agent.models import SearchQuery

_HHMM_RE = re.compile(r"^\d{1,2}:\d{2}$")
_CABINS = {"经济": "经济", "经济舱": "经济", "公务": "公务", "公务舱": "公务",
           "商务": "公务", "商务舱": "公务", "头等": "头等", "头等舱": "头等"}


def _minutes(hhmm: str) -> int:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return hour * 60 + minute


def _time(value: Any, fallback: str, field_name: str) -> str:
    raw = str(value or fallback).strip()
    if not _HHMM_RE.fullmatch(raw):
        raise ValueError(f"时刻 {field_name}={raw!r} 应为 HH:MM 格式。")
    hour, minute = (int(x) for x in raw.split(":"))
    if hour > 23 or minute > 59:
        raise ValueError(f"时刻 {field_name}={raw!r} 不合法。")
    return f"{hour:02d}:{minute:02d}"


def make_search_query(data: Mapping[str, Any], *, today: date | None = None) -> SearchQuery:
    """从字典构造严格、归一化后的查询。"""
    now = today or date.today()
    dep = str(data.get("dep") or data.get("dep_city") or "").strip()
    arr = str(data.get("arr") or data.get("arr_city") or "").strip()
    if not dep or not arr:
        raise ValueError("请填写出发城市与到达城市（中文或三位 IATA 码）。")
    dep_code, arr_code = resolve_code(dep), resolve_code(arr)
    if dep_code == arr_code:
        raise ValueError("出发与到达城市不能相同。")

    day_text = str(data.get("date") or data.get("dep_date") or (now + timedelta(days=7))).strip()
    try:
        dep_date = date.fromisoformat(day_text)
    except ValueError as exc:
        raise ValueError(f"日期格式应为 YYYY-MM-DD，收到: {day_text!r}") from exc
    if dep_date < now:
        raise ValueError("出发日期不能早于今天。")

    raw_stops = data.get("max_stops", 0)
    try:
        max_stops = int(0 if raw_stops in (None, "") else raw_stops)
    except (TypeError, ValueError) as exc:
        raise ValueError("经停上限必须是整数。") from exc
    if not 0 <= max_stops <= 2:
        raise ValueError("经停上限必须在 0 到 2 之间。")

    raw_cabin = str(data.get("cabin") or data.get("cabin_keyword") or "经济").strip()
    cabin = _CABINS.get(raw_cabin)
    if cabin is None:
        raise ValueError("舱位仅支持经济、公务或头等。")

    dep_after = _time(data.get("dep_after"), "00:00", "dep_after")
    dep_before = _time(data.get("dep_before"), "23:59", "dep_before")
    if _minutes(dep_before) < _minutes(dep_after):
        raise ValueError(
            f"出发时间窗不合法:最晚出发 {dep_before} 早于最早出发 {dep_after}。"
        )

    return SearchQuery(
        dep_city=dep,
        arr_city=arr,
        dep_code=dep_code,
        arr_code=arr_code,
        dep_date=dep_date,
        cabin_keyword=cabin,
        max_stops=max_stops,
        dep_after=dep_after,
        dep_before=dep_before,
        arr_before=_time(data.get("arr_before"), "23:59", "arr_before"),
    )


def query_to_dict(q: SearchQuery) -> dict[str, Any]:
    return {
        "dep": q.dep_city,
        "arr": q.arr_city,
        "date": q.dep_date_str,
        "cabin": q.cabin_keyword,
        "max_stops": q.max_stops,
        "dep_after": q.dep_after,
        "dep_before": q.dep_before,
        "arr_before": q.arr_before,
    }
