"""携程卡片文本解析 + 离线 fixture 结构回归(不连浏览器 / CDP / 不解析图片)。

覆盖:
1. flight_from_card_text —— 纯文本 → Flight 的字段抽取与判定(空占位/广告/中转/跨天);
2. 由 tools/make_fixture.py 从真实页面提取的 tests/fixtures/ 子树结构假设
   (携程改版后,用 tools/probe.py 抓新页面 → make_fixture.py 重新生成即可更新断言依据)。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from flight_agent.adapters.ctrip import _price_from_text, flight_from_card_text
from flight_agent.models import SearchQuery

FIXTURE = Path(__file__).parent / "fixtures" / "ctrip_flight_list_fragment.html"


def _q(**kw) -> SearchQuery:
    base = dict(dep_city="北京", arr_city="上海", dep_code="BJS", arr_code="SHA",
                dep_date=date.today())
    base.update(kw)
    return SearchQuery(**base)


# —— 来自真实抓取(artifacts/latest_query.json 的 raw 字段,inner_text 原文) ——
REAL_CA8341 = (
    "中国国航\nCA8341 空客321(中)\n当日低价\n22:00\n大兴国际机场\n"
    "23:45\n浦东国际机场T2\n已减¥15\n黄金贵宾可享\n¥355起\n经济舱1.7折\n订票"
)
REAL_MU5231 = (
    "东方航空\nMU5231 空客321(中)\n22:00\n大兴国际机场\n00:05\n+1天\n"
    "浦东国际机场T1\n赠接送机85折优惠券宠物友好\n¥370起\n经济舱1.8折\n订票"
)


# --------------------------------------------------------------------------- #
def test_real_card_ca8341():
    f = flight_from_card_text(REAL_CA8341, _q())
    assert f is not None
    assert f.flight_no == "CA8341"
    assert f.airline == "中国国航"
    assert (f.dep_time, f.arr_time) == ("22:00", "23:45")
    assert f.dep_airport == "大兴国际机场"
    assert f.arr_airport == "浦东国际机场T2"
    assert f.stops == 0
    assert f.arr_day_offset == 0
    assert f.price == 355.0
    assert "经济舱" in f.cabin


def test_real_card_mu5231_overnight():
    f = flight_from_card_text(REAL_MU5231, _q())
    assert f is not None and f.flight_no == "MU5231"
    assert f.airline == "东方航空"
    assert f.price == 370.0
    assert f.arr_day_offset == 1          # +1天 → 次日到达


def test_transfer_card_parses_stops():
    text = ("深圳航空\nZH1234 空客320\n08:00\n北京首都机场\n13:00\n上海虹桥机场\n"
            "中转1次\n¥880\n经济舱\n订票")
    f = flight_from_card_text(text, _q(max_stops=1))
    assert f is not None
    assert f.stops == 1
    assert "中转" in f.stop_info


def test_empty_placeholder_is_none():
    """首屏常出现无任何文本的空占位节点,应判定为 None 而非报错。"""
    assert flight_from_card_text("", _q()) is None
    assert flight_from_card_text("   \n  \n ", _q()) is None


def test_non_flight_text_is_none():
    """头部下拉/广告等有文字但无航班号的内容,应静默跳过。"""
    assert flight_from_card_text("石家庄\n¥299起\n特惠出行", _q()) is None


def test_price_hint_wins_over_text():
    """适配器从价格节点拿到的金额应优先于文本兜底。"""
    f = flight_from_card_text(REAL_CA8341, _q(), price_hint=888.0)
    assert f.price == 888.0


def test_no_price_keeps_flight_with_none_price():
    text = "中国国航\nCA1501\n08:00\n首都机场\n10:00\n虹桥机场\n售罄\n经济舱"
    f = flight_from_card_text(text, _q())
    assert f is not None
    assert f.price is None


def test_default_cabin_and_guessed_stops():
    text = "海南航空\nHU7605\n14:00\n首都机场\n16:30\n虹桥机场\n¥600起\n订票"
    f = flight_from_card_text(text, _q())
    assert f is not None
    assert f.cabin == "经济舱(默认)"
    assert f.stops == 0                    # 无直飞/经停字样 → 按直飞推测


def test_price_from_text_filters_small_labels():
    assert _price_from_text("已减¥15\n¥355起\n保险¥20") == 355.0
    assert _price_from_text("无金额") is None


# --------------------------------------------------------------------------- #
# 离线 fixture 结构回归(由 tools/make_fixture.py 从真实页面子树生成)
def test_fixture_matches_observed_ctrip_dom():
    sub = FIXTURE.read_text(encoding="utf-8")
    assert 'class="flight-list root-flights"' in sub
    # 滚动后的真实快照:13 个 flight-item 槽位,其中 10 个渲染了 .plane-No
    assert sub.count('class="flight-item domestic"') == 13
    assert sub.count('<span class="plane-No">') == 10
    # 样例航班号(plane-No 节点内以 ">CA8341&nbsp;" 形式出现,id 兜底形如 comfort-…)
    for no in ("CZ8888", "MU5231", "MU5138", "SC4642"):
        assert f">{no}&nbsp;" in sub or f'id="comfort-{no}' in sub
    # 覆盖关键结构:跨天(+1天)、中转组合、航司 logo 与价格节点
    assert "+1天" in sub
    assert "中转" in sub
    assert "airline-logo" in sub
    assert "flight-price" in sub
