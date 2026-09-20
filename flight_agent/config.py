"""运行配置(默认值集中在这里,CLI 可覆盖)。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # —— 浏览器会话 ——
    cdp_host: str = "127.0.0.1"
    cdp_port: int = 9222

    # —— 页面等待 ——
    page_timeout_ms: int = 30_000   # 等待航班列表出现的总超时
    settle_ms: int = 4_000          # 列表出现后再静置一段时间等价格渲染
    scroll_rounds: int = 6          # 滚动懒加载轮数(0 = 关闭滚动)
    scroll_settle_ms: int = 1_500   # 每轮滚动后的静置时间
    # 懒加载"拉满"的判定:连续多少轮卡片数不再增长才算加载完,以及总时间预算。
    # 为什么需要这两个:懒加载在两次批量渲染之间存在平台期,只看一轮不变就收手会
    # 严重漏抓(实测携程冷启动只抓到 13 条,而完整列表有 185 条)。
    load_stable_rounds: int = 3
    load_timeout_ms: int = 30_000

    # —— 输出 ——
    top_n: int = 10                 # 终端展示前 N 条
    artifacts_dir: str = "artifacts"
    dump_on_failure: bool = True    # 失败时把 HTML 存到 artifacts/ 便于修选择器
    # 注:不做截图转储 —— 解析与调试只依赖 DOM/HTML 文本

    # —— 渠道 ——
    # 默认查询携程、去哪儿两个网站（已完成适配），并行查询后汇总结果
    # 可选渠道: ctrip, qunar, tongcheng, fliggy
    # 注：tongcheng 和 fliggy 的适配器尚未完成，暂不启用
    sites: tuple[str, ...] = ("ctrip", "qunar")
    stop_after_first_success: bool = False  # 查询所有配置的网站
    # 去哪儿每页只有 20 条且按价格升序,只看第一页会漏掉整段晚班机,所以默认翻 3 页。
    # 调大覆盖更全但更慢;设为 1 就退回"只抓第一页"的旧行为。
    qunar_max_pages: int = 3


@dataclass
class LLMSettings:
    """OpenAI 兼容模型配置；默认使用 DeepSeek，可完全由环境变量替换。"""

    base_url: str = field(
        default_factory=lambda: os.getenv("FLIGHT_AGENT_LLM_BASE_URL", "https://api.deepseek.com")
    )
    model: str = field(
        default_factory=lambda: os.getenv("FLIGHT_AGENT_LLM_MODEL", "deepseek-chat")
    )
    api_key: str = field(
        default_factory=lambda: os.getenv("FLIGHT_AGENT_LLM_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY", ""),
        repr=False,
    )
    timeout_s: int = 30
