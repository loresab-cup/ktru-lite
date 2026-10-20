"""Check the configured neural networks with one short request each; keys are never printed.

docker compose exec backend python check_llm.py            # every provider in LLM_PROVIDERS
docker compose exec backend python check_llm.py yandex     # one of them
"""

import sys
import time

import httpx

import extractor
import providers

HINTS = {
    401: "неверный ключ",
    403: "нет прав: сервисному аккаунту Yandex нужна роль ai.languageModels.user",
    404: "неверный адрес, folder_id или имя модели",
    429: "превышена квота одновременных запросов",
}
ROW = "Бумага офисная А4 80 г/м2"


def check(p: providers.Provider) -> bool:
    if not p.configured:
        print(f"{p.name}: не настроен — заполните ключ (и folder_id для Yandex) в .env")
        return False
    started = time.monotonic()
    try:
        user = f'[{{"id": 0, "text": "{ROW}"}}]'
        answer = providers.chat(p, extractor.SYSTEM_PROMPT, user, time.monotonic() + 60)
        [row] = extractor.to_attributes(answer, [ROW], p.name)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        print(f"{p.name}: ошибка HTTP {code} — {HINTS.get(code, 'см. ответ сервиса')}")
        return False
    except Exception as exc:  # noqa: BLE001 - every failure is reported, not raised
        print(f"{p.name}: ошибка {type(exc).__name__}: {str(exc)[:200]}")
        return False
    print(
        f"{p.name}: OK за {time.monotonic() - started:.1f} с, модель {p.model}, "
        f"потоков {p.parallel}, наименование «{row['name']}»"
    )
    return True


if __name__ == "__main__":
    wanted = {name.lower() for name in sys.argv[1:]}
    found = [p for p in providers.load() if not wanted or p.name in wanted]
    if not found:
        print("Таких провайдеров нет в LLM_PROVIDERS")
    results = [check(p) for p in found]  # every provider is checked, not up to the first failure
    sys.exit(0 if found and all(results) else 1)
