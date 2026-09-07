"""城市名 → IATA 城市码(携程搜索结果 URL 使用城市码,如 oneway-bjs-sha)。"""

from __future__ import annotations

# 常见国内城市。若某城市缺失或携程对某城市码另有惯例,可直接传三位 IATA 码绕开本表。
CITY_CODES: dict[str, str] = {
    "北京": "BJS",
    "上海": "SHA",
    "广州": "CAN",
    "深圳": "SZX",
    "成都": "CTU",
    "重庆": "CKG",
    "杭州": "HGH",
    "西安": "SIA",
    "武汉": "WUH",
    "南京": "NKG",
    "昆明": "KMG",
    "厦门": "XMN",
    "长沙": "CSX",
    "青岛": "TAO",
    "天津": "TSN",
    "大连": "DLC",
    "郑州": "CGO",
    "沈阳": "SHE",
    "三亚": "SYX",
    "海口": "HAK",
    "乌鲁木齐": "URC",
    "哈尔滨": "HRB",
    "贵阳": "KWE",
    "南宁": "NNG",
    "福州": "FOC",
    "济南": "TNA",
    "太原": "TYN",
    "兰州": "LHW",
    "长春": "CGQ",
    "合肥": "HFE",
    "石家庄": "SJW",
    "南昌": "KHN",
    "呼和浩特": "HET",
    "银川": "INC",
    "西宁": "XNN",
    "拉萨": "LXA",
}


def resolve_code(name_or_code: str) -> str:
    """入参可以是中文城市名,或三位 IATA 码(大小写不敏感,直接透传)。"""
    raw = name_or_code.strip()
    upper = raw.upper()
    if len(upper) == 3 and upper.isalpha():
        return upper
    if raw in CITY_CODES:
        return CITY_CODES[raw]
    raise ValueError(
        f"无法识别的城市: {raw!r}。支持中文城市名或三位 IATA 码。"
        f"内置城市: {', '.join(sorted(CITY_CODES))}"
    )
