"""携程机票适配器(桌面版 www/flights.ctrip.com)。

抽取策略(结构变化时只改本文件,重点维护“选择器配置区”):
1. 卡片定位优先限定在真实列表容器 `.flight-list` 内(避免误抓头部下拉/推荐位
   里同名的 `.flight-item` 节点),容器找不到时退回旧泛化选择器;
2. 携程列表页**渐进渲染 + 滚动懒加载**:首屏常只有 2~3 张带航班号、其余为空占位
   (~12s 才出列表),滚动后才会补全到 10+ 张。因此:等待出现 → 静置 → 多轮滚动,
   直到卡片槽位数稳定;
3. 字段解析**优先读 DOM 节点**(航班号 `.plane-No`,航空公司 `.airline-logo[alt]`,
   时刻/机场 `.depart-box|.arrive-box` 下的 `.time|.airport|.day`,价格
   `.flight-price/.domestic-flight-price`)。注意 inner_text 会漏掉部分卡片(例如
   部分卡不渲染航班号节点),因此航班号还有一层 **元素 id 兜底**(`airlineNameCA8341_…`)。
   节点都拿不到时才退回纯文本解析 `flight_from_card_text()`(该纯函数无浏览器依赖,
   可离线单测);
4. 单卡失败不拖垮整体;整体失败(dump_on_failure)只转储 HTML(不做截图 ——
   解析与调试从不依赖图片)。
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from playwright.sync_api import Page

from flight_agent.adapters.base import (
    SiteAdapter,
    clean_price,
    guess_flight_no,
    load_until_stable,
)
from flight_agent.models import Flight, SearchQuery, SiteResult

log = logging.getLogger("flight-agent.ctrip")

# ========================================================================== #
# —— 携程改版时重点维护的“选择器配置区” ——
# 首选:限定在真实航班列表容器内(排除头部下拉 / 推荐位等同类名节点)。
#   当前真实结构:div.flight-list.root-flights > div.flight-item.domestic
CARD_SCOPED_SELECTORS = [
    "div.flight-list div.flight-item",
    "div[class*='flightList'] div[class*='flightItem']",
]
# 兜底:容器结构大改导致首选找不到时,退回旧泛化写法(会多抓,但至少不丢)
CARD_FALLBACK_SELECTORS = [
    "div.flight-item",
    "div[class*='flightItem']",
    "div[class*='flight-card']",
    "div[class*='FlightCard']",
]
# 卡片内字段节点(按优先级排,取第一个非空)
NO_SELECTORS = [".plane-No", "span[class*='plane-No']"]
AIRLINE_NAME_SELECTORS = [".airline-name", "span[class*='airline-name']"]
DEP_TIME_SELECTORS = [".depart-box .time", "div[class*='depart-box'] div[class*='time']"]
ARR_TIME_SELECTORS = [".arrive-box .time", "div[class*='arrive-box'] div[class*='time']"]
DEP_AIRPORT_SELECTORS = [".depart-box .airport", "div[class*='depart-box'] div[class*='airport']"]
ARR_AIRPORT_SELECTORS = [".arrive-box .airport", "div[class*='arrive-box'] div[class*='airport']"]
DAY_SELECTORS = [".arrive-box .day", "span[class*='crossDays']", "span[class*='day']"]
PRICE_SELECTORS = [
    "div.domestic-flight-price",
    "div.flight-price",
    "div[class*='price-box'] div[class*='price']",
    "div[class*='price']",
    "span[class*='price']",
    "b[class*='price']",
]
CLOSE_SELECTORS = [
    "div[class*='close']",
    "span[class*='close']",
    "i[class*='close']",
    "button[class*='close']",
]
_NO_RESULT_HINTS = ["没有找到", "无航班", "暂无航班", "抱歉", "无结果"]

_HHMM_RE = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?!\d)")
_FLIGHT_NO_RE = re.compile(r"((?:[A-Z]{2}|[A-Z]\d|\d[A-Z])\d{3,4})")
_AIRLINE_PREFIX = {  # 航班号前缀 → 航司名(兜底;卡内通常有中文名/logo alt)
    "CA": "中国国航", "MU": "东方航空", "CZ": "南方航空", "HU": "海南航空",
    "3U": "四川航空", "MF": "厦门航空", "ZH": "深圳航空", "SC": "山东航空",
    "KN": "中国联合航空", "GS": "天津航空", "HO": "吉祥航空", "9C": "春秋航空",
    "BK": "奥凯航空", "G5": "华夏航空", "8L": "祥鹏航空", "KY": "昆明航空",
    "AQ": "九元航空", "EU": "成都航空", "TV": "西藏航空", "JD": "首都航空",
    "PN": "西部航空", "FU": "福州航空", "NS": "河北航空", "GJ": "浙江长龙航空",
    "QW": "青岛航空", "DZ": "东海航空", "GT": "桂林航空", "Y8": "金鹏航空",
}

_CABIN_RE = re.compile(r"(经济舱|公务舱|头等舱|超级经济舱)")

# 抓所有后代元素 id,用于航班号兜底(如 airlineNameCA8341_… / comfort-MU5137_…)
_COLLECT_IDS_JS = """(el) => {
  const out = [];
  for (const n of el.querySelectorAll('[id]')) out.push(n.id);
  return out.join(' ');
}"""
# 滚动懒加载:列表容器滚到底 + 页面下滚
_SCROLL_LIST_JS = """(els) => {
  const list = document.querySelector('.flight-list');
  if (list) list.scrollTo(0, list.scrollHeight);
  window.scrollBy(0, 800);
}"""


def _css(sels: list[str]) -> str:
    return ", ".join(sels)


# ========================================================================== #
# —— 纯文本解析(无浏览器依赖,可离线单测) ——

def _clean_lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _is_airport_line(s: str) -> bool:
    return ("机场" in s) or bool(re.search(r"T\d", s))


def _airports_from_lines(lines: list[str], dep_time: str, arr_time: str) -> tuple[str, str]:
    """时刻行通常紧跟机场行;从时刻行向后扫几行找机场名。"""
    dep_ap = arr_ap = ""
    for i, ln in enumerate(lines):
        window = lines[i + 1 : i + 4]
        if ln == dep_time and not dep_ap:
            dep_ap = next((w for w in window if _is_airport_line(w)), "")
        elif ln == arr_time and not arr_ap:
            arr_ap = next((w for w in window if _is_airport_line(w)), "")
    return dep_ap, arr_ap


def _airline_from_lines(lines: list[str], flight_no: str) -> str:
    """航司名通常在航班号行的上方(纯中文短行);兼容同一行的前缀写法。"""
    idx = next((i for i, ln in enumerate(lines) if flight_no in ln), None)
    if idx is None:
        return ""
    for up in range(idx - 1, max(-1, idx - 4), -1):
        cand = lines[up]
        if re.fullmatch(r"[\u4e00-\u9fa5·]{2,10}", cand) and not re.search(
            r"舱|折|¥|天|抵|省", cand
        ):
            return cand
    head = lines[idx].split(flight_no, 1)[0]
    m = re.search(r"([\u4e00-\u9fa5·]{2,10})$", head)
    return m.group(1) if m else ""


def _parse_stops(text: str) -> tuple[int, str]:
    """返回 (经停次数, 说明文本)。"""
    if "经停" in text and "中转" not in text:
        return 1, "经停"
    n = text.count("中转")
    if n:
        return min(n, 2), f"中转{n}次"
    if "直飞" in text:
        return 0, "直飞"
    # 无明确字样:多数国内列表默认直飞;保持 0 并交给原文诊断
    return 0, "直飞(推测)"


def _price_from_text(text: str) -> Optional[float]:
    """从卡片文本取最小合理金额(¥30 ~ ¥100000),排除零散标注。"""
    amounts = [
        float(x.replace(",", ""))
        for x in re.findall(r"¥\s*([0-9][0-9,]*)", text or "")
    ]
    amounts = [a for a in amounts if 30 <= a <= 100000]
    return min(amounts) if amounts else None


def flight_from_card_text(
    text: str,
    q: SearchQuery,
    site: str = "ctrip",
    price_hint: Optional[float] = None,
) -> Optional[Flight]:
    """从一张航班卡片的 inner_text 解析出 Flight(纯函数)。

    返回 None 表示这不是一张可用的航班卡(空占位 / 无航班号 / 纯广告),
    调用方应静默跳过、不当作解析失败刷屏。
    price_hint: 适配器优先从“价格节点”取到的金额;为 None 时退化为文本兜底。
    """
    if not text or not text.strip():
        return None
    flight_no = guess_flight_no(text)
    if not flight_no:
        return None
    lines = _clean_lines(text)

    # 携程卡片内时刻分行排列:按出现顺序取前两个 HH:MM 为出发/到达
    hhmm = _HHMM_RE.findall(text)
    dep_time = hhmm[0] if hhmm else ""
    arr_time = hhmm[1] if len(hhmm) > 1 else ""

    stops, stop_info = _parse_stops(text)
    cabin_m = _CABIN_RE.search(text)
    cabin = cabin_m.group(1) if cabin_m else "经济舱(默认)"
    price = price_hint if price_hint is not None else _price_from_text(text)
    airline = _airline_from_lines(lines, flight_no) or _AIRLINE_PREFIX.get(
        flight_no[:2], flight_no[:2]
    )
    dep_ap, arr_ap = _airports_from_lines(lines, dep_time, arr_time)

    return Flight(
        airline=airline,
        flight_no=flight_no,
        dep_airport=dep_ap or q.dep_city,
        arr_airport=arr_ap or q.arr_city,
        dep_time=dep_time,
        arr_time=arr_time,
        arr_day_offset=1 if ("+1天" in text or "次日" in text) else 0,
        stops=stops,
        stop_info=stop_info,
        cabin=cabin,
        price=price,
        site=site,
        raw=text[:300],
    )


# ========================================================================== #
# —— 页面机制(Playwright / DOM) ——

class CtripAdapter(SiteAdapter):
    site = "ctrip"

    # ------------------------------------------------------------------ #
    def build_url(self, q: SearchQuery) -> str:
        cabin = {"经济": "Y", "公务": "C", "头等": "F"}.get(q.cabin_keyword, "Y")
        return (
            "https://flights.ctrip.com/online/list/"
            f"oneway-{q.dep_code.lower()}-{q.arr_code.lower()}"
            f"?depdate={q.dep_date_str}&cabin={cabin}&adult=1&child=0&infant=0"
        )

    # ------------------------------------------------------------------ #
    def search(self, session, q: SearchQuery, settings) -> SiteResult:
        page = session.new_page()
        url = self.build_url(q)
        result = SiteResult(site=self.site, url=url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=settings.page_timeout_ms)
            page.wait_for_timeout(800)
            self._focus(page)
            self._dismiss_popups(page)

            cards = self._wait_cards(page, settings)
            if not cards:
                self._record_failure(page, q, settings, result, reason="未等到航班列表")
                return result

            # 列表渐进渲染:静置一段时间,再滚动懒加载拉满
            page.wait_for_timeout(settings.settle_ms)
            if settings.scroll_rounds > 0:
                self._scroll_list_to_load(page, settings)
            cards = self._locate_cards(page).all()

            parsed = skipped = placeholder = 0
            for card in cards:
                try:
                    text = card.inner_text(timeout=2000)
                except Exception:  # noqa: BLE001 单卡读取失败按跳过处理
                    text = ""
                if not text.strip():
                    placeholder += 1  # 空占位节点(尚未渲染)
                    continue
                try:
                    if self._parse_card(card, text, q, result):
                        parsed += 1
                    else:
                        skipped += 1
                except Exception as exc:  # noqa: BLE001 单卡失败不拖垮整体
                    log.debug("解析单卡失败: %s", exc)
                    skipped += 1
            if placeholder:
                result.warnings.append(
                    f"列表含 {placeholder} 个空占位节点(未渲染/骨架)"
                )
            if skipped:
                result.warnings.append(f"跳过 {skipped} 个非航班/异常卡片")
            if parsed == 0:
                reason = "卡片存在但全部为空占位(列表未渲染/需滚动)"
                if skipped:
                    reason = "卡片存在但全部解析失败"
                self._record_failure(page, q, settings, result, reason=reason)
            elif result.ok_count == 0:
                result.warnings.append("解析出卡片但均未拿到价格(可能改版/需登录/售罄)")

            # 无航班提示检测
            body_text = ""
            try:
                body_text = page.locator("body").inner_text(timeout=3000)
            except Exception:  # noqa: BLE001
                pass
            if any(hint in body_text for hint in _NO_RESULT_HINTS) and not result.flights:
                result.warnings.append("页面提示无航班结果")

        except Exception as exc:  # noqa: BLE001
            log.warning("携程抓取异常: %s", exc)
            result.warnings.append(f"抓取异常: {exc}")
            if settings.dump_on_failure:
                self._dump(page, q, settings, result)
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
        return result

    # ------------------------------------------------------------------ #
    def _dismiss_popups(self, page: Page) -> None:
        """尽力点掉可能遮挡的弹层(登录引导/优惠券等),失败不致命。"""
        for _ in range(3):
            try:
                close_btn = page.locator(_css(CLOSE_SELECTORS)).first
                if close_btn.count() and close_btn.is_visible():
                    close_btn.click(timeout=1500)
                    page.wait_for_timeout(300)
                    continue
            except Exception:  # noqa: BLE001
                pass
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            except Exception:  # noqa: BLE001
                pass
            break

    # ------------------------------------------------------------------ #
    def _locate_cards(self, page: Page):
        """首选限定容器的卡片选择器;找不到再退回泛化写法。"""
        scoped = page.locator(_css(CARD_SCOPED_SELECTORS))
        try:
            if scoped.count() > 0:
                return scoped
        except Exception:  # noqa: BLE001
            pass
        return page.locator(_css(CARD_FALLBACK_SELECTORS))

    # ------------------------------------------------------------------ #
    def _wait_cards(self, page: Page, settings) -> List:
        deadline = time.time() + settings.page_timeout_ms / 1000.0
        while time.time() < deadline:
            try:
                loc = self._locate_cards(page)
                n = loc.count()
            except Exception:  # noqa: BLE001
                n = 0
            if n > 0:
                return loc
            page.wait_for_timeout(1000)
        return []

    # ------------------------------------------------------------------ #
    def _scroll_list_to_load(self, page: Page, settings) -> int:
        """滚动懒加载:反复“滚到底 → 等新卡片”,直到连续多轮不再增长或超时。

        判定逻辑见 adapters.base.load_until_stable 的说明(单轮平台期会导致漏抓)。
        """
        loc = page.locator(_css(CARD_SCOPED_SELECTORS))

        def count() -> int:
            return loc.count()

        def step() -> None:
            if loc.count():
                loc.first.evaluate(_SCROLL_LIST_JS)

        return load_until_stable(
            page,
            count,
            step,
            settle_ms=settings.scroll_settle_ms,
            timeout_ms=settings.load_timeout_ms,
            stable_rounds=settings.load_stable_rounds,
        )

    # ------------------------------------------------------------------ #
    # —— 单卡节点化抽取 ——
    def _first_text(self, card, sels: list[str], timeout_ms: int = 800) -> str:
        for sel in sels:
            try:
                loc = card.locator(sel).first
                if loc.count() > 0:
                    t = (loc.inner_text(timeout=timeout_ms) or "").strip()
                    if t:
                        return t
            except Exception:  # noqa: BLE001
                continue
        return ""

    def _pick_price(self, card) -> Optional[float]:
        """价格节点优先:取第一个能解析出合理金额的价格节点。"""
        for sel in PRICE_SELECTORS:
            try:
                loc = card.locator(sel).first
                if loc.count() > 0:
                    p = clean_price(loc.inner_text(timeout=800))
                    if p is not None and p >= 30:
                        return p
            except Exception:  # noqa: BLE001
                continue
        return None

    def _flight_no_of(self, card, text: str) -> Optional[str]:
        """航班号:1) .plane-No 节点文本;2) 后代元素 id(airlineNameCA8341_…);
        3) 卡片文本正则。返回 None 表示拿不到。"""
        try:
            t = self._first_text(card, NO_SELECTORS)
            m = _FLIGHT_NO_RE.search(t)
            if m:
                return m.group(1)
        except Exception:  # noqa: BLE001
            pass
        try:
            ids = card.evaluate(_COLLECT_IDS_JS) or ""
            m = _FLIGHT_NO_RE.search(ids)
            if m:
                return m.group(1)
        except Exception:  # noqa: BLE001
            pass
        m = _FLIGHT_NO_RE.search(text or "")
        return m.group(1) if m else None

    def _parse_card(self, card, text: str, q: SearchQuery, result: SiteResult) -> bool:
        """解析一张有内容的卡片(节点优先)。返回 True = 产出 Flight。"""
        flight_no = self._flight_no_of(card, text)
        if not flight_no:
            # 纯文本路径(离线单测覆盖同款判定逻辑)
            fallback = flight_from_card_text(text, q, site=self.site)
            if fallback is None:
                return False
            result.flights.append(fallback)
            return True

        airline = ""
        try:
            img = card.locator("img.airline-logo").first
            if img.count() > 0:
                airline = (img.get_attribute("alt") or "").strip()
        except Exception:  # noqa: BLE001
            pass
        if not airline:
            airline = self._first_text(card, AIRLINE_NAME_SELECTORS)
        if not airline:
            airline = _AIRLINE_PREFIX.get(flight_no[:2], flight_no[:2])

        dep_t = self._first_text(card, DEP_TIME_SELECTORS)
        arr_t = self._first_text(card, ARR_TIME_SELECTORS)
        dep_ap = self._first_text(card, DEP_AIRPORT_SELECTORS)
        arr_ap = self._first_text(card, ARR_AIRPORT_SELECTORS)
        day_t = self._first_text(card, DAY_SELECTORS)
        dep_time = _HHMM_RE.search(dep_t).group(0) if _HHMM_RE.search(dep_t) else ""
        arr_time = _HHMM_RE.search(arr_t).group(0) if _HHMM_RE.search(arr_t) else ""
        arr_day_offset = 1 if re.search(r"\+1天|次日", f"{arr_t} {day_t}") else 0

        stops, stop_info = _parse_stops(text)
        cabin_m = _CABIN_RE.search(text)
        cabin = cabin_m.group(1) if cabin_m else "经济舱(默认)"
        price = self._pick_price(card)
        if price is None:
            price = _price_from_text(text)

        result.flights.append(
            Flight(
                airline=airline,
                flight_no=flight_no,
                dep_airport=dep_ap or q.dep_city,
                arr_airport=arr_ap or q.arr_city,
                dep_time=dep_time,
                arr_time=arr_time,
                arr_day_offset=arr_day_offset,
                stops=stops,
                stop_info=stop_info,
                cabin=cabin,
                price=price,
                site=self.site,
                raw=text[:300],
            )
        )
        return True

    # ------------------------------------------------------------------ #
    def _record_failure(self, page, q, settings, result, reason: str) -> None:
        log.warning("携程抓取失败: %s (dep=%s arr=%s date=%s)",
                    reason, q.dep_code, q.arr_code, q.dep_date_str)
        result.warnings.append(reason)
        if settings.dump_on_failure:
            self._dump(page, q, settings, result)

    # ------------------------------------------------------------------ #
    def _dump(self, page, q, settings, result) -> None:
        """把现场 HTML 存下来,便于人工/LLM 修正选择器。

        设计上不做截图:解析与调试只依赖 DOM/HTML 文本(截图不提供解析所需信息)。
        """
        try:
            out = Path(settings.artifacts_dir)
            out.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            stem = f"ctrip_{q.dep_code.lower()}-{q.arr_code.lower()}_{q.dep_date_str}_{stamp}"
            html_path = out / f"{stem}.html"
            html_path.write_text(page.content(), encoding="utf-8")
            result.html_path = str(html_path)
            log.info("现场 HTML 已转储: %s", html_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("转储失败: %s", exc)
