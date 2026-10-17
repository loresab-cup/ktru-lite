"""Messages protocol, secret separation, malformed answers and existing fallback behavior."""

import json
import threading
import time

import httpx
import pytest

import extractor


@pytest.fixture
def anthropic(monkeypatch):
    monkeypatch.setattr(extractor, "PROVIDER", "anthropic")
    monkeypatch.setattr(extractor, "ANTHROPIC_BASE_URL", "https://gateway.example")
    monkeypatch.setattr(extractor, "ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setattr(extractor, "CREDENTIALS", "unrelated-gigachat-secret")
    monkeypatch.setattr(extractor, "MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(extractor, "_down_until", 0)
    monkeypatch.setattr(extractor, "_slots", threading.BoundedSemaphore(1))

    def no_oauth(*args, **kwargs):
        raise AssertionError("Messages provider must never call GigaChat OAuth")

    monkeypatch.setattr(extractor, "access_token", no_oauth)
    return monkeypatch


def answer(stop="end_turn"):
    return {
        "stop_reason": stop,
        "content": [
            {"type": "thinking", "thinking": "ignored"},
            {"type": "text", "text": '[{"id":0,"name":"Бумага",'},
            {"type": "text", "text": '"brand":"InventedBrand"}]'},
        ],
    }


@pytest.mark.parametrize(
    "base", ["https://gateway.example", "https://gateway.example/v1"]
)
def test_messages_protocol_and_secret_separation(anthropic, base):
    anthropic.setattr(extractor, "ANTHROPIC_BASE_URL", base)
    seen = []

    def handle(request):
        seen.append(request)
        assert str(request.url) == "https://gateway.example/v1/messages"
        assert request.headers["x-api-key"] == "test-anthropic-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
        assert "unrelated-gigachat-secret" not in str(request.headers)
        body = json.loads(request.content)
        assert body["system"] == extractor.SYSTEM_PROMPT
        assert body["model"] == "gpt-5.6-luna"
        assert [m["role"] for m in body["messages"]] == ["user"]
        assert json.loads(body["messages"][0]["content"]) == [
            {"id": 0, "text": "Бумага А4"}
        ]
        assert body["max_tokens"] == 4096
        assert 0 < request.extensions["timeout"]["read"] <= 5
        return httpx.Response(200, json=answer())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        anthropic.setattr(extractor, "_client", client)
        [row] = extractor.extract_chunk(["Бумага А4"], time.monotonic() + 5)
    assert row["source"] == "llm" and row["name"] == "Бумага"
    assert row["brand"] == ""  # Existing source guard also applies to the new provider.
    assert len(seen) == 1


def test_messages_401_never_renews_gigachat_token(anthropic):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        anthropic.setattr(extractor, "_client", client)
        [row] = extractor.extract_chunk(["Бумага"], time.monotonic() + 5)
    assert row["source"] == "rules" and row["fallback"] == extractor.ERROR
    assert len(seen) == 1


@pytest.mark.parametrize("stop", ["max_tokens", "tool_use", None])
def test_incomplete_messages_are_not_accepted_as_success(anthropic, stop):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=answer(stop)))
    ) as client:
        anthropic.setattr(extractor, "_client", client)
        [row] = extractor.extract_chunk(["Бумага"], time.monotonic() + 5)
    assert row["fallback"] == extractor.ERROR


def test_messages_retries_server_overload(anthropic):
    statuses = iter([429, 503, 200])
    anthropic.setattr(extractor.time, "sleep", lambda _: None)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(next(statuses), json=answer())
        )
    ) as client:
        anthropic.setattr(extractor, "_client", client)
        assert extractor.call_llm([], time.monotonic() + 5).startswith('[{"id":0')
    with pytest.raises(StopIteration):
        next(statuses)


@pytest.mark.parametrize("field", ["ANTHROPIC_API_KEY", "MODEL"])
def test_messages_requires_its_own_key_and_model(anthropic, field):
    anthropic.setattr(extractor, field, "")
    assert extractor.llm_state() == "off"
    [row] = extractor.extract_chunk(["Бумага"], time.monotonic() + 5)
    assert row["fallback"] == extractor.NO_KEY


def test_messages_expired_budget_sends_no_request(anthropic):
    def handle(_):
        raise AssertionError("Expired budget must not make network calls")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        anthropic.setattr(extractor, "_client", client)
        [row] = extractor.extract_chunk(["Бумага"], time.monotonic() - 1)
    assert row["fallback"] == extractor.BUDGET


def test_unknown_provider_is_off_without_using_gigachat(anthropic):
    anthropic.setattr(extractor, "PROVIDER", "misspelled")
    assert extractor.llm_state() == "off"
