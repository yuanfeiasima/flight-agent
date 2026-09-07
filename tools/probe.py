"""调试探针:把携程真实列表页的 DOM 结构摸清楚(不改动主流程)。

用法: uv run python tools/probe.py
依赖: 先运行 scripts/open_chrome_debug.sh 并保持 Chrome 打开(登录态在 .chrome-profile/)
只保存 HTML(不做截图 —— 解析与调试不依赖图片)。
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from flight_agent.browser import BrowserSession
from flight_agent.adapters.ctrip import (
    CARD_FALLBACK_SELECTORS,
    CARD_SCOPED_SELECTORS,
    PRICE_SELECTORS,
    _card_css,
    _price_css,
)

URL = "https://flights.ctrip.com/online/list/oneway-bjs-sha?depdate=2026-09-15&cabin=Y&adult=1&child=0&infant=0"
OUT = Path("artifacts")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with BrowserSession.connect("127.0.0.1", 9222) as session:
        page = session.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(6000)
        print("URL after goto :", page.url)
        print("TITLE          :", page.title())

        for name, sels in (("scoped", CARD_SCOPED_SELECTORS),
                           ("fallback", CARD_FALLBACK_SELECTORS)):
            for sel in sels:
                try:
                    n = page.locator(sel).count()
                    print(f"card[{name}] {sel!r:60} -> {n}")
                except Exception as e:  # noqa: BLE001
                    print(f"card[{name}] {sel!r:60} -> ERR {e}")
        try:
            print("combined scoped  ->", page.locator(_card_css(use_fallback=False)).count())
            print("combined fallback->", page.locator(_card_css(use_fallback=True)).count())
        except Exception as e:  # noqa: BLE001
            print("combined card  -> ERR", e)
        for sel in PRICE_SELECTORS:
            try:
                n = page.locator(sel).count()
                print(f"price sel {sel!r:60} -> {n}")
            except Exception as e:  # noqa: BLE001
                print(f"price sel {sel!r:60} -> ERR {e}")

        # 打印前 6 张卡片:文本 + 是否空占位
        combined = page.locator(_card_css(use_fallback=False))
        total = combined.count()
        shown = min(total, 6)
        for i in range(shown):
            try:
                txt = combined.nth(i).inner_text(timeout=2000)
            except Exception as e:  # noqa: BLE001
                txt = f"(读取失败 {e})"
            tag = "空占位" if not txt.strip() else f"{len(txt)} 字符"
            print(f"\n--- card[{i}] ({tag}) ---")
            print(txt[:400])

        body_text = page.locator("body").inner_text(timeout=5000)
        fns = re.findall(r"\b[A-Z]{2}\d{3,4}\b", body_text)
        print("\n整页出现的航班号 token 数:", len(fns))
        print("样例:", fns[:40])

        html = page.content()
        (OUT / "probe.html").write_text(html, encoding="utf-8")
        print(f"\nsaved: {OUT/'probe.html'} ({len(html)} bytes)")
        page.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
