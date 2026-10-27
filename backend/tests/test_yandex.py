"""Yandex configuration, HTTP contract and concurrent shared-quota reservations."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

import extractor
import providers


def settings(**overrides):
    return {
        "LLM_PROVIDERS": "yandex,yandex2,yandex3,yandex4,yandex5",
        "YANDEX_FOLDER_ID": "folder",
        **{f"YANDEX{n if n > 1 else ''}_API_KEY": f"key{n}" for n in range(1, 6)},
        **overrides,
    }


def test_five_yandex_keys_share_settings_but_never_credentials():
    loaded = providers.load(settings(YANDEX2_FOLDER_ID="other", YANDEX_MODEL="yandexgpt-5.1"))
    assert len(loaded) == 5 and all(p.configured for p in loaded)
    assert [p.key for p in loaded] == [f"key{n}" for n in range(1, 6)]
    assert loaded[0].model == "gpt://folder/yandexgpt-5.1"
    assert loaded[1].model == "gpt://other/yandexgpt-5.1"
    assert loaded[1].base_url == "https://ai.api.cloud.yandex.net/v1"
    assert len({id(p.shared_slots) for p in loaded}) == 1
    missing = providers.load(settings(YANDEX2_API_KEY=""))[1]
    assert not missing.configured
    assert not providers.load(settings(YANDEX_FOLDER_ID=""))[0].configured
    full = providers.load(
        settings(YANDEX_FOLDER_ID="", YANDEX_MODEL="gpt://explicit/yandexgpt-5.1")
    )[0]
    assert full.configured and full.project == "explicit"


def test_yandex_http_contract(monkeypatch):
    [p] = providers.load(settings(LLM_PROVIDERS="yandex"))
    requests = []

    def answer(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]})

    with httpx.Client(transport=httpx.MockTransport(answer)) as client:
        monkeypatch.setattr(providers, "_client", client)
        assert providers.chat(p, "system", "rows", time.monotonic() + 5) == "[]"
    request = requests[0]
    assert str(request.url) == "https://ai.api.cloud.yandex.net/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer key1"
    assert request.headers["x-project"] == "folder"
    assert json.loads(request.content)["model"] == "gpt://folder/aliceai-llm-flash"


@pytest.mark.parametrize("limit", [2, 5])
def test_five_keys_really_run_concurrently_with_shared_cap(limit):
    loaded = providers.load(settings(YANDEX_TOTAL_PARALLEL=str(limit)))
    lock = threading.Lock()
    active = peak = 0
    entered = threading.Event()

    def run(p):
        nonlocal active, peak
        until = time.monotonic() + 3
        while not p.try_acquire():
            assert time.monotonic() < until
            time.sleep(0.001)
        try:
            with lock:
                active += 1
                peak = max(peak, active)
                if active == limit:
                    entered.set()
            assert entered.wait(2)
        finally:
            with lock:
                active -= 1
            p.release()

    with ThreadPoolExecutor(5) as pool:
        list(pool.map(run, loaded))
    assert peak == limit
    # Every reservation is returned, including the shared quota.
    acquired = [p for p in loaded if p.try_acquire()]
    assert len(acquired) == limit
    for p in acquired:
        p.release()


def test_blocked_shared_quota_does_not_leak_key_slots():
    first, second = providers.load(
        settings(LLM_PROVIDERS="yandex,yandex2", YANDEX_TOTAL_PARALLEL="1")
    )
    assert first.try_acquire()
    assert not second.try_acquire()
    first.release()
    assert second.try_acquire()
    second.release()


def test_job_splits_rows_over_five_accounts_and_preserves_order(monkeypatch):
    loaded = providers.load(settings(YANDEX_TOTAL_PARALLEL="5"))
    barrier = threading.Barrier(5)
    seen = set()
    lock = threading.Lock()

    def chat(p, system, user, deadline, cancelled=None):
        with lock:
            seen.add(p.name)
        barrier.wait(timeout=3)
        return json.dumps([{"id": row["id"], "name": row["text"]} for row in json.loads(user)])

    texts = [f"Бумага строка {n}" for n in range(10)]
    monkeypatch.setattr(extractor, "PROVIDERS", loaded)
    monkeypatch.setattr(extractor, "BATCH_SIZE", 2)
    monkeypatch.setattr(extractor, "SLOTS", 5)
    monkeypatch.setattr(providers, "chat", chat)
    with ThreadPoolExecutor(5) as pool:
        monkeypatch.setattr(extractor, "_pool", pool)
        job = extractor.JobExtractor(texts, budget=5)
        rows = [job.get(text) for text in texts]
    assert [row["name"] for row in rows] == texts
    assert seen == {p.name for p in loaded}
    assert all(row["source"] == "llm" for row in rows)
