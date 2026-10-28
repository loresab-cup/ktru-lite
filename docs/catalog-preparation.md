# Каталог и векторы для поставки

В архив проекта для организаторов положите оба файла в `db/`. Git их намеренно
не хранит. Пустой clone без этих файлов не является готовой поставкой.

## 1. Каталог

`ktru_catalog.jsonl.gz` — исходный сжатый JSONL-дамп каталога команды, а не входная
спецификация товаров. Используйте согласованную версию целиком. Источника для
повторной выгрузки всего ЕИС в этом репозитории нет; у получателя должен быть
сам файл. Не переименовывайте XLSX/CSV в этот формат.

## 2. Векторы

Предпочтительно передать готовый `ktru_vectors_yandex.npz` вместе с тем же дампом.
NPZ содержит `codes` и `vectors`: для каждого кода — 256 чисел модели Yandex
text-search-doc. При запуске проверяются размерность, коды, конечность значений
и ненулевые векторы; затем они нормализуются и импортируются в pgvector.
Подмена моделью другой размерности или Yandex query-векторами недопустима.

Если готового NPZ нет, `scripts/embed_catalog.py` создаёт его через платный API.
Нужны `YANDEX_EMBED_API_KEY` и `YANDEX_EMBED_FOLDER_ID` в `.env`. Сначала соберите
backend-образ, затем выполните из корня проекта:

```sh
docker compose build backend
docker compose run --rm --no-deps --entrypoint python -v ./scripts:/scripts:ro backend /scripts/embed_catalog.py /catalog/ktru_catalog.jsonl.gz /cache/ktru_vectors_yandex.npz
docker compose create backend
docker compose cp backend:/cache/ktru_vectors_yandex.npz db/ktru_vectors_yandex.npz
```

Это длительная отдельная подготовка, она не входит в лимит обработки 1000 строк.
Скрипт сохраняет `.partial.npy` в cache для продолжения с тем же дампом; при смене
дампа используйте другое имя выходного файла, чтобы не продолжить чужую заготовку.

## Проверка поставки

Сравните SHA-256 обоих файлов с переданной командой ведомостью, например в PowerShell:

```powershell
Get-FileHash db/ktru_catalog.jsonl.gz, db/ktru_vectors_yandex.npz -Algorithm SHA256
```

Запустите Compose, дождитесь `catalog: ready`, `vector_search: pgvector` в
`/api/health`, затем выполните приёмку из README. В проверенной версии — 81 509 кодов.
