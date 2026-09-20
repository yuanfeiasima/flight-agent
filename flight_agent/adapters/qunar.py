"""去哪儿桌面航班列表适配器，只读取渲染后的 DOM 文本与属性。"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from flight_agent.adapters.base import (
    SiteAdapter,
    clean_price,
    guess_flight_no,
    load_until_stable,
)
from flight_agent.models import Flight, SearchQuery, SiteResult

log = logging.getLogger("flight-agent.qunar")

CARD_SELECTORS = ["div.m-airfly-lst div.b-airfly", "div.b-airfly"]
# 价格节点:aria-label 最准(报价：400元),fix_price 的 title 次之。
PRICE_LABEL_SELECTORS = [".col-price .prc", ".prc", ".col-price"]
PRICE_TITLE_SELECTORS = [".fix_price", ".col-price [title]", ".prc [title]"]
_HHMM_RE = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?!\d)")
_CABIN_RE = re.compile(r"(经济舱|公务舱|商务舱|头等舱)")
_NO_RESULT_HINTS = ("暂无航班", "没有找到", "无符合条件", "暂无符合条件", "换个条件试试")
# 去哪儿未登录时同样返回“暂无符合条件的机票信息”,但页面头部会出现登录入口。
# 这两种情况对用户的处置完全不同(一个要去登录,一个是真没票),必须区分开。
_LOGIN_HINTS = ("请登录", "或免费注册", "立即登录", "登录后查看")
_LOGIN_WARNING = (
    "去哪儿需要登录后才返回航班列表 —— 请在自动打开的那个 Chrome 里"
    "登录一次去哪儿(登录态会保存在应用自己的资料目录里,以后不用重复登录)。"
)

# —— 价格只认属性,不认文本 ——
# 去哪儿把价格渲染成“滚动数字轮盘”(odometer):
#   <p class="prc" aria-label="报价：400元。…">
#     <span class="fix_price" title="400">
#       <span class="prc_wp"><em class="rel">
#         <b><i title="400">4</i><i title="400">0</i><i title="400">7</i></b>  <- 隐藏的滚轮数字
#         <b title="400">0</b>                                              <- 当前可见数字
#       </em></span>
#     </span>
#   </p>
# inner_text 会把滚轮里所有数字按 DOM 顺序拼起来(如 "¥ 374 0 4 0" 甚至 "¥85"),
# 每次渲染滚轮停位不同 ⇒ 文本解析出的是随机数。真实金额只存在于
# aria-label 与 title 属性里,所以这里必须读属性;读不到就返回 None(判为“无价”),
# 绝不退化成文本猜测 —— 一个错误低价会直接毁掉“最低价推荐”。
_PRICE_RANGE = (30.0, 100_000.0)
_LABEL_PRICE_RE = re.compile(r"报\s*价\s*[:：]\s*¥?\s*([0-9][0-9,]*)")
_AMOUNT_RE = re.compile(r"^\s*¥?\s*([0-9][0-9,]*)\s*(?:元)?\s*$")


def _in_range(value: float) -> Optional[float]:
    low, high = _PRICE_RANGE
    return value if low <= value <= high else None


def price_from_label(label: str) -> Optional[float]:
    """从 aria-label="报价：400元。点击enter…" 里取出金额(纯函数)。"""
    m = _LABEL_PRICE_RE.search(label or "")
    if not m:
        return None
    return _in_range(float(m.group(1).replace(",", "")))


def price_from_title(title: str) -> Optional[float]:
    """fix_price 的 title / 滚轮 <i> 的 title 就是纯金额,如 "400"(纯函数)。"""
    m = _AMOUNT_RE.match(title or "")
    if not m:
        return None
    return _in_range(float(m.group(1).replace(",", "")))


def no_result_reason(body: str) -> str:
    """没抓到卡片时,根据页面文本判断真实原因(纯函数,可离线回归)。

    关键区别:去哪儿**未登录**时也返回“暂无符合条件的机票信息”,只是页面头部
    多了登录入口。若不区分,用户会以为“这条航线没票”,而实际上只要登录就行。
    """
    text = body or ""
    if not any(hint in text for hint in _NO_RESULT_HINTS):
        return "未等到航班列表"
    if any(hint in text for hint in _LOGIN_HINTS):
        return _LOGIN_WARNING
    return "页面提示无航班结果"


def flight_from_card_text(
    text: str,
    q: SearchQuery,
    *,
    price_hint: Optional[float] = None,
    allow_text_price: bool = True,
) -> Optional[Flight]:
    """解析去哪儿卡片的可见文本；不读取图片，也不做 OCR。

    allow_text_price: 文本里是否允许猜价格。**适配器必须传 False** —— 去哪儿价格是
    滚动数字轮盘,文本里的数字是随机的,只有 DOM 属性才可信(见文件顶部说明)。
    纯文本入口(离线测试/无浏览器场景)保留 True 以兼容干净文本。
    """
    flight_no = guess_flight_no(text or "")
    if not flight_no:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    times = _HHMM_RE.findall(text)
    dep_time = times[0] if times else ""
    arr_time = times[1] if len(times) > 1 else ""
    time_indexes = [i for i, line in enumerate(lines) if _HHMM_RE.fullmatch(line)]

    def airport_after(index: int) -> str:
        for line in lines[index + 1:index + 4]:
            if "机场" in line or re.search(r"T\d", line):
                return line
        return ""

    dep_airport = airport_after(time_indexes[0]) if time_indexes else ""
    arr_airport = airport_after(time_indexes[1]) if len(time_indexes) > 1 else ""
    no_index = next((i for i, line in enumerate(lines) if flight_no in line), 0)
    airline = ""
    for line in reversed(lines[max(0, no_index - 3):no_index + 1]):
        candidate = line.replace(flight_no, "").strip()
        if re.fullmatch(r"[\u4e00-\u9fa5·]{2,12}", candidate) and "舱" not in candidate:
            airline = candidate
            break

    # 经停/中转判断：根据页面文本中的明确标识
    if "中转" in text:
        match = re.search(r"中转\s*(\d)", text)
        stops = int(match.group(1)) if match else 1
        stop_info = f"中转{stops}次"
    elif "经停" in text:
        stops, stop_info = 1, "经停"
    elif "转" in text and "中转" not in text:
        # 去哪儿可能只显示"转"字，如"1转"、"经重庆转"等
        match = re.search(r"(\d+)\s*转", text)
        if match:
            stops = int(match.group(1))
            stop_info = f"{stops}转"
        else:
            # 有"转"字但没找到数字，默认为1次中转
            stops, stop_info = 1, "中转"
    else:
        stops, stop_info = 0, "直飞"
    cabin_match = _CABIN_RE.search(text)
    cabin = cabin_match.group(1).replace("商务", "公务") if cabin_match else "经济舱(默认)"
    if price_hint is not None:
        price = price_hint
    elif allow_text_price:
        price = clean_price(text)
    else:
        price = None  # 宁可标“无价”,也不给随机数当最低价
    return Flight(
        airline=airline or flight_no[:2],
        flight_no=flight_no,
        dep_airport=dep_airport or q.dep_city,
        arr_airport=arr_airport or q.arr_city,
        dep_time=dep_time,
        arr_time=arr_time,
        arr_day_offset=1 if ("+1天" in text or "次日" in text) else 0,
        stops=stops,
        stop_info=stop_info,
        cabin=cabin,
        price=price,
        site="qunar",
        raw=text[:300],
    )


class QunarAdapter(SiteAdapter):
    site = "qunar"

    def build_url(self, q: SearchQuery) -> str:
        params = {
            "searchDepartureAirport": q.dep_city,
            "searchArrivalAirport": q.arr_city,
            "searchDepartureTime": q.dep_date_str,
            "nextNDays": "0",
            "startSearch": "true",
            "fromCode": q.dep_code,
            "toCode": q.arr_code,
            "from": "flight_dom_search",
        }
        return "https://flight.qunar.com/site/oneway_list.htm?" + urlencode(params)

    def search(self, session, q: SearchQuery, settings) -> SiteResult:
        page = session.new_page()
        url = self.build_url(q)
        result = SiteResult(site=self.site, url=url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=settings.page_timeout_ms)
            self._focus(page)
            cards = self._wait_cards(page, settings.page_timeout_ms)
            if not cards:
                self._failure(page, q, settings, result, no_result_reason(self._body_text(page)))
                return result
            page.wait_for_timeout(settings.settle_ms)

            # 翻页抓取:去哪儿**每页只有 20 条**,且按价格升序 —— 只看第一页会完全
            # 错过晚上的航班(实测深圳→北京第一页最晚只到 16:30),时刻窗筛选和
            # 跨站比价都会因此落空。所以按配置翻若干页。
            max_pages = max(1, int(getattr(settings, "qunar_max_pages", 1) or 1))
            skipped = 0
            for page_no in range(1, max_pages + 1):
                self._scroll(page, settings)
                parsed, skipped_now = self._parse_cards(page, q, result)
                skipped += skipped_now
                log.debug("去哪儿第 %d 页:解析 %d 条,跳过 %d 个", page_no, parsed, skipped_now)
                if parsed == 0 and page_no == 1:
                    break                       # 第一页就一条都没解析出来,再翻也没意义
                if page_no >= max_pages or not self._goto_next_page(page):
                    break
            if skipped:
                result.warnings.append(f"跳过 {skipped} 个非航班/异常卡片")
            if not result.flights:
                self._failure(page, q, settings, result, "卡片存在但全部解析失败")
            elif result.ok_count == 0:
                result.warnings.append("解析出卡片但均未拿到价格（可能改版/售罄）")
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"抓取异常: {exc}")
            if settings.dump_on_failure:
                self._dump(page, q, settings, result)
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
        return result

    def _parse_cards(self, page, q: SearchQuery, result: SiteResult) -> tuple[int, int]:
        """解析当前页所有卡片,返回 (成功条数, 跳过条数)。"""
        parsed = skipped = 0
        for card in page.locator(", ".join(CARD_SELECTORS)).all():
            try:
                text = card.inner_text(timeout=2000)
                price = self._price(card)
                flight = flight_from_card_text(
                    text, q, price_hint=price, allow_text_price=False
                )
                if flight:
                    result.flights.append(flight)
                    parsed += 1
                else:
                    skipped += 1
            except Exception as exc:  # noqa: BLE001
                log.debug("解析去哪儿单卡失败: %s", exc)
                skipped += 1
        return parsed, skipped

    @staticmethod
    def _first_card_id(page) -> str:
        """当前第一张卡片的标识(用 reactid 里的航班号),用来判断“翻页是否真的换了内容”。"""
        try:
            loc = page.locator(CARD_SELECTORS[0]).first
            if loc.count():
                return loc.get_attribute("data-reactid") or ""
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _goto_next_page(self, page) -> bool:
        """点“下一页”;返回 False 表示已经是最后一页/翻不动。"""
        try:
            nxt = page.locator("a:has-text('下一页')").first
            if nxt.count() == 0 or not nxt.is_visible():
                return False
            if "disabled" in (nxt.get_attribute("class") or ""):
                return False
            before = self._first_card_id(page)
            nxt.click(timeout=4000)
            for _ in range(24):                 # 最多等 12 秒让新一页渲染出来
                page.wait_for_timeout(500)
                now = self._first_card_id(page)
                if now and now != before:
                    self._focus(page)
                    return True
            log.warning("去哪儿翻页后列表没有变化,停止翻页")
            return False
        except Exception as exc:  # noqa: BLE001
            log.debug("去哪儿翻页失败: %s", exc)
            return False

    def _wait_cards(self, page, timeout_ms: int):
        cards = page.locator(", ".join(CARD_SELECTORS))
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            try:
                if cards.count():
                    return cards
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(1000)
        return None

    def _scroll(self, page, settings) -> int:
        """滚动懒加载:与携程同款“连续多轮不增长才算加载完”判定。"""
        cards = page.locator(", ".join(CARD_SELECTORS))

        def step() -> None:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

        return load_until_stable(
            page,
            lambda: cards.count(),
            step,
            settle_ms=settings.scroll_settle_ms,
            timeout_ms=settings.load_timeout_ms,
            stable_rounds=settings.load_stable_rounds,
        )

    def _price(self, card) -> Optional[float]:
        """价格只从属性读(见文件顶部 odometer 说明),读不到返回 None。"""
        for selector in PRICE_LABEL_SELECTORS:
            try:
                locator = card.locator(selector).first
                if locator.count():
                    price = price_from_label(locator.get_attribute("aria-label") or "")
                    if price is not None:
                        return price
            except Exception:  # noqa: BLE001
                continue
        for selector in PRICE_TITLE_SELECTORS:
            try:
                locator = card.locator(selector).first
                if locator.count():
                    price = price_from_title(locator.get_attribute("title") or "")
                    if price is not None:
                        return price
            except Exception:  # noqa: BLE001
                continue
        return None

    @staticmethod
    def _body_text(page) -> str:
        try:
            return page.locator("body").inner_text(timeout=2000)
        except Exception:  # noqa: BLE001
            return ""

    def _failure(self, page, q, settings, result, reason: str) -> None:
        result.warnings.append(reason)
        if settings.dump_on_failure:
            self._dump(page, q, settings, result)

    def _dump(self, page, q, settings, result) -> None:
        """只保存 HTML，不生成截图。"""
        try:
            out = Path(settings.artifacts_dir)
            out.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = out / f"qunar_{q.dep_code.lower()}-{q.arr_code.lower()}_{q.dep_date_str}_{stamp}.html"
            path.write_text(page.content(), encoding="utf-8")
            result.html_path = str(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("转储去哪儿 HTML 失败: %s", exc)
