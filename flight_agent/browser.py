"""浏览器会话层:通过 CDP 附加到用户已登录的真实 Chrome。

关键设计:agent 不负责登录、不新建带登录态的浏览器;它“附加”到用户人工
登录好的 Chrome 上,复用同一条登录态。关闭连接不会关闭用户的 Chrome。
"""

from __future__ import annotations

import logging

from playwright.sync_api import Playwright, sync_playwright

log = logging.getLogger("flight-agent.browser")


class BrowserSession:
    """包装一个通过 CDP 附加的真实 Chrome(用户已登录)。"""

    def __init__(self, pw: Playwright, browser, cdp_url: str):
        self._pw = pw
        self._browser = browser
        self.cdp_url = cdp_url

    # —— 上下文管理器: with BrowserSession.connect(...) as session ——
    def __enter__(self) -> "BrowserSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    @classmethod
    def connect(cls, host: str = "127.0.0.1", port: int = 9222) -> "BrowserSession":
        cdp_url = f"http://{host}:{port}"
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:  # playwright 连接失败统一转成可读提示
            pw.stop()
            raise ConnectionError(
                f"无法附加到 Chrome({cdp_url})。\n"
                f"请先运行: bash scripts/open_chrome_debug.sh 并保持该 Chrome 打开。\n"
                f"原始错误: {exc}"
            ) from exc
        session = cls(pw, browser, cdp_url)
        if browser.contexts:
            log.info(
                "已附加 Chrome(调试端口 %s),现有窗口 %d 个",
                port,
                len(browser.contexts),
            )
        return session

    # ------------------------------------------------------------------ #
    def new_page(self):
        """在已有窗口(即已登录的那个资料目录)里开新标签页干活。

        注意:必须复用已有 context,否则新 context 不带你的登录 Cookie。
        """
        if not self._browser.contexts:
            raise RuntimeError(
                "当前 Chrome 没有任何窗口。请打开一个标签页(如 https://flights.ctrip.com)后重试。"
            )
        ctx = self._browser.contexts[0]
        return ctx.new_page()

    def check(self) -> str:
        """健康检查:返回第一个页面标题,确认连的是你的真实浏览器。"""
        if not self._browser.contexts:
            return "(没有打开的页面)"
        pages = self._browser.contexts[0].pages
        return pages[0].title if pages else "(窗口内没有标签页)"

    def close(self) -> None:
        """断开 CDP 连接(不关闭用户的 Chrome)。"""
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            self._pw.stop()
