from datetime import date
from pathlib import Path

from flight_agent.adapters.qunar import (
    QunarAdapter,
    flight_from_card_text,
    no_result_reason,
    price_from_label,
    price_from_title,
)
from flight_agent.models import SearchQuery

FIXTURE = Path(__file__).parent / "fixtures" / "qunar_flight_cards.html"

# —— 真实卡片的 inner_text 原文(滚动数字轮盘 odometer) ——
# 注意 "¥ 374 0 4 0" 里没有一个是真实价格:真实价格是 ¥400(见 aria-label/title)。
REAL_ODOMETER_CZ8803 = (
    "南方航空\nCZ8803空客330(大)\n20:00\n\n北京大兴机场\n\n2h10m\n22:10\n\n"
    "虹桥机场T2\n\n¥\n374\n0\n4\n0\n\n2.5折"
)


def _q():
    return SearchQuery(
        dep_city="北京", arr_city="上海", dep_code="BJS", arr_code="SHA",
        dep_date=date(2026, 9, 20),
    )


def test_qunar_card_text_parser_uses_visible_text_only():
    text = (
        "四川航空\n3U8899\n07:25\n首都国际机场T2\n10:05\n虹桥国际机场T2\n"
        "直飞\n¥680起\n经济舱"
    )
    f = flight_from_card_text(text, _q())
    assert f is not None
    assert f.flight_no == "3U8899"
    assert f.airline == "四川航空"
    assert (f.dep_time, f.arr_time) == ("07:25", "10:05")
    assert (f.dep_airport, f.arr_airport) == ("首都国际机场T2", "虹桥国际机场T2")
    assert f.price == 680
    assert f.site == "qunar"


def test_qunar_ignores_non_flight_card_and_builds_url():
    assert flight_from_card_text("低价推荐 ¥300", _q()) is None
    url = QunarAdapter().build_url(_q())
    assert "flight.qunar.com/site/oneway_list.htm" in url
    assert "fromCode=BJS" in url and "toCode=SHA" in url
    assert "searchDepartureTime=2026-09-20" in url


def test_ctrip_style_flight_number_regex_handles_digit_prefix():
    assert flight_from_card_text("春秋航空\n9C8921\n08:00\n北京\n10:00\n上海\n¥500", _q()).flight_no == "9C8921"


# --------------------------------------------------------------------------- #
# 价格必须来自属性,而不是滚动数字轮盘的文本(否则会随机给出错误低价)
def test_price_from_aria_label():
    assert price_from_label("报价：400元。点击enter查看详细信息，tab阅读下一条航线") == 400.0
    assert price_from_label("报价:1,280元") == 1280.0
    assert price_from_label("没有报价字样") is None
    assert price_from_label("") is None


def test_price_from_title_attribute():
    assert price_from_title("400") == 400.0
    assert price_from_title(" ¥400 ") == 400.0
    assert price_from_title("空客321(中)") is None      # 机型等非金额 title
    assert price_from_title("") is None


def test_price_out_of_range_is_rejected():
    """离谱金额(0/1e9)按“无价”处理,避免污染最低价推荐。"""
    assert price_from_title("12") is None
    assert price_from_title("9999999") is None


def test_odometer_text_never_becomes_price():
    """回归:轮盘文本里的随机数字绝不能当价格。适配器必须传 allow_text_price=False。"""
    f = flight_from_card_text(REAL_ODOMETER_CZ8803, _q(), allow_text_price=False)
    assert f is not None and f.flight_no == "CZ8803"
    assert f.price is None                     # 宁可无价,也不要 374/85 这种假价

    hinted = flight_from_card_text(REAL_ODOMETER_CZ8803, _q(), price_hint=400.0,
                                   allow_text_price=False)
    assert hinted is not None and hinted.price == 400.0


def test_odometer_text_still_used_when_explicitly_allowed():
    """纯文本入口(无浏览器)保留旧行为,便于干净的离线文本。"""
    f = flight_from_card_text(REAL_ODOMETER_CZ8803, _q())
    assert f is not None and f.price == 374.0


# --------------------------------------------------------------------------- #
# 离线 fixture 结构回归(tests/fixtures/qunar_flight_cards.html,真实卡片子树)
# —— 真实抓到的“未登录”页面正文片段(interface 提示 + 无结果提示同时存在) ——
REAL_LOGGED_OUT_BODY = (
    "适老化及无障碍 | 请登录 | 或免费注册 | 消息 | 查看订单 | 积分商城 | 联系客服 | "
    "首页 | 机票 | 酒店 | 单程往返 | 09-25周五 | ¥320 | 价格日历 | 起飞时间价格排序 | "
    "暂无符合条件的机票信息，请重新搜索 | 关于Qunar.com"
)
REAL_LOGGED_IN_NO_FLIGHT_BODY = (
    "去哪儿用户 | 退出 | 我的机票 | 消息5 | 查看订单 | 首页 | 机票 | "
    "09-25周五 | ¥388 | 价格日历 | 起飞时间价格排序"
)


def test_login_wall_is_distinguished_from_no_flights():
    """回归:去哪儿未登录也返回“暂无符合条件”,必须提示去登录而不是“没票”。"""
    reason = no_result_reason(REAL_LOGGED_OUT_BODY)
    assert "登录" in reason and "暂无" not in reason

    # 已登录但没有结果时,不应误导用户去登录
    plain = no_result_reason(REAL_LOGGED_IN_NO_FLIGHT_BODY + " 暂无符合条件的机票信息")
    assert plain == "页面提示无航班结果"

    # 完全没有无结果提示 = 列表没加载出来
    assert no_result_reason(REAL_LOGGED_IN_NO_FLIGHT_BODY) == "未等到航班列表"


# --------------------------------------------------------------------------- #
# 离线 fixture 结构回归(tests/fixtures/qunar_flight_cards.html,真实卡片子树)
def test_fixture_matches_observed_qunar_dom():
    sub = FIXTURE.read_text(encoding="utf-8")
    assert sub.count('class="b-airfly"') == 3
    # 价格:aria-label 与 fix_price[title] 两条属性通路都要在
    assert 'aria-label="报价：400元。' in sub
    assert 'class="fix_price" title="400"' in sub
    # 轮盘结构:隐藏滚轮数字 <b><i title=...> + 可见数字 <b title=...>
    assert 'class="prc_wp"' in sub and 'class="rel"' in sub
    assert 'class="prc"' in sub
    # 航班号、航司 logo、机场节点、时刻
    assert "$CZ8803" in sub and "air-logo" in sub
    assert 'class="airport"' in sub
    assert "大兴机场" in sub and "虹桥机场" in sub
    assert "20:00" in sub and "22:10" in sub
