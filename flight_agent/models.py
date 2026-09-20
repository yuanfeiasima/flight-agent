"""领域数据模型:查询、航班、渠道抓取结果、最终推荐。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Optional

_WS_RE = re.compile(r"\s+")


def norm_airport(name: str) -> str:
    """机场名归一化(仅用于跨渠道比对,不改展示):去掉空白与“国际”字样。

    例:携程“大兴国际机场T2” / 去哪儿“北京大兴机场T2” → “大兴机场T2” / “北京大兴机场T2”。
    各渠道城市前缀写法不一,所以同一航班的主判据仍是航班号 + 起降时刻,
    本函数只作为时刻缺失时的兜底。
    """
    return _WS_RE.sub("", (name or "").replace("国际机场", "机场")).lower()


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
    dep_before: str = "23:59"    # 最晚出发时刻 HH:MM(与 dep_after 构成出发时间窗)
    arr_before: str = "23:59"    # 最晚到达时刻 HH:MM

    @property
    def dep_date_str(self) -> str:
        return self.dep_date.isoformat()


@dataclass
class Quote:
    """某个渠道对同一航班的报价(比价明细的一行)。

    同一物理航班在多个渠道出现时,每个渠道各留一条 Quote,
    这样界面才能展示“携程 ¥400 / 去哪儿 ¥388”而不仅仅是最低价。
    """

    site: str                  # 渠道标识,如 ctrip / qunar
    price: Optional[float] = None
    url: str = ""              # 该渠道的查询结果页(便于人工点回去复核)

    def to_dict(self) -> dict:
        return asdict(self)


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
    quotes: list[Quote] = field(default_factory=list)  # 各渠道报价(比价明细)
    fetched_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict:
        # asdict() 不会带上 @property,而界面/存档需要跨渠道差价,所以显式补上
        data = asdict(self)
        data["spread"] = self.spread
        data["site_count"] = self.site_count
        return data

    # —— 比价明细的便捷视图(界面/终端共用,避免各自重复实现) ——
    @property
    def priced_quotes(self) -> list[Quote]:
        """有价格的报价,按价格升序。"""
        return sorted(
            (q for q in self.quotes if q.price is not None),
            key=lambda q: (q.price, q.site),
        )

    @property
    def spread(self) -> float:
        """各渠道报价的最大差价(只有 1 个渠道时为 0)。"""
        prices = [q.price for q in self.priced_quotes]
        return round(max(prices) - min(prices), 2) if len(prices) > 1 else 0.0

    @property
    def site_count(self) -> int:
        return len({q.site for q in self.priced_quotes})

    def key(self) -> tuple:
        """去重键:同一物理航班(多渠道比价时同航班取最低价)。

        跨渠道时机场写法不同(携程“大兴国际机场” vs 去哪儿“北京大兴机场”),
        因此同一航班的主判据是**航班号 + 起降时刻**;时刻缺失时才退回机场名。
        """
        if self.dep_time and self.arr_time:
            return (self.flight_no, self.dep_time, self.arr_time, self.stops)
        return (
            self.flight_no,
            norm_airport(self.dep_airport),
            norm_airport(self.arr_airport),
            self.stops,
        )

    def is_richer_than(self, other: "Flight") -> bool:
        """展示信息是否更完整(机场/航司名更长者更具体),用于合并同航班时择优。"""
        return (len(self.dep_airport) + len(self.arr_airport) + len(self.airline)) > (
            len(other.dep_airport) + len(other.arr_airport) + len(other.airline)
        )

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
