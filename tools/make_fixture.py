"""从真实页面 HTML 提取“航班列表子树”,固化为离线测试 fixture。

真实页面保存: tools/probe.py → artifacts/probe.html
本工具:      uv run python tools/make_fixture.py
产出:        tests/fixtures/ctrip_flight_list_fragment.html
             (只需 .flight-list.root-flights 那一段,约 20KB,可入库做回归测试;
              之后改选择器/解析逻辑,不连浏览器也能离线验证)

用法: uv run python tools/make_fixture.py [源HTML路径] [输出路径]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MARKER = 'class="flight-list root-flights"'
DEFAULT_SRC = Path("artifacts/probe.html")
DEFAULT_OUT = Path("tests/fixtures/ctrip_flight_list_fragment.html")


def extract_flight_list(doc: str) -> str:
    """深度匹配,截取 <div class="flight-list root-flights"> … </div> 整段子树。"""
    i = doc.find(MARKER)
    if i < 0:
        raise SystemExit(f"源 HTML 中找不到 {MARKER!r};请先用 tools/probe.py 生成真实页面。")
    open_end = doc.find(">", i) + 1
    pat = re.compile(r"<div\b|</div\s*>")
    j, depth = open_end, 0
    while True:
        m = pat.search(doc, j)
        if not m:
            raise SystemExit("HTML 结构异常: 未找到列表容器的闭合标签。")
        depth += -1 if m.group(0).startswith("</") else 1
        if depth == 0:
            return doc[i : m.start()]
        j = m.end()


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    doc = src.read_text(encoding="utf-8")
    subtree = extract_flight_list(doc)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(subtree, encoding="utf-8")

    # 打印关键统计,便于人工核对 / 写测试断言
    print(f"来源     : {src} ({len(doc)} bytes)")
    print(f"输出     : {out} ({len(subtree)} bytes)")
    print(f"flight-item.domestic 开标签: {subtree.count('class=\"flight-item domestic\"')}")
    real = re.findall(r"\b(?:CA|MU|CZ|HU|3U|MF|ZH|SC|KN|9C|HO|GS|GJ|QW|DZ|EU|TV|PN|FU|NS)\d{3,4}\b", subtree)
    print(f"真实航班号 token(去重前): {len(real)} {real}")
    print(f"data-testid=flight-item-N: {len(re.findall(r'data-testid=\"flight-item-\\d+\"', subtree))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
