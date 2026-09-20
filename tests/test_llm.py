import json
from datetime import date

import pytest

from flight_agent.config import LLMSettings
from flight_agent.llm import FlightQueryLLM, LLMError


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode()


def test_deepseek_compatible_response_becomes_query():
    captured = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        content = json.dumps({
            "dep": "北京", "arr": "上海", "date": "2026-09-18",
            "cabin": "经济", "max_stops": 0,
            "dep_after": "06:00", "arr_before": "12:00",
        }, ensure_ascii=False)
        return _Response({"choices": [{"message": {"content": content}}]})

    settings = LLMSettings(base_url="https://example.test/v1", model="custom-model", api_key="secret")
    q = FlightQueryLLM(settings, opener=opener).parse(
        "下周五上午北京到上海", today=date(2026, 9, 9)
    )
    assert q.dep_code == "BJS" and q.arr_code == "SHA"
    assert q.dep_after == "06:00" and q.arr_before == "12:00"
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["body"]["model"] == "custom-model"
    assert "secret" not in json.dumps(captured["body"])


def test_llm_requires_api_key_before_network():
    with pytest.raises(LLMError, match="密钥"):
        FlightQueryLLM(LLMSettings(api_key="")).parse("北京到上海")
