"""比价决策引擎(纯确定性逻辑,不依赖浏览器/网络,便于单元测试)。"""

from flight_agent.engine.compare import (
    apply_constraints,
    dedupe_flights,
    hhmm_to_min,
    rank,
    recommend,
)

__all__ = ["apply_constraints", "dedupe_flights", "hhmm_to_min", "rank", "recommend"]
