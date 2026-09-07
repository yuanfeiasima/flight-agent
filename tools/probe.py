"""调试探针:把携程真实列表页的 DOM 结构摸清楚(不改动主流程)。

用法: uv run python tools/probe.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from flight_agent.browser import BrowserSession
from flight_agent.adapters.ctrip import CARD_SELECTORS, PRICE_SELECTORS, _card_css, _price_css

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

        for sel in CARD_SELECTORS:
            try:
                n = page.locator(sel).count()
                print(f"card sel {sel!r:60} -> {n}")
            except Exception as e:  # noqa: BLE001
                print(f"card sel {sel!r:60} -> ERR {e}")
        try:
            print("combined card  ->", page.locator(_card_css()).count())
        except Exception as e:  # noqa: BLE001
            print("combined card  -> ERR", e)
        for sel in PRICE_SELECTORS:
            try:
                n = page.locator(sel).count()
                print(f"price sel {sel!r:60} -> {n}")
            except Exception as e:  # noqa: BLE001
                print(f"price sel {sel!r:60} -> ERR {e}")

        # 打印前 8 张候选卡片的 inner_text,便于观察结构
        combined = page.locator(_card_css())
        total = combined.count()
        shown = min(total, 8)
        for i in range(shown):
            try:
                txt = combined.nth(i).inner_text()
                print(f"\n--- card[{i}] inner_text ({len(txt)} chars) ---")
                print(txt[:500])
            except Exception as e:  # noqa: BLE001
                print(f"card[{i}] read ERR {e}")

        body_text = page.locator("body").inner_text(timeout=5000)
        import re as _re
        fns = _re.findall(r"\b[A-Z]{2}\d{3,4}\b", body_text)
        print("\n整页出现的航班号 token 数:", len(fns))
        print("样例:", fns[:40])

        html = page.content()
        (OUT / "probe.html").write_text(html, encoding="utf-8")
        page.screenshot(path=str(OUT / "probe.png"), full_page=False)
        print(f"\nsaved: {OUT/'probe.html'} ({len(html)} bytes) , {OUT/'probe.png'}")
        page.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
