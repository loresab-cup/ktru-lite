"""Neural network providers: HTTP requests, retries, failover and limits."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

import extractor
import providers
from providers import Provider

ANSWER = '[{"id": 0, "name": "Бумага", "search_text": "Бумага для офисной техники"}]'


def chat_answer(content: str = ANSWER, finish: str = "stop") -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}


def mock(monkeypatch, handler, gigachat: bool = False) -> None:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(providers, "_gigachat_client" if gigachat else "_client", client)


def openai(parallel: int = 1, name: str = "x") -> Provider:
    return Provider(name, "openai", "https://x/v1", "key", "model", parallel)


# --- HTTP ----------------------------------------------------------------------------------


class FakeGigaChat:
    """An OAuth server and a chat endpoint that may reject the first token."""

    def __init__(self, reject_first_chat: bool = False):
        self.tokens = 0
        self.chats = []
        self.reject = reject_first_chat

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == providers.AUTH_URL:
            assert request.headers["Authorization"] == "Basic test-key"
            assert request.headers["RqUID"]  # uuid4 is required by the OAuth endpoint
            self.tokens += 1
            body = {"access_token": f"token-{self.tokens}", "expires_at": 4_000_000_000_000}
            return httpx.Response(200, json=body)
        self.chats.append(request.headers["Authorization"])
        if self.reject:
            self.reject = False
            return httpx.Response(401, json={"message": "expired"})
        assert json.loads(request.content)["temperature"] > 0
        return httpx.Response(200, json=chat_answer())


@pytest.fixture
def gigachat(monkeypatch):
    monkeypatch.setattr(providers, "_token", {"value": "", "expires": 0.0})
    return Provider("gigachat", "gigachat", "https://giga/v1", "test-key", "GigaChat-3-Ultra")


def test_gigachat_token_is_cached_and_used(monkeypatch, gigachat):
    fake = FakeGigaChat()
    mock(monkeypatch, fake, gigachat=True)
    deadline = time.monotonic() + 30
    assert providers.chat(gigachat, "s", "[]", deadline) == ANSWER
    assert providers.chat(gigachat, "s", "[]", deadline) == ANSWER
    assert fake.tokens == 1 and fake.chats == ["Bearer token-1", "Bearer token-1"]


def test_gigachat_renews_rejected_token(monkeypatch, gigachat):
    fake = FakeGigaChat(reject_first_chat=True)
    mock(monkeypatch, fake, gigachat=True)
    assert providers.chat(gigachat, "s", "[]", time.monotonic() + 30) == ANSWER
    assert fake.tokens == 2 and fake.chats == ["Bearer token-1", "Bearer token-2"]


def test_oauth_lock_respects_job_budget(monkeypatch):
    lock = threading.Lock()
    lock.acquire()
    monkeypatch.setattr(providers, "_token_lock", lock)
    try:
        with pytest.raises(TimeoutError):
            providers.access_token("key", deadline=time.monotonic() + 0.02)
    finally:
        lock.release()


def test_oauth_timeout_is_limited_by_remaining_budget(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"]["read"])
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    mock(monkeypatch, handler, gigachat=True)
    monkeypatch.setattr(providers, "_token", {"value": "", "expires": 0.0})
    with pytest.raises(TimeoutError):
        providers.access_token("key", deadline=time.monotonic() + 0.2)
    assert 0 < seen[0] <= 0.2 + 1e-6
    assert not providers._token_lock.locked()


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retry_then_success(monkeypatch, status):
    responses = iter([status, 200])
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)
    mock(monkeypatch, lambda _: httpx.Response(next(responses), json=chat_answer("[]")))
    assert providers.chat(openai(), "s", "u", time.monotonic() + 30) == "[]"
    with pytest.raises(StopIteration):
        next(responses)


def test_slow_gateway_error_is_not_retried(monkeypatch):
    """502 after 37 s is the gateway's own timeout: no second and third try of 37 s each."""
    clock = [0.0]
    calls = []
    monkeypatch.setattr(providers.time, "monotonic", lambda: clock[0])

    def slow_502(request):
        calls.append(clock[0])
        clock[0] += 37.0
        return httpx.Response(502)

    mock(monkeypatch, slow_502)
    with pytest.raises(httpx.TransportError):  # as a silent network: the provider is paused
        providers.chat(openai(), "s", "u", deadline=200.0)
    assert len(calls) == 1


