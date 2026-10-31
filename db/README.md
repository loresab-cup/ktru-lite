# Каталог КТРУ

Приложению нужны два файла из [релиза catalog_yandex](https://github.com/loresab-cup/ktru-lite/releases/tag/catalog_yandex):

- [ktru_catalog.jsonl.gz](https://github.com/loresab-cup/ktru-lite/releases/download/catalog_yandex/ktru_catalog.jsonl.gz), около 344 МБ.
- [ktru_vectors_yandex.npz](https://github.com/loresab-cup/ktru-lite/releases/download/catalog_yandex/ktru_vectors_yandex.npz), около 90 МБ.

При запуске Docker Compose сервис catalog скачивает отсутствующие файлы в эту
папку и сверяет контрольные суммы. Их также можно скачать вручную и положить сюда
с указанными именами. Генерировать каталог и векторы заново не нужно.

Большие файлы распространяются через релиз и не хранятся в истории Git.
