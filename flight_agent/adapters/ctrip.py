"""携程机票适配器(桌面版 www/flights.ctrip.com)。

抽取策略(结构变化时只改本文件):
1. 用候选容器选择器收集“航班卡片”,拿不到则等待到超时;
2. 逐卡解析:航班号/时刻用正则,价格优先走价格节点选择器,
   拿不到时取卡内最小的合理金额兜底;
3. 解析失败的卡计入 warnings/无价统计,不中断整体抓取;
4. 抓取失败(dump_on_failure)时把整页 HTML 与截图存到 artifacts/,便于修正选择器。

注意:低价区间可能被网站折叠(“更多低价”),首版只抓列表主区,README 已注明。
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import List, Optional

from playwright.sync_api import Page

from flight_agent.adapters.base import SiteAdapter, clean_price, guess_flight_no
from flight_agent.models import Flight, SearchQuery, SiteResult

log = logging.getLogger("flight-agent.ctrip")

# —— 携程改版时重点维护的“选择器配置区” ——
CARD_SELECTORS = [
    "div.flight-item",
    "div[class*='flightItem']",
    "div[class*='flight-card']",
    "div[class*='FlightCard']",
]
PRICE_SELECTORS = [
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
_AIRLINE_PREFIX = {  # 航班号前缀 → 航司名(兜底;卡内文本通常已有中文名)
    "CA": "中国国航", "MU": "东方航空", "CZ": "南方航空", "HU": "海南航空",
    "3U": "四川航空", "MF": "厦门航空", "ZH": "深圳航空", "SC": "山东航空",
    "KN": "中国联合航空", "GS": "天津航空", "HO": "吉祥航空", "9C": "春秋航空",
    "BK": "奥凯航空", "G5": "华夏航空", "8L": "祥鹏航空", "KY": "昆明航空",
    "AQ": "九元航空", "EU": "成都航空", "TV": "西藏航空", "JD": "首都航空",
    "PN": "西部航空", "FU": "福州航空", "NS": "河北航空", "GJ": "浙江长龙航空",
    "QW": "青岛航空", "DZ": "东海航空", "GT": "桂林航空", "Y8": "金鹏航空",
}

_CABIN_RE = re.compile(r"(经济舱|公务舱|头等舱|超级经济舱)")


def _card_css() -> str:
    return ", ".join(CARD_SELECTORS)


def _price_css() -> str:
    return ", ".join(PRICE_SELECTORS)


def _close_css() -> str:
    return ", ".join(CLOSE_SELECTORS)


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
    if "直飞" in text:
        return 0, "直飞"
    if "经停" in text:
        return 1, "经停"
    n = text.count("中转")
    if n:
        return min(n, 2), f"中转{n}次"
    # 无明确字样:多数国内列表默认直飞;保持 0 并交给原文诊断
    return 0, "直飞(推测)"


def _pick_price(card, card_text: str) -> Optional[float]:
    """价格解析:优先价格节点,失败则取卡内最小合理金额兜底。"""
    try:
        node = card.locator(_price_css()).first
        if node.count() > 0:
            t = node.inner_text()
            p = clean_price(t)
            if p is not None:
                return p
    except Exception:  # noqa: BLE001 定位器异常不致命
        pass
    amounts = [
        float(x.replace(",", ""))
        for x in re.findall(r"¥\s*([0-9][0-9,]*)", card_text)
    ]
    amounts = [a for a in amounts if 30 <= a <= 100000]  # 排除零散标注
    return min(amounts) if amounts else None


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
            self._dismiss_popups(page)

            cards = self._wait_cards(page, settings)
            if not cards:
                self._record_failure(page, q, settings, result, reason="未等到航班列表")
                return result

            # 列表出现后再静置,等价格等字段渲染完成
            page.wait_for_timeout(settings.settle_ms)
            cards = page.locator(_card_css()).all()

            parsed = skipped = 0
            for card in cards:
                try:
                    if self._parse_card(card, q, result):
                        parsed += 1
                    else:
                        skipped += 1
                except Exception as exc:  # noqa: BLE001 单卡失败不拖垮整体
                    log.debug("解析单卡失败: %s", exc)
                    skipped += 1
            if skipped:
                result.warnings.append(f"跳过 {skipped} 个非航班/异常卡片")
            if parsed == 0:
                self._record_failure(page, q, settings, result, reason="卡片存在但全部解析失败")
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
                close_btn = page.locator(_close_css()).first
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
    def _wait_cards(self, page: Page, settings) -> List:
        deadline = time.time() + settings.page_timeout_ms / 1000.0
        while time.time() < deadline:
            try:
                loc = page.locator(_card_css())
                n = loc.count()
            except Exception:  # noqa: BLE001
                n = 0
            if n > 0:
                return loc
            page.wait_for_timeout(1000)
        return []

    # ------------------------------------------------------------------ #
    def _parse_card(self, card, q: SearchQuery, result: SiteResult) -> bool:
        """解析一张卡片。成功(识别出航班号)返回 True;否则 False(静默跳过,不刷屏警告)。"""
        text = card.inner_text()
        flight_no = guess_flight_no(text)
        if not flight_no:
            return False
        lines = _clean_lines(text)

        # 携程卡片内时刻分行排列:按出现顺序取前两个 HH:MM 为出发/到达
        hhmm = _HHMM_RE.findall(text)
        dep_time = hhmm[0] if hhmm else ""
        arr_time = hhmm[1] if len(hhmm) > 1 else ""

        stops, stop_info = _parse_stops(text)
        cabin_m = _CABIN_RE.search(text)
        cabin = cabin_m.group(1) if cabin_m else "经济舱(默认)"
        price = _pick_price(card, text)
        airline = _airline_from_lines(lines, flight_no) or _AIRLINE_PREFIX.get(
            flight_no[:2], flight_no[:2]
        )
        dep_ap, arr_ap = _airports_from_lines(lines, dep_time, arr_time)

        result.flights.append(
            Flight(
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
        """把现场(HTML+截图)存下来,便于人工/LLM 修正选择器。"""
        try:
            out = Path(settings.artifacts_dir)
            out.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            stem = f"ctrip_{q.dep_code.lower()}-{q.arr_code.lower()}_{q.dep_date_str}_{stamp}"
            html_path = out / f"{stem}.html"
            html_path.write_text(page.content(), encoding="utf-8")
            result.html_path = str(html_path)
            shot = out / f"{stem}.png"
            page.screenshot(path=str(shot), full_page=False)
            result.screenshot_path = str(shot)
            log.info("现场已转储: %s / %s", html_path, shot)
        except Exception as exc:  # noqa: BLE001
            log.warning("转储失败: %s", exc)
