"""命令行入口:一次“查询 → 抓取 → 比价 → 推荐”的 agent 主流程。

示例:
  uv run python -m flight_agent.cli --dep 北京 --arr 上海 --date 2026-09-05
  uv run python -m flight_agent.cli --self-test   # 离线验证引擎(不连浏览器)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from flight_agent import __version__
from flight_agent.adapters import ADAPTERS
from flight_agent.browser import BrowserSession
from flight_agent.city_codes import resolve_code
from flight_agent.config import Settings
from flight_agent.engine.compare import recommend
from flight_agent.models import Flight, SearchQuery, SiteResult

log = logging.getLogger("flight-agent")


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flight-agent",
        description="机票自动比价·最低价筛选 agent(MVP:携程单渠道/单日查询)",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--dep", help="出发城市,如 北京(或三位 IATA 码)")
    p.add_argument("--arr", help="到达城市,如 上海")
    p.add_argument("--date", help="出发日期 YYYY-MM-DD(默认 7 天后)")
    p.add_argument("--max-stops", type=int, default=0, help="最大经停次数,默认 0=直飞")
    p.add_argument("--cabin", default="经济", help="舱位关键字:经济/公务/头等")
    p.add_argument("--dep-after", default="00:00", help="最早出发时刻 HH:MM")
    p.add_argument("--arr-before", default="23:59", help="最晚到达时刻 HH:MM")
    p.add_argument("--top", type=int, default=10, help="终端展示前 N 条")
    p.add_argument("--cdp-port", type=int, default=9222, help="Chrome 调试端口")
    p.add_argument("--out", default="artifacts/latest_query.json", help="结构化结果输出路径")
    p.add_argument("--no-dump", action="store_true", help="抓取失败时不转储 HTML")
    p.add_argument("--self-test", action="store_true", help="不连浏览器,离线跑一遍引擎示例")
    return p


# --------------------------------------------------------------------------- #
def make_query(args) -> SearchQuery:
    dep_day = args.date or (date.today() + timedelta(days=7)).isoformat()
    try:
        dep_date = date.fromisoformat(dep_day)
    except ValueError:
        raise SystemExit(f"日期格式错误: {dep_day}(应为 YYYY-MM-DD)")
    if dep_date < date.today():
        raise SystemExit("出发日期不能早于今天")
    dep_code = resolve_code(args.dep)
    arr_code = resolve_code(args.arr)
    return SearchQuery(
        dep_city=args.dep.strip(),
        arr_city=args.arr.strip(),
        dep_code=dep_code,
        arr_code=arr_code,
        dep_date=dep_date,
        cabin_keyword=args.cabin,
        max_stops=args.max_stops,
        dep_after=args.dep_after,
        arr_before=args.arr_before,
    )


def make_settings(args) -> Settings:
    return Settings(
        cdp_port=args.cdp_port,
        top_n=args.top,
        dump_on_failure=not args.no_dump,
    )


# --------------------------------------------------------------------------- #
def _fmt_row(i: int, f: Flight) -> str:
    seg = "直飞" if f.stops == 0 else (f.stop_info or f"经停{f.stops}")
    day = " +1天" if f.arr_day_offset else ""
    return (
        f"{i:>2}. {f.flight_no:<8} {f.airline:<8} "
        f"{f.dep_airport}→{f.arr_airport:<10} "
        f"{f.dep_time or '--:--'}→{f.arr_time or '--:--'}{day}  "
        f"{seg:<6} {f.cabin:<6} "
        f"{('¥%d' % f.price) if f.price is not None else '无价':>10}"
    )


def print_report(q: SearchQuery, rec, settings: Settings) -> None:
    print("\n" + "=" * 78)
    print(f"查询: {q.dep_city} → {q.arr_city}  {q.dep_date_str}  "
          f"(直飞≤{q.max_stops}次 | {q.cabin_keyword}舱 | "
          f"{q.dep_after}~{q.arr_before})")
    print("=" * 78)
    for w in rec.warnings:
        print(f"[!] {w}")
    if not rec.ranked:
        print("未获得任何航班。")
    else:
        priced = [f for f in rec.ranked if f.price is not None]
        print(f"满足约束的航班共 {len(rec.ranked)} 条"
              + (f"(其中 {rec.dropped_no_price} 条无价)" if rec.dropped_no_price else ""))
        shown = rec.ranked[: settings.top_n]
        print("\n按价格升序:")
        for i, f in enumerate(shown, 1):
            print(_fmt_row(i, f))
        if rec.best:
            print("\n" + "-" * 78)
            print("★ 最低价推荐:")
            print("  " + _fmt_row(1, rec.best).split(". ", 1)[1])
            print("  仅供比价参考 —— 下单/支付请回到人工确认。")
    print("-" * 78)


# --------------------------------------------------------------------------- #
def _self_test() -> int:
    """离线自检:不连浏览器,构造样例跑一遍引擎并断言结果。"""
    from flight_agent.models import Flight, SiteResult

    today = date.today()
    sample = [
        Flight(airline="中国国航", flight_no="CA1501", dep_airport="北京", arr_airport="上海",
               dep_time="07:00", arr_time="09:15", stops=0, stop_info="直飞",
               cabin="经济舱", price=760.0, site="ctrip"),
        Flight(airline="东方航空", flight_no="MU5137", dep_airport="北京", arr_airport="上海",
               dep_time="09:30", arr_time="11:45", stops=0, stop_info="直飞",
               cabin="经济舱", price=510.0, site="ctrip"),
        Flight(airline="春秋航空", flight_no="9C8888", dep_airport="北京", arr_airport="上海",
               dep_time="06:20", arr_time="08:35", stops=0, stop_info="直飞",
               cabin="经济舱", price=399.0, site="ctrip"),
        # 中转,应被 max_stops=0 过滤
        Flight(airline="深圳航空", flight_no="ZH1234", dep_airport="北京", arr_airport="上海",
               dep_time="08:00", arr_time="13:00", stops=1, stop_info="经停西安",
               cabin="经济舱", price=330.0, site="ctrip"),
    ]
    q = SearchQuery(dep_city="北京", arr_city="上海", dep_code="BJS", arr_code="SHA",
                    dep_date=today, max_stops=0, cabin_keyword="经济",
                    dep_after="00:00", arr_before="23:59")
    res = SiteResult(site="ctrip", url="self-test", flights=sample)
    rec = recommend([res], q)
    assert rec.best is not None and rec.best.flight_no == "9C8888", rec.best
    assert len(rec.ranked) == 3, [f.flight_no for f in rec.ranked]
    assert rec.best.price == 399.0
    print_report(q, rec, Settings(top_n=10))
    print("\n引擎自检通过 ✓")
    return 0


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.dep or not args.arr:
        raise SystemExit("需要 --dep 与 --arr(城市名或三位 IATA 码);--self-test 可离线验证。")

    q = make_query(args)
    settings = make_settings(args)

    # —— 渠道列表(当前只有携程,注册表见 adapters/__init__.py)——
    site_results: list[SiteResult] = []
    try:
        with BrowserSession.connect(settings.cdp_host, settings.cdp_port) as session:
            print(f"已附加 Chrome: 当前页面 [{session.check()}]")
            print(f"开始抓取 {len(settings.sites)} 个渠道 ...")
            for site in settings.sites:
                adapter_cls = ADAPTERS[site]
                result = adapter_cls().search(session, q, settings)
                site_results.append(result)
                print(f"[{site}] 抓到航班 {len(result.flights)} 条"
                      f"(成功解析价格 {result.ok_count} 条)")
                for w in result.warnings:
                    print(f"  [!] {w}")
    except ConnectionError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    rec = recommend(site_results, q)
    print_report(q, rec, settings)

    # —— 结构化结果落盘 ——
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "query": {
            "dep_city": q.dep_city, "arr_city": q.arr_city,
            "dep_code": q.dep_code, "arr_code": q.arr_code,
            "dep_date": q.dep_date_str, "max_stops": q.max_stops,
            "cabin_keyword": q.cabin_keyword,
            "dep_after": q.dep_after, "arr_before": q.arr_before,
        },
        "sites": [r.to_dict() for r in site_results],
        "recommendation": rec.to_dict(),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结构化结果已写入: {out}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
