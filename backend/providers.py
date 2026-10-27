"""Neural networks for extractor.py: settings from .env and one chat request with retries.

LLM_PROVIDERS=yandex,yandex2,yandex3,yandex4,yandex5,gigachat lists them; the order is the priority
when several are free. GigaChat is used only when none of the others can take the batch.
- yandex, yandex2...yandex5: YandexGPT in Yandex AI Studio, one API key of a service account each:
  X_API_KEY, X_FOLDER_ID, X_MODEL (yandexgpt-5.1 or a full gpt://... URI), X_PARALLEL, X_RPM,
  X_BASE_URL; yandex2...yandex5 take what they lack from YANDEX_*
- gigachat: GIGACHAT_* (OAuth key -> token for 30 minutes)
- any other name X: an OpenAI-compatible API with X_BASE_URL, X_API_KEY, X_MODEL, X_PARALLEL, X_RPM
Optional X_REASONING_EFFORT (none, low, ...) for models that think before answering and
X_MAX_TOKENS for the answer limit.
Every provider has its own limit of requests at the same moment (X_PARALLEL -> a semaphore)
and, if X_RPM is set, of requests per minute.
"""

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# A provider without a key is skipped
DEFAULT_PROVIDERS = "yandex,yandex2,yandex3,yandex4,yandex5,gigachat"
YANDEX_URL = "https://ai.api.cloud.yandex.net/v1"  # the OpenAI-compatible API of AI Studio
YANDEX_MODEL = "yandexgpt-5.1"  # YandexGPT Pro 5.1; Lite 5 is withdrawn on 15.11.2026
# A cloud runs 5 generations at the same moment for all its keys together (measured 27.10.2026:
# the 6th gets 429, quota ai.textGenerationCompletionSessionsCount), so five keys of one cloud
# take one request each; keys of other clouds bring 5 more each
YANDEX_PARALLEL = "1"
# YandexGPT has a 32K context for the question and the answer together: 8K answer tokens are
# still twice what 20 rows need, and the prompt with the rows keeps the rest
YANDEX_MAX_TOKENS = "8000"
MAX_TOKENS = 16384  # a batch of 20 rows answers in 2-4 thousand tokens: room for long rows
TEMPERATURE = 0.0001  # below 0.001 GigaChat answers the same way every time
REQUEST_TIMEOUT = 40.0  # a batch of 10 rows takes 5-23 s; a hung request goes to a retry sooner
RETRIES = 2
# A 5xx that came this late is the gateway's own timeout (a gateway: 502 after 37 s): a retry would
# cost the same again, so the batch goes to the next provider at once
SLOW_ERROR = 15.0
TOKEN_MARGIN = 60.0  # seconds: a GigaChat token that expires sooner is replaced first

# GigaChat certificates are signed by the Russian root CA (Минцифры), missing from the usual
# trust stores. "false" turns the check off (as in the Sber example) - traffic with the key can
# then be intercepted by a forged certificate; a path to that CA bundle keeps the check on.
_VERIFY = os.getenv("GIGACHAT_VERIFY_SSL", "true")
GIGACHAT_VERIFY: bool | str = (
    _VERIFY if _VERIFY.endswith((".pem", ".crt")) else (_VERIFY.lower() not in ("0", "false", "no"))
)
AUTH_URL = os.getenv("GIGACHAT_AUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")


class AnswerCut(ValueError):
    """The answer hit max_tokens: another provider would cut the same batch, so it is halved."""


class Cancelled(Exception):
    """The job was stopped by the user: its rows are not needed any more."""


@dataclass
class Provider:
    name: str
    kind: str  # gigachat | yandex | openai
    base_url: str
    key: str
    model: str
    parallel: int = 1
    reasoning: str = (
        ""  # reasoning_effort for models that think before answering: "none" is fastest
    )
    rpm: int = 0  # requests per minute the plan allows (free tiers: ~30); 0 - no limit
    folder: str = ""  # Yandex Cloud folder of the key: sent as OpenAI-Project
    max_tokens: int = MAX_TOKENS
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
        p = name.upper()
        if name == "gigachat":
            result.append(
                Provider(
                    name,
                    "gigachat",
                    env.get("GIGACHAT_BASE_URL", "https://api.giga.chat/v1"),
                    env.get("GIGACHAT_CREDENTIALS", ""),
                    env.get("GIGACHAT_MODEL", "GigaChat-3-Ultra"),
                    int(env.get("GIGACHAT_PARALLEL", "1")),
                )
            )
        elif name.startswith("yandex"):
            result.append(yandex(env, name))
        else:
            result.append(
                Provider(
                    name,
                    "openai",
                    setting(env, name, "BASE_URL"),
                    setting(env, name, "API_KEY", shared=False).strip(),
                    setting(env, name, "MODEL").strip(),
                    int(setting(env, name, "PARALLEL", "1")),
                )
            )
        result[-1].reasoning = env.get(f"{p}_REASONING_EFFORT", "").strip()
        limit = YANDEX_MAX_TOKENS if result[-1].kind == "yandex" else str(MAX_TOKENS)
        result[-1].max_tokens = int(setting(env, name, "MAX_TOKENS", limit))
        result[-1].rpm = int(setting(env, name, "RPM", "0", shared=name != "gigachat") or 0)
    return result


