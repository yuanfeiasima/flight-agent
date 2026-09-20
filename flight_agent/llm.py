"""用 OpenAI 兼容文本接口把自然语言需求转换为结构化机票查询。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import date
from typing import Any, Callable

from flight_agent.config import LLMSettings
from flight_agent.models import SearchQuery
from flight_agent.query import make_search_query


class LLMError(RuntimeError):
    pass


def _json_from_content(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError("模型没有返回合法 JSON。") from exc
    if not isinstance(value, dict):
        raise LLMError("模型返回值必须是 JSON 对象。")
    return value


class FlightQueryLLM:
    """DeepSeek 默认实现；换 base_url/model 即可使用其他兼容模型。"""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ):
        self.settings = settings or LLMSettings()
        self._opener = opener

    def parse(self, prompt: str, *, today: date | None = None) -> SearchQuery:
        if not prompt or not prompt.strip():
            raise ValueError("请输入机票需求。")
        if not self.settings.api_key:
            raise LLMError(
                "未配置模型密钥。请设置 FLIGHT_AGENT_LLM_API_KEY（或 DEEPSEEK_API_KEY）。"
            )
        current = today or date.today()
        system = (
            "你是机票查询参数提取器，只处理用户的文本。"
            "返回一个 JSON 对象，不要 Markdown，不要解释。字段必须包含："
            "dep、arr、date、cabin、max_stops、dep_after、dep_before、arr_before。"
            "dep 和 arr 使用城市中文名或三位 IATA 城市码，不要填机场名称。"
            "date 必须是 YYYY-MM-DD；cabin 只能是经济、公务、头等；"
            "max_stops 是 0 到 2 的整数；时间为 HH:MM。"
            "dep_after/dep_before 是出发时间窗（用户说“18点到23点之间出发”就填这两个），"
            "arr_before 是最晚到达时刻。"
            "缺省值：经济舱、仅直飞、dep_after=00:00、dep_before=23:59、arr_before=23:59。"
            f"今天是 {current.isoformat()}，相对日期以此计算。"
        )
        body = json.dumps(
            {
                "model": self.settings.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt.strip()},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.settings.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise LLMError(f"模型接口返回 HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise LLMError(f"无法连接模型接口: {exc}") from exc
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError("模型接口响应结构不正确。") from exc
        return make_search_query(_json_from_content(content), today=current)


def with_overrides(settings: LLMSettings, *, model: str | None, base_url: str | None) -> LLMSettings:
    return replace(
        settings,
        model=model or settings.model,
        base_url=base_url or settings.base_url,
    )
