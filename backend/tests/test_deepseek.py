"""DeepSeek's HTTP contract and single-key provider isolation."""

import json
import time

import httpx

import extractor
import providers


def test_single_key_ignores_other_configured_services():
    [provider] = providers.load(
        {
            "DEEPSEEK_API_KEY": " one-key ",
            "YANDEX_API_KEY": "other-key",
            "GIGACHAT_CREDENTIALS": "other-credentials",
        }
    )
    assert provider.name == "deepseek" and provider.key == "one-key"
    assert provider.parallel == 4
    assert not providers.load({"YANDEX_API_KEY": "other-key"})[0].configured


def test_deepseek_extracts_attributes_without_thinking(monkeypatch):
    [provider] = providers.load({"DEEPSEEK_API_KEY": "key", "DEEPSEEK_REASONING_EFFORT": "high"})
    text = "Бумага офисная А4 80 г/м2"
    answer = json.dumps(
        [{"id": 0, "name": "Бумага офисная", "characteristics": [], "search_text": text}]
    )

    def handle(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer key"
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-flash"
        assert payload["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in payload
        assert json.loads(payload["messages"][1]["content"]) == [{"id": 0, "text": text}]
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": answer}}]}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(providers, "_client", client)
        answer = providers.chat(
            provider,
            extractor.SYSTEM_PROMPT,
            json.dumps([{"id": 0, "text": text}]),
            time.monotonic() + 5,
        )
    [attributes] = extractor.to_attributes(answer, [text], provider.name)
    assert attributes["source"] == "llm" and attributes["provider"] == "deepseek"
    assert attributes["name"] == "Бумага офисная"
