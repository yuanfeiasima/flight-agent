"""领域数据模型:查询、航班、渠道抓取结果、最终推荐。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class SearchQuery:
    """一次比价查询(含全部约束,约束默认值见 config / CLI)。"""

    dep_city: str              # 出发城市展示名,如 北京
    arr_city: str              # 到达城市展示名,如 上海
    dep_code: str              # 出发 IATA 城市码,如 BJS
    arr_code: str              # 到达 IATA 城市码,如 SHA
    dep_date: date             # 出发日期
    cabin_keyword: str = "经济"  # 舱位关键字(经济/公务)
    max_stops: int = 0           # 最大允许经停次数,0=只看直飞
    dep_after: str = "00:00"     # 最早出发时刻 HH:MM
    arr_before: str = "23:59"    # 最晚到达时刻 HH:MM

    @property
    def dep_date_str(self) -> str:
        return self.dep_date.isoformat()


@dataclass
class Flight:
    """归一化后的一个航班条目(任意渠道解析结果都填成这个结构)。"""

    airline: str             # 航司,如 中国国航
    flight_no: str           # 航班号,如 CA1831
    dep_airport: str = ""    # 出发机场/城市(展示用,适配器尽力填充)
    arr_airport: str = ""
    dep_time: str = ""       # HH:MM
    arr_time: str = ""
    arr_day_offset: int = 0  # 到达跨天偏移,如 次日到达=1
    stops: int = 0
    stop_info: str = ""      # 如 "经停西安" / "直飞"
    cabin: str = ""          # 经济舱/公务舱/头等舱
    price: Optional[float] = None  # 含税价(人民币);解析失败为 None
    site: str = ""           # 渠道标识,如 ctrip
    raw: str = ""            # 来源卡片原文(诊断用)
    fetched_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict:
        return asdict(self)

    def key(self) -> tuple:
        """去重键:同一物理航班(多渠道比价时同航班取最低价)。"""
        return (self.flight_no, self.dep_airport, self.arr_airport, self.stops)

    def short(self) -> str:
        seg = "直飞" if self.stops == 0 else (self.stop_info or f"经停{self.stops}")
        day = " +1天" if self.arr_day_offset else ""
        price = "—" if self.price is None else f"¥{self.price:,.0f}"
        return (
            f"{self.flight_no} {self.dep_airport}→{self.arr_airport} "
            f"{self.dep_time}-{self.arr_time}{day} {seg} {self.cabin} {price}"
        )


@dataclass
class SiteResult:
    """单个渠道一次抓取的结果(含诊断信息)。"""

    site: str
    url: str
    flights: list[Flight] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    html_path: Optional[str] = None
    screenshot_path: Optional[str] = None
    fetched_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )

    @property
    def ok_count(self) -> int:
        """成功解析出价格的航班数。"""
        return sum(1 for f in self.flights if f.price is not None)

    def to_dict(self) -> dict:
        return {
            "site": self.site,
            "url": self.url,
            "flights": [f.to_dict() for f in self.flights],
            "warnings": self.warnings,
            "html_path": self.html_path,
            "screenshot_path": self.screenshot_path,
            "fetched_at": self.fetched_at,
        }


@dataclass
class Recommendation:
    """比价决策引擎的输出:谁是最低价,完整排序,以及诊断。"""

    best: Optional[Flight]
    ranked: list[Flight]  # 已按 (价格, 出发时刻) 升序、满足全部约束的航班
    dropped_no_price: int
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "best": self.best.to_dict() if self.best else None,
            "ranked": [f.to_dict() for f in self.ranked],
            "dropped_no_price": self.dropped_no_price,
            "warnings": self.warnings,
        }