def yandex(env, name: str) -> Provider:
    """YandexGPT: the model is addressed as gpt://<folder>/<model>, the folder also goes in the
    OpenAI-Project header. A key without its folder is not configured."""
    folder = setting(env, name, "FOLDER_ID").strip()
    model = setting(env, name, "MODEL", YANDEX_MODEL).strip()
    if folder and not model.startswith("gpt://"):
        model = f"gpt://{folder}/{model}"
    return Provider(
        name,
        "yandex",
        setting(env, name, "BASE_URL", YANDEX_URL),
        setting(env, name, "API_KEY", shared=False).strip(),
        model if folder else "",
        int(setting(env, name, "PARALLEL", YANDEX_PARALLEL)),
        folder=folder,
    )


def setting(env, name: str, key: str, default: str = "", shared: bool = True) -> str:
    """X_KEY; a numbered key (yandex2...yandex5) takes a missing one from YANDEX_KEY, so another
    key needs only its own X_API_KEY line (and X_FOLDER_ID when it is from another cloud)."""
    value = env.get(f"{name.upper()}_{key}") or ""
    base = name.rstrip("0123456789")
    if not value and shared and base != name:
        value = env.get(f"{base.upper()}_{key}") or ""
    return value or default


# --- GigaChat token ------------------------------------------------------------------------

_gigachat_client = httpx.Client(timeout=REQUEST_TIMEOUT, verify=GIGACHAT_VERIFY)
_client = httpx.Client(timeout=REQUEST_TIMEOUT)
_token = {"value": "", "expires": 0.0}  # GigaChat access token and its end, unix seconds
_token_lock = threading.Lock()


def access_token(key: str, renew: bool = False, deadline: float | None = None) -> str:
    """GigaChat access token: cached for its 30 minutes, renewed a minute before the end.

    Waiting for the lock and the OAuth request itself never outlive the job budget."""
    left = 30.0 if deadline is None else max(0.0, deadline - time.monotonic())
    if not _token_lock.acquire(timeout=left):
        raise TimeoutError("LLM budget is over while waiting for OAuth")
    try:
        left = 30.0 if deadline is None else deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("LLM budget is over before OAuth")
        if renew or time.time() > _token["expires"] - TOKEN_MARGIN:
            response = _gigachat_client.post(
                AUTH_URL,
                headers={
                    "Authorization": f"Basic {key}",
                    "RqUID": str(uuid.uuid4()),
                    "Accept": "application/json",
                },
                data={"scope": SCOPE},
                timeout=min(30.0, left),
            )
            response.raise_for_status()
            body = response.json()
            _token["value"] = body["access_token"]
            _token["expires"] = body["expires_at"] / 1000  # milliseconds in the answer
        return _token["value"]
    except httpx.TimeoutException as exc:
        if left < 30.0:  # cut by the job budget: the network itself may be fine
            raise TimeoutError("LLM budget is over during OAuth") from exc
        raise
    finally:
        _token_lock.release()


# --- one request ---------------------------------------------------------------------------


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
    if p.kind == "gigachat":
        headers = {"Authorization": f"Bearer {access_token(p.key, deadline=deadline)}"}
        client = _gigachat_client
    elif p.kind == "yandex":
        headers = {"Authorization": f"Api-Key {p.key}", "OpenAI-Project": p.folder}
        client = _client
    else:
        headers = {"Authorization": f"Bearer {p.key}"}
        client = _client
    p.wait_turn(deadline)
    timeout = min(REQUEST_TIMEOUT, max(1.0, deadline - time.monotonic()))
    started = time.monotonic()
    try:
        response = client.post(
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
    renewed = False
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
        if response.status_code == 401 and p.kind == "gigachat" and not renewed:
            access_token(p.key, renew=True, deadline=deadline)  # revoked or ended early
            renewed = True
            continue
        response.raise_for_status()
        choice = (response.json().get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise AnswerCut("the answer was cut by max_tokens")
        return choice.get("message", {}).get("content") or ""
    raise RuntimeError("unreachable")
