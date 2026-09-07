"""运行配置(默认值集中在这里,CLI 可覆盖)。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Settings:
    # —— 浏览器会话 ——
    cdp_host: str = "127.0.0.1"
    cdp_port: int = 9222

    # —— 页面等待 ——
    page_timeout_ms: int = 30_000   # 等待航班列表出现的总超时
    settle_ms: int = 2_500          # 列表出现后再静置一段时间等价格渲染
    scroll_rounds: int = 5          # 滚动懒加载轮数(0 = 关闭滚动)
    scroll_settle_ms: int = 1_200   # 每轮滚动后的静置时间

    # —— 输出 ——
    top_n: int = 10                 # 终端展示前 N 条
    artifacts_dir: str = "artifacts"
    dump_on_failure: bool = True    # 失败时把 HTML 存到 artifacts/ 便于修选择器
    # 注:不做截图转储 —— 解析与调试只依赖 DOM/HTML 文本

    # —— 渠道(预留多渠道)——
    sites: tuple[str, ...] = ("ctrip",)