def test_request_cut_by_budget_is_a_timeout(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("cut by the short timeout", request=request)

    mock(monkeypatch, handler)
    with pytest.raises(TimeoutError):
        providers.chat(openai(), "s", "u", time.monotonic() + 5)


def test_silent_network_is_a_transport_error(monkeypatch):
    """Full windows without an answer until the budget ends: the network is down."""
    clock = [0.0]
    monkeypatch.setattr(providers.time, "monotonic", lambda: clock[0])

    def silent(request):
        clock[0] += 100.0
        raise httpx.ReadTimeout("no answer", request=request)

    mock(monkeypatch, silent)
    with pytest.raises(httpx.TransportError):
        providers.chat(openai(), "s", "u", deadline=150.0)


def test_nothing_is_sent_after_cancel(monkeypatch):
    sent = []
    mock(monkeypatch, sent.append)
    stop = threading.Event()
    stop.set()
    with pytest.raises(providers.Cancelled):
        providers.chat(openai(), "s", "u", time.monotonic() + 5, stop)
    assert not sent


def test_openai_request_and_cut_answer(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=chat_answer(finish="stop" if len(seen) == 1 else "length"))

    mock(monkeypatch, handler)
    p = Provider("other", "openai", "https://ai/v1", "secret", "chat-model", 2)
    assert providers.chat(p, "s", "u", time.monotonic() + 30) == ANSWER
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "https://ai/v1/chat/completions"
    assert seen[0].headers["Authorization"] == "Bearer secret"
    assert body["model"] == "chat-model" and "reasoning_effort" not in body
    with pytest.raises(ValueError):
        providers.chat(p, "s", "u", time.monotonic() + 30)


def test_yandex_request(monkeypatch):
    seen = []
    mock(
        monkeypatch, lambda request: seen.append(request) or httpx.Response(200, json=chat_answer())
    )
    [p] = providers.load(
        {"LLM_PROVIDERS": "yandex", "YANDEX_API_KEY": " secret ", "YANDEX_FOLDER_ID": "b1g"}
    )
    assert providers.chat(p, "s", "u", time.monotonic() + 30) == ANSWER
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "https://ai.api.cloud.yandex.net/v1/chat/completions"
    assert seen[0].headers["Authorization"] == "Api-Key secret"
    assert seen[0].headers["OpenAI-Project"] == "b1g"
    assert body["model"] == "gpt://b1g/yandexgpt-5.1" and body["max_tokens"] == 8000
    assert p.parallel == 1  # five keys of one cloud share its 5 generations


def test_load_from_env():
    env = {
        "LLM_PROVIDERS": "gigachat, yandex, other, gigachat",
        "GIGACHAT_CREDENTIALS": "key",
        "YANDEX_API_KEY": "k",
        "YANDEX_FOLDER_ID": "b1g",
        "YANDEX_PARALLEL": "4",
        "OTHER_BASE_URL": "https://api.example/v1",
        "OTHER_API_KEY": "d",
        "OTHER_MODEL": "chat",
    }
    giga, first, other = providers.load(env)
    assert (giga.kind, giga.parallel, giga.configured) == ("gigachat", 1, True)
    assert (first.kind, first.parallel, first.model) == ("yandex", 4, "gpt://b1g/yandexgpt-5.1")
    assert (other.kind, other.configured, other.max_tokens) == ("openai", True, 16384)
    reasoning = providers.load({"LLM_PROVIDERS": "other", "OTHER_REASONING_EFFORT": "low"})[0]
    assert providers.body(reasoning, "s", "u")["reasoning_effort"] == "low"
    assert "reasoning_effort" not in providers.body(other, "s", "u")
    assert not providers.load({"LLM_PROVIDERS": "yandex3"})[0].configured  # no key
    # the sample values of the guide left in .env are not a key
    sample = {
        "LLM_PROVIDERS": "yandex",
        "YANDEX_API_KEY": "<API-ключ 1>",
        "YANDEX_FOLDER_ID": "b1g",
    }
    assert providers.load(sample)[0].placeholder
    assert not providers.load(sample)[0].configured
    sample |= {"YANDEX_API_KEY": "AQVN1", "YANDEX_FOLDER_ID": "<ID каталога ключа 1>"}
    assert not providers.load(sample)[0].configured
    # a key without its folder cannot address a model
    assert not providers.load({"LLM_PROVIDERS": "yandex", "YANDEX_API_KEY": "k"})[0].configured
    names = [p.name for p in providers.load({})]
    assert names == ["yandex", "yandex2", "yandex3", "yandex4", "yandex5", "gigachat"]
    # Another key needs only its own line: folder, model and limits come from YANDEX_*;
    # a key of another cloud brings its own folder, a full gpt:// URI is kept as it is
    shared = env | {
        "LLM_PROVIDERS": "yandex,yandex4,yandex5",
        "YANDEX4_API_KEY": " k4 ",
        "YANDEX5_API_KEY": "k5",
        "YANDEX5_FOLDER_ID": "b1other",
        "YANDEX5_MODEL": "gpt://b1other/aliceai-llm-flash",
        "YANDEX_RPM": "30",
        "YANDEX_MAX_TOKENS": "6000",
    }
    _, fourth, fifth = providers.load(shared)
    assert (fourth.key, fourth.model, fourth.folder) == ("k4", "gpt://b1g/yandexgpt-5.1", "b1g")
    assert (fourth.parallel, fourth.rpm, fourth.max_tokens) == (4, 30, 6000)
    assert (fifth.folder, fifth.model) == ("b1other", "gpt://b1other/aliceai-llm-flash")


# --- several providers in extract_chunk ----------------------------------------------------


def fake_chat(errors=None, log=None):
    """Answers every row as a product, or raises the error set for the provider."""
    errors = errors or {}

    def chat(p, system, user, deadline, cancelled=None):
        if log is not None:
            log.append(p.name)
        if p.name in errors:
            raise errors[p.name]
        rows = json.loads(user)
        return json.dumps([{"id": r["id"], "name": r["text"].split()[0]} for r in rows])

    return chat


def test_no_provider_means_rules_with_the_reason(monkeypatch):
    monkeypatch.setattr(extractor, "PROVIDERS", [])
    assert extractor.llm_state() == "off"
    [row] = extractor.extract_chunk(["Бумага А4"], time.monotonic() + 5)
    assert row["source"] == "rules" and row["fallback"] == extractor.NO_KEY


def test_silent_provider_is_paused_and_rows_get_rules(monkeypatch):
    calls = []
    monkeypatch.setattr(extractor, "PROVIDERS", [openai()])
    monkeypatch.setattr(providers, "chat", fake_chat({"x": httpx.ConnectError("no")}, calls))
    [row] = extractor.extract_chunk(["Бумага"], time.monotonic() + 5)
    assert row["fallback"] == extractor.UNAVAILABLE and extractor.llm_state() == "down"
    [row] = extractor.extract_chunk(["Лампа"], time.monotonic() + 5)  # skips the network at once
    assert row["fallback"] == extractor.UNAVAILABLE and calls == ["x"]


def test_failed_batch_moves_to_the_next_provider(monkeypatch):
    refused = httpx.HTTPStatusError("500", request=None, response=httpx.Response(500))
    first, second = openai(name="a"), openai(name="b")
    monkeypatch.setattr(extractor, "PROVIDERS", [first, second])
    monkeypatch.setattr(providers, "chat", fake_chat({"a": refused}))
    rows = extractor.extract_chunk(["Бумага А4", "Лампа 10 Вт"], time.monotonic() + 5)
    assert [row["provider"] for row in rows] == ["b", "b"]
    assert not first.is_down()  # an HTTP error is not an outage
    monkeypatch.setattr(providers, "chat", fake_chat({"a": refused, "b": refused}))
    [row] = extractor.extract_chunk(["Бумага"], time.monotonic() + 5)
    assert row["fallback"] == extractor.ERROR  # every provider failed
    assert first.slots.acquire(blocking=False) and second.slots.acquire(blocking=False)


def test_gigachat_is_used_only_when_yandex_cannot_answer(monkeypatch):
    refused = httpx.HTTPStatusError("500", request=None, response=httpx.Response(500))
    yandex = openai(name="yandex")
    giga = Provider("gigachat", "gigachat", "https://giga/v1", "key", "GigaChat-3-Ultra")
    monkeypatch.setattr(extractor, "PROVIDERS", [giga, yandex])  # even listed first
    calls = []
    monkeypatch.setattr(providers, "chat", fake_chat(log=calls))
    [row] = extractor.extract_chunk(["Бумага"], time.monotonic() + 5)
    assert row["provider"] == "yandex" and calls == ["yandex"]
    yandex.slots.acquire()  # YandexGPT is only busy: the batch waits instead of going to GigaChat
    assert extractor.take_provider(set(), time.monotonic() + 0.2) is None
    yandex.slots.release()
    monkeypatch.setattr(providers, "chat", fake_chat({"yandex": refused}))
    [row] = extractor.extract_chunk(["Бумага"], time.monotonic() + 5)
    assert row["provider"] == "gigachat"  # YandexGPT failed: GigaChat takes over


def test_budget_end_gives_budget_reason(monkeypatch):
    monkeypatch.setattr(extractor, "PROVIDERS", [openai()])
    monkeypatch.setattr(providers, "chat", fake_chat({"x": TimeoutError("cut")}))
    [row] = extractor.extract_chunk(["Бумага"], time.monotonic() + 5)
    assert row["fallback"] == extractor.BUDGET and extractor.llm_state() == "on"


def test_providers_share_the_queue_within_their_limits(monkeypatch):
    fast, slow = openai(parallel=2, name="fast"), openai(parallel=1, name="slow")
    active = {"fast": 0, "slow": 0}
    peak = dict(active)
    lock = threading.Lock()
    inner = fake_chat()

    def chat(p, *args):
        with lock:
            active[p.name] += 1
            peak[p.name] = max(peak[p.name], active[p.name])
        time.sleep(0.03 if p.name == "fast" else 0.15)
        try:
            return inner(p, *args)
        finally:
            with lock:
                active[p.name] -= 1

    monkeypatch.setattr(extractor, "PROVIDERS", [fast, slow])
    monkeypatch.setattr(extractor, "_pool", ThreadPoolExecutor(3))  # one worker per slot
    monkeypatch.setattr(extractor, "SLOTS", 3)
    monkeypatch.setattr(providers, "chat", chat)
    texts = [f"Товар {i}" for i in range(200)]
    job = extractor.JobExtractor(texts)
    used = [job.get(text)["provider"] for text in texts]
    assert peak["fast"] <= 2 and peak["slow"] == 1
    assert used.count("fast") > used.count("slow") > 0


def test_a_freed_slot_goes_to_the_batch_that_waited_longest(monkeypatch):
    yandex = openai(name="yandex")
    monkeypatch.setattr(extractor, "PROVIDERS", [yandex])
    yandex.slots.acquire()  # a request is running
    got = {}
    waiter = threading.Thread(
        target=lambda: got.update(first=extractor.take_provider(set(), time.monotonic() + 3))
    )
    waiter.start()
    time.sleep(0.2)  # the first batch is waiting now
    yandex.slots.release()
    # A worker that has just finished a batch asks at once, before the waiter wakes up
    assert extractor.take_provider(set(), time.monotonic() + 0.3) is None
    waiter.join()
    assert got["first"] is yandex
    yandex.slots.release()


def test_the_worker_of_the_gigachat_slot_does_not_hold_a_batch_back(monkeypatch):
    """5 YandexGPT slots and a GigaChat one: 6 workers. The sixth batch used to wait until no
    other batch was left, and the rows after it reached the table only at the end."""
    yandex = openai(parallel=5, name="yandex")
    giga = Provider("gigachat", "gigachat", "https://giga/v1", "key", "GigaChat-3-Ultra")
    finished = []
    inner = fake_chat()

    def chat(p, system, user, *args):
        time.sleep(0.02)
        finished.append(json.loads(user)[0]["text"])
        return inner(p, system, user, *args)

    monkeypatch.setattr(extractor, "PROVIDERS", [yandex, giga])
    monkeypatch.setattr(extractor, "_pool", ThreadPoolExecutor(6))
    monkeypatch.setattr(extractor, "SLOTS", 6)
    monkeypatch.setattr(extractor, "BATCH_SIZE", 1)
    monkeypatch.setattr(providers, "chat", chat)
    texts = [f"Товар {i}" for i in range(60)]
    job = extractor.JobExtractor(texts)
    assert all(job.get(text)["provider"] == "yandex" for text in texts)
    assert finished.index("Товар 5") < 15  # in the second round, not the last one


def test_bad_objects_fall_back_per_row():
    answer = json.dumps([{"id": 0, "name": "Бумага", "characteristics": 42}, {"id": 1}])
    paper, lamp, cable = extractor.to_attributes(answer, ["Бумага 80 г", "Лампа", "Кабель"], "a")
    assert paper["fallback"] == lamp["fallback"] == extractor.ERROR  # bad list, no name
    assert cable["fallback"] == extractor.MISSING
    with pytest.raises(ValueError):
        extractor.to_attributes("[]", ["Бумага"], "a")


def test_a_job_keeps_only_a_few_unfinished_batches_in_the_pool(monkeypatch):
    """Another job uploaded meanwhile must not wait for every batch of a big file."""
    go = threading.Event()
    inner = fake_chat()

    def slow_chat(*args):
        go.wait(5)
        return inner(*args)

    monkeypatch.setattr(extractor, "PROVIDERS", [openai()])
    monkeypatch.setattr(extractor, "SLOTS", 1)
    monkeypatch.setattr(extractor, "AHEAD_ROUNDS", 2)
    monkeypatch.setattr(extractor, "BATCH_SIZE", 10)
    monkeypatch.setattr(providers, "chat", slow_chat)
    texts = [f"Товар {i}" for i in range(100)]  # 10 batches
    job = extractor.JobExtractor(texts)
    assert len(job._futures) == 2  # the rest waits until these finish
    go.set()
    assert [job.get(t)["source"] for t in texts] == ["llm"] * 100
    assert len(job._futures) == 10


@pytest.mark.parametrize("status", [401, 402, 403])
def test_refused_key_or_payment_pauses_the_provider(monkeypatch, status):
    refused = httpx.HTTPStatusError("x", request=None, response=httpx.Response(status))
    first, second = openai(name="a"), openai(name="b")
    monkeypatch.setattr(extractor, "PROVIDERS", [first, second])
    monkeypatch.setattr(providers, "chat", fake_chat({"a": refused}))
    [row] = extractor.extract_chunk(["Бумага"], time.monotonic() + 5)
    assert row["provider"] == "b" and first.is_down() and not second.is_down()


def test_refused_connection_is_retried_sooner_than_a_silent_network(monkeypatch):
    refused, silent = openai(name="a"), openai(name="b")
    monkeypatch.setattr(extractor, "PROVIDERS", [refused])
    monkeypatch.setattr(providers, "chat", fake_chat({"a": httpx.ConnectError("refused")}))
    extractor.extract_chunk(["Бумага"], time.monotonic() + 5)
    monkeypatch.setattr(extractor, "PROVIDERS", [silent])
    monkeypatch.setattr(providers, "chat", fake_chat({"b": httpx.ReadTimeout("no answer")}))
    extractor.extract_chunk(["Бумага"], time.monotonic() + 5)
    now = time.monotonic()
    assert refused.down_until - now <= extractor.CONNECT_PAUSE
    assert silent.down_until - now > extractor.CONNECT_PAUSE


def test_rpm_spaces_requests_and_env_sets_it():
    p = Provider("x", "openai", "http://x", "k", "m", parallel=4, rpm=600)  # 0.1 s apart
    started = time.monotonic()
    for _ in range(4):
        p.wait_turn(time.monotonic() + 10)
    assert time.monotonic() - started >= 0.29
    free = Provider("y", "openai", "http://y", "k", "m")
    started = time.monotonic()
    free.wait_turn(time.monotonic() + 10)
    assert time.monotonic() - started < 0.05
    loaded = providers.load({"LLM_PROVIDERS": "yandex", "YANDEX_RPM": "28"})
    assert loaded[0].rpm == 28


@pytest.mark.parametrize("status", [429, 503])
def test_overloaded_gateway_is_skipped_for_a_few_seconds(monkeypatch, status):
    refused = httpx.HTTPStatusError("x", request=None, response=httpx.Response(status))
    first, second = openai(name="a"), openai(name="b")
    monkeypatch.setattr(extractor, "PROVIDERS", [first, second])
    monkeypatch.setattr(providers, "chat", fake_chat({"a": refused}))
    now = time.monotonic()
    [row] = extractor.extract_chunk(["Бумага"], now + 5)
    assert row["provider"] == "b" and first.is_busy() and not first.is_down()
    assert first.busy_until - now <= extractor.GATEWAY_PAUSE + 1


def test_rows_skipped_by_the_model_are_asked_again(monkeypatch):
    sizes = []

    def chat(provider, system, user, deadline, cancelled=None):
        sizes.append(len(json.loads(user)))
        return ANSWER  # the model only ever answers for id 0

    monkeypatch.setattr(extractor, "PROVIDERS", [openai()])
    monkeypatch.setattr(providers, "chat", chat)
    rows = extractor.extract_chunk(["Бумага А4", "Бумага офисная"], time.monotonic() + 5)
    assert sizes == [2, 1]  # the skipped row went alone
    assert [row["source"] for row in rows] == ["llm", "llm"]


def test_answer_without_ids_is_read_by_position():
    answer = '[{"name": "Бумага", "search_text": "Бумага для офисной техники"}]'
    [row] = extractor.to_attributes(answer, ["Бумага А4"], "x")
    assert row["source"] == "llm" and row["name"] == "Бумага"


def test_cut_answer_splits_the_batch_instead_of_failing_over(monkeypatch):
    sizes = []

    def chat(provider, system, user, deadline, cancelled=None):
        rows = json.loads(user)
        sizes.append(len(rows))
        if len(rows) > 2:
            raise providers.AnswerCut("cut")
        return json.dumps(
            [{"id": r["id"], "name": "Бумага", "search_text": "Бумага"} for r in rows],
            ensure_ascii=False,
        )

    monkeypatch.setattr(extractor, "PROVIDERS", [openai(name="a"), openai(name="b")])
    monkeypatch.setattr(providers, "chat", chat)
    texts = ["Бумага А4", "Бумага офисная", "Бумага для печати", "Бумага цветная"]
    rows = extractor.extract_chunk(texts, time.monotonic() + 5)
    assert sizes == [4, 2, 2]  # one try with the whole batch, no second provider for it
    assert [row["source"] for row in rows] == ["llm"] * 4
