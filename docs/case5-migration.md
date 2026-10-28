# Переход PostgreSQL 16 → PostgreSQL 16 с pgvector

Не подключайте прежний том Alpine PostgreSQL к другому образу вслепую.
Новый Compose использует отдельный `pgvector_data`, сохраняя `pgdata` для возврата.
Перед первым запуском нового стека на существующей установке:

1. Дождитесь завершения заданий и остановите API: `docker compose stop backend`.
2. На ещё работающей старой базе создайте dump (ниже имена по умолчанию):
   `docker compose exec db pg_dump -U ktru -d ktru -Fc -f /tmp/backup.dump`.
3. Скопируйте его: `docker compose cp db:/tmp/backup.dump ./backup.dump`.
4. Проверьте восстановление на отдельной базе. В поставленном изменении это сделано.
5. Запустите только новые DB/Redis: `docker compose up -d --wait db redis`.
6. Убедитесь, что новый том пустой. Не восстанавливайте поверх действующей базы.
7. `docker compose cp ./backup.dump db:/tmp/backup.dump`.
8. `docker compose exec db pg_restore -U ktru -d ktru --exit-on-error --single-transaction /tmp/backup.dump`.
9. `docker compose up -d --build`.

Если POSTGRES_USER/POSTGRES_DB изменены в .env, подставьте соответствующие значения.
Миграция 0002 добавит расширение vector и отдельную таблицу, не удаляя старые данные.
После готовности API импортирует каталог в pgvector, worker загружает свою копию
метаданных и начинает принимать задачи.

Сравните количество и содержимое старых заданий/позиций, выполните приёмку.
Откат выполняется старым кодом и прежней конфигурацией с томом pgdata. Новые данные,
созданные после перехода, перед откатом сохраните отдельным dump.

Резервные копии этой установки: outputs/case5/before-case5.dump и
outputs/case5/before-case5-final.dump. Они исключены из Git и содержат пользовательские данные.
