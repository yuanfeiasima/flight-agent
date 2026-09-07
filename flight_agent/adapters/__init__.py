"""站点适配器:每个机票网站一个 adapter,统一产出 Flight/SiteResult。"""

from flight_agent.adapters.base import SiteAdapter
from flight_agent.adapters.ctrip import CtripAdapter

__all__ = ["SiteAdapter", "CtripAdapter"]

# 渠道注册表:新增网站时在 adapters/ 下加一个类并在此登记
ADAPTERS = {"ctrip": CtripAdapter}
