"""Neural networks for extractor.py: settings from .env and one chat request with retries.

LLM_PROVIDERS=yandex,yandex2,yandex3,yandex4,yandex5 lists them; the order is the priority
when several are free.
- yandex, yandex2...yandex5: YandexGPT in Yandex AI Studio, one API key of a service account each:
  X_API_KEY, X_FOLDER_ID, X_MODEL (yandexgpt-5.1 or a full gpt://... URI), X_PARALLEL, X_RPM,
  X_BASE_URL; yandex2...yandex5 take what they lack from YANDEX_*
Other names are skipped: no other neural network is used. Optional X_MAX_TOKENS for the answer
limit.
Every provider has its own limit of requests at the same moment (X_PARALLEL -> a semaphore)
and, if X_RPM is set, of requests per minute.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# A provider without a key is skipped
DEFAULT_PROVIDERS = "yandex,yandex2,yandex3,yandex4,yandex5"
YANDEX_URL = "https://ai.api.cloud.yandex.net/v1"  # the OpenAI-compatible API of AI Studio
YANDEX_MODEL = "yandexgpt-5.1"  # YandexGPT Pro 5.1; Lite 5 is withdrawn on 15.11.2026
# A cloud runs 5 generations at the same moment for all its keys together (measured 27.10.2026:
# the 6th gets 429, quota ai.textGenerationCompletionSessionsCount). «auto» shares them between
# the keys of one folder (a folder lies in one cloud); keys of other clouds bring 5 more each.
# Measured 29.10.2026: four folders of four clouds, 5 requests each at once, 20 answers and no 429;
# the 6th and 7th of one folder got 429. Two folders of one cloud need numbers set by hand.
YANDEX_PARALLEL = "auto"
CLOUD_GENERATIONS = 5
# YandexGPT has a 32K context for the question and the answer together: 8K answer tokens are
# still twice what 20 rows need, and the prompt with the rows keeps the rest
YANDEX_MAX_TOKENS = "8000"
MAX_TOKENS = 16384  # a batch of 20 rows answers in 2-4 thousand tokens: room for long rows
TEMPERATURE = 0.0001  # Keep the existing extraction settings.
REQUEST_TIMEOUT = 40.0  # a batch of 10 rows takes 5-23 s; a hung request goes to a retry sooner
RETRIES = 2
# A 5xx that came this late is the gateway's own timeout (a gateway: 502 after 37 s): a retry would
# cost the same again, so the batch goes to the next provider at once
SLOW_ERROR = 15.0


class AnswerCut(ValueError):
    """The answer hit max_tokens: another provider would cut the same batch, so it is halved."""


class Cancelled(Exception):
    """The job was stopped by the user: its rows are not needed any more."""


@dataclass
class Provider:
    name: str
    kind: str  # yandex
    base_url: str
    key: str
    model: str
    parallel: int = 1
    rpm: int = 0  # requests per minute the plan allows (free tiers: ~30); 0 - no limit
    folder: str = ""  # Yandex Cloud folder of the key: sent as OpenAI-Project
    max_tokens: int = MAX_TOKENS
    reasoning: str = ""  # optional reasoning_effort for models hosted by Yandex AI Studio
    auto_parallel: bool = False  # X_PARALLEL=auto: set by share_cloud_slots
    down_until: float = 0.0  # did not answer a moment ago: skipped until then
    busy_until: float = 0.0  # overloaded (429, 502-504): the batches wait for others meanwhile
    slots: threading.BoundedSemaphore = field(init=False, repr=False)
    next_start: float = field(default=0.0, init=False, repr=False)
    turn: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.parallel = max(1, self.parallel)
        self.slots = threading.BoundedSemaphore(self.parallel)

    def wait_turn(self, deadline: float) -> None:
        """Spaces the requests by 60 / rpm seconds, so the plan's limit never answers 429.

        Never sleeps past the job budget: the request itself then reports the end of it."""
        if self.rpm <= 0:
            return
        with self.turn:
            now = time.monotonic()
            start = max(now, self.next_start)
            self.next_start = start + 60.0 / self.rpm
        time.sleep(max(0.0, min(start, deadline) - now))

    @property
    def configured(self) -> bool:
        return bool(self.key and self.model) and not self.placeholder

    @property
    def placeholder(self) -> bool:
        """A sample value left in .env («<API-ключ 1>»): keys and folder IDs are plain ASCII."""
        return any(not v.isascii() or "<" in v for v in (self.key, self.model, self.folder))

    def is_down(self) -> bool:
        return time.monotonic() < self.down_until

    def is_busy(self) -> bool:
        return time.monotonic() < self.busy_until


def load(env=os.environ) -> list[Provider]:
    """Providers from LLM_PROVIDERS, in that order."""
    result = []
    for name in dict.fromkeys(
        n.strip().lower() for n in env.get("LLM_PROVIDERS", DEFAULT_PROVIDERS).split(",")
    ):
        if not name:
            continue
        if name == "yandex" or (name.startswith("yandex") and name[6:].isdigit()):
            result.append(yandex(env, name))
        else:
            logger.warning("LLM_PROVIDERS: %s is not a Yandex provider, skipped", name)
            continue
        result[-1].max_tokens = int(setting(env, name, "MAX_TOKENS", YANDEX_MAX_TOKENS))
        result[-1].reasoning = setting(env, name, "REASONING_EFFORT").strip()
        result[-1].rpm = int(setting(env, name, "RPM", "0") or 0)
    share_cloud_slots(result)
    return result


def share_cloud_slots(found: list[Provider]) -> None:
    """X_PARALLEL=auto: the CLOUD_GENERATIONS of a folder go to its auto keys, the first ones
    get the rest of the division (two keys of a folder: 3 and 2)."""
    by_folder: dict[str, list[Provider]] = {}
    for p in found:
        if p.configured:
            by_folder.setdefault(p.folder, []).append(p)
    for keys in by_folder.values():
        automatic = [p for p in keys if p.auto_parallel]
        if not automatic:
            continue  # Explicit limits may describe an increased cloud quota.
        remaining = CLOUD_GENERATIONS - sum(p.parallel for p in keys if not p.auto_parallel)
        if remaining < len(automatic):
            raise ValueError(
                "YANDEX_PARALLEL: explicit slots leave no room for every auto key; "
                "set all limits explicitly or reduce the explicit slots"
            )
        share, rest = divmod(remaining, len(automatic))
        for n, p in enumerate(automatic):
            p.parallel = max(1, share + (n < rest))
            p.slots = threading.BoundedSemaphore(p.parallel)


def yandex(env, name: str) -> Provider:
    """YandexGPT: the model is addressed as gpt://<folder>/<model>, the folder also goes in the
    OpenAI-Project header. A key without its folder is not configured."""
    folder = setting(env, name, "FOLDER_ID").strip()
    model = setting(env, name, "MODEL", YANDEX_MODEL).strip()
    if folder and not model.startswith("gpt://"):
        model = f"gpt://{folder}/{model}"
    parallel = setting(env, name, "PARALLEL", YANDEX_PARALLEL).strip().lower()
    return Provider(
        name,
        "yandex",
        setting(env, name, "BASE_URL", YANDEX_URL),
        setting(env, name, "API_KEY", shared=False).strip(),
        model if folder else "",
        int(parallel) if parallel.isdigit() else 1,
        folder=folder,
        auto_parallel=not parallel.isdigit(),  # «auto» or a typo: the safe share
    )


def setting(env, name: str, key: str, default: str = "", shared: bool = True) -> str:
    """X_KEY; a numbered key (yandex2...yandex5) takes a missing one from YANDEX_KEY, so another
    key needs only its own X_API_KEY line (and X_FOLDER_ID when it is from another cloud)."""
    value = env.get(f"{name.upper()}_{key}") or ""
    base = name.rstrip("0123456789")
    if not value and shared and base != name:
        value = env.get(f"{base.upper()}_{key}") or ""
    return value or default


_client = httpx.Client(timeout=REQUEST_TIMEOUT)


def body(p: Provider, system: str, user: str) -> dict:
    model = p.model
    result = {
        "model": model,
        "max_tokens": p.max_tokens,
        "temperature": TEMPERATURE,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if p.reasoning:
        result["reasoning_effort"] = p.reasoning
    return result


def post(p: Provider, payload: dict, deadline: float) -> httpx.Response:
    """One POST /chat/completions; a timeout cut by the job budget becomes TimeoutError."""
    headers = {"Authorization": f"Api-Key {p.key}", "OpenAI-Project": p.folder}
    p.wait_turn(deadline)
    timeout = min(REQUEST_TIMEOUT, max(1.0, deadline - time.monotonic()))
    started = time.monotonic()
    try:
        response = _client.post(
            f"{p.base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers=headers | {"Accept": "application/json"},
            timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        if timeout < REQUEST_TIMEOUT:  # cut by the job budget: the network itself may be fine
            raise TimeoutError("LLM budget is over: the request was cut") from exc
        raise
    logger.info(
        "LLM %s: HTTP %s in %.1f s", p.name, response.status_code, time.monotonic() - started
    )
    return response


def chat(p: Provider, system: str, user: str, deadline: float, cancelled=None) -> str:
    """Answer text; retries 429, 5xx and a silent network while the budget lasts.

    TimeoutError - the budget ended; httpx.TransportError - the network kept silent;
    httpx.HTTPStatusError or ValueError - the provider refused or answered incompletely."""
    payload = body(p, system, user)
    silent = None
    for attempt in range(RETRIES + 1):
        if cancelled is not None and cancelled.is_set():
            raise Cancelled  # stopped: send nothing more
        if deadline - time.monotonic() < 1:
            # The budget ran out while the network kept silent: that is "no answer", not "slow"
            raise silent or TimeoutError("LLM budget is over")
        started = time.monotonic()
        try:
            response = post(p, payload, deadline)
        except httpx.TimeoutException as exc:  # a full window without an answer
            if attempt == RETRIES:
                raise
            silent = exc
            continue
        except TimeoutError:
            if silent:
                raise silent from None  # the network had kept silent before: unavailable
            raise
        if response.status_code >= 500 and time.monotonic() - started >= SLOW_ERROR:
            # The gateway itself waited in vain: the same as no answer, the provider is paused
            raise httpx.ReadTimeout(f"HTTP {response.status_code} after a long wait")
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == RETRIES:
                response.raise_for_status()
            time.sleep(min(2.0 * (attempt + 1), max(0.0, deadline - time.monotonic())))
            continue
        response.raise_for_status()
        choice = (response.json().get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise AnswerCut("the answer was cut by max_tokens")
        return choice.get("message", {}).get("content") or ""
    raise RuntimeError("unreachable")
