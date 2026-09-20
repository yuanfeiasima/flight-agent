"""网页版 GUI 的静态与语法回归(防止“页面打不开/卡在初始化”再次发生)。

背景:index.html 内联脚本里出现过一处三元运算符括号错位
(`... +" · ":"") + "符合 "…条")`),`:` 落在未闭合的 `(` 内。这类错误会让
**整个内联脚本解析失败**,于是按钮、健康检查、历史全部不工作,页面永远停在
“检测 Chrome…”。而它既不会让括号总数失衡,也不影响 Python 测试,所以必须
用真正的 JS 引擎来校验 —— 本文件用工程已有的 Playwright + 调试 Chrome 做这件事
(Chrome 不在线时自动跳过,不阻塞离线自检)。
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

import pytest

INDEX = Path(__file__).parent.parent / "flight_agent" / "webapp" / "static" / "index.html"
CDP_HOST, CDP_PORT = "127.0.0.1", 9222

_SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.S)
_ID_SELECTOR_RE = re.compile(r"""\$\("#([A-Za-z0-9_-]+)"\)""")
_HTML_ID_RE = re.compile(r"""id="([A-Za-z0-9_-]+)""")


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def _script() -> str:
    m = _SCRIPT_RE.search(_html())
    assert m, "index.html 里找不到内联 <script>(GUI 结构变了?)"
    return m.group(1)


def _cdp_alive() -> bool:
    try:
        with urllib.request.urlopen(
            f"http://{CDP_HOST}:{CDP_PORT}/json/version", timeout=2
        ) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("Browser"))
    except Exception:  # noqa: BLE001 未启动调试 Chrome 属正常情况
        return False


# --------------------------------------------------------------------------- #
# 静态检查:脚本引用的每个 #id 都必须真实存在于 HTML 里
def test_gui_script_dom_ids_all_exist():
    """脚本里 $("#foo") 的 foo 必须在 HTML 中有 id="foo",否则一跑就 null 报错。"""
    html, script = _html(), _script()
    referenced = set(_ID_SELECTOR_RE.findall(script))
    declared = set(_HTML_ID_RE.findall(html))
    assert referenced, "脚本里没有解析到任何 $('#id'),选择器写法可能变了"
    missing = sorted(referenced - declared)
    assert not missing, f"脚本引用了 HTML 中不存在的元素 id: {missing}"


def test_gui_html_has_core_controls():
    """核心交互元素(表单/按钮/结果表/历史)不能缺,缺了 GUI 就没法用。"""
    html = _html()
    for dom_id in ("qf", "dep", "arr", "date", "cabin", "stops", "top",
                   "go", "tbody", "historyList"):
        assert f'id="{dom_id}"' in html, f"GUI 缺少必要元素 #{dom_id}"


def test_gui_script_delimiters_are_balanced():
    """快速兜底:括号/花括号/方括号总数应相等(比 JS 解析弱,但零成本)。"""
    script = _script()
    for open_ch, close_ch in (("(", ")"), ("{", "}"), ("[", "]")):
        assert script.count(open_ch) == script.count(close_ch), (
            f"内联脚本 {open_ch}{close_ch} 不配对:"
            f"{script.count(open_ch)} vs {script.count(close_ch)}"
        )


# --------------------------------------------------------------------------- #
# 真语法校验:交给 Chrome 的 JS 引擎解析(需要调试 Chrome,缺失则跳过)
@pytest.mark.skipif(not _cdp_alive(), reason="未连接调试 Chrome(CDP 9222),跳过 JS 语法校验")
def test_gui_script_parses_in_real_js_engine():
    """把内联脚本交给真实 JS 引擎解析:语法错误会让整页脚本失效,必须拦住。

    连接不上/超时(Chrome 正忙、刚被关掉)时跳过而不是判失败 —— 这条用例的价值
    在于“用真引擎解析语法”,不该因为环境抖动变成噪音。
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    script = _script()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(
                f"http://{CDP_HOST}:{CDP_PORT}", timeout=15_000
            )
            try:
                page = browser.contexts[0].new_page()
                try:
                    result = page.evaluate(
                        "(src) => { try { new Function(src); return 'OK'; }"
                        " catch (e) { return 'ERR: ' + e.message; } }",
                        script,
                    )
                finally:
                    page.close()
            finally:
                browser.close()      # 只断开 CDP,不会关掉你的 Chrome
    except (PlaywrightTimeout, PlaywrightError) as exc:
        pytest.skip(f"调试 Chrome 当前不可用,跳过 JS 语法校验: {type(exc).__name__}")

    assert result == "OK", f"index.html 内联脚本语法错误: {result}"
