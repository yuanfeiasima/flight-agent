"""站点适配器:每个机票网站一个 adapter,统一产出 Flight/SiteResult。"""

from flight_agent.adapters.base import SiteAdapter
from flight_agent.adapters.ctrip import CtripAdapter
from flight_agent.adapters.fliggy import FliggyAdapter
from flight_agent.adapters.qunar import QunarAdapter
from flight_agent.adapters.tongcheng import TongchengAdapter

__all__ = [
    "SiteAdapter",
    "CtripAdapter",
    "QunarAdapter",
    "TongchengAdapter",
    "FliggyAdapter",
]

# 渠道注册表:新增网站时在 adapters/ 下加一个类并在此登记
ADAPTERS = {
    "ctrip": CtripAdapter,
    "qunar": QunarAdapter,
    "tongcheng": TongchengAdapter,
    "fliggy": FliggyAdapter,
}
