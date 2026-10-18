"""Yandex protocol, source guards, secret isolation and incomplete answers."""

import json
import threading
import time

import httpx
import pytest

import extractor


@pytest.fixture
def yandex(monkeypatch):
    monkeypatch.setattr(extractor, "PROVIDER", "yandex")
    monkeypatch.setattr(extractor, "YANDEX_BASE_URL", "https://ai.api.cloud.yandex.net/v1")
    monkeypatch.setattr(extractor, "YANDEX_API_KEY", "test-yandex-key")
    monkeypatch.setattr(extractor, "YANDEX_FOLDER_ID", "test-folder")
    monkeypatch.setattr(extractor, "MODEL", "qwen3.6-35b-a3b")
    monkeypatch.setattr(extractor, "CREDENTIALS", "other-provider-secret")
    monkeypatch.setattr(extractor, "ANTHROPIC_API_KEY", "other-messages-secret")
    monkeypatch.setattr(extractor, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(extractor, "_down_until", 0)

    def no_oauth(*args, **kwargs):
        raise AssertionError("Yandex must never use GigaChat OAuth")

    monkeypatch.setattr(extractor, "access_token", no_oauth)
    return monkeypatch


def response(finish="stop"):
    return {
        "choices": [
            {
                "finish_reason": finish,
                "message": {"content": '[{"id":0,"name":"Бумага","brand":"Invented"}]'},
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


@pytest.mark.parametrize("model", ["qwen3.6-35b-a3b", "gpt://test-folder/qwen3.6-35b-a3b"])
def test_yandex_protocol_and_guard(yandex, model):
    yandex.setattr(extractor, "MODEL", model)

    def handle(request):
        assert str(request.url) == "https://ai.api.cloud.yandex.net/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-yandex-key"
        assert request.headers["OpenAI-Project"] == "test-folder"
        assert "x-api-key" not in request.headers
        assert "other-provider-secret" not in str(request.headers)
        assert "other-messages-secret" not in str(request.headers)
        body = json.loads(request.content)
        assert body["model"] == "gpt://test-folder/qwen3.6-35b-a3b"
        assert body["reasoning_effort"] == "none"
        assert body["messages"][0] == {
            "role": "system",
            "content": extractor.SYSTEM_PROMPT,
        }
        assert 0 < request.extensions["timeout"]["read"] <= 5
        return httpx.Response(200, json=response())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        yandex.setattr(extractor, "_client", client)
        [row] = extractor.extract_chunk(["Бумага А4"], time.monotonic() + 5)
    assert row["source"] == "llm" and row["brand"] == ""


@pytest.mark.parametrize("field", ["YANDEX_API_KEY", "YANDEX_FOLDER_ID", "MODEL"])
def test_missing_yandex_configuration_stays_off(yandex, field):
    yandex.setattr(extractor, field, "")
    assert extractor.llm_state() == "off"
    [row] = extractor.extract_chunk(["Бумага"], time.monotonic() + 5)
    assert row["fallback"] == extractor.NO_KEY


@pytest.mark.parametrize("finish", ["length", None])
def test_yandex_incomplete_answer_is_not_accepted(yandex, finish):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response(finish)))
    ) as client:
        yandex.setattr(extractor, "_client", client)
        [row] = extractor.extract_chunk(["Бумага"], time.monotonic() + 5)
    assert row["fallback"] == extractor.ERROR


def test_yandex_auth_failure_does_not_renew_other_credentials(yandex):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "unauthorized"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        yandex.setattr(extractor, "_client", client)
        [row] = extractor.extract_chunk(["Бумага"], time.monotonic() + 5)
    assert row["fallback"] == extractor.ERROR and len(calls) == 1
