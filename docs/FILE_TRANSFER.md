# Межустройственная передача файла через ИРУ

## Контракт

`transfer_file(source_device_id, source_path, target_device_id, target_path OR target_directory)` — один tool action в обычном режиме и PLAN. Владельца задаёт task_runtime из авторизованного запроса. Модель передаёт только имена устройств и пути, не user_id, токены или байты. Устройства указываются точными короткими ID. Неизвестное, чужое или offline устройство не заменяется другим.

Лимит — **500 MiB = 524288000 байт**. Один regular file, не directory. Для папки сначала создайте ZIP. `target_directory="desktop"` (также значение по умолчанию) разрешается агентом target через существующий `get_desktop_path`, включая OneDrive. Bare source filename ищется на Desktop source; остальные source paths должны быть абсолютными. Целевые каталоги должны уже существовать. Нельзя одновременно задавать target_path и target_directory.

## Execution path

LLM → controller tool schema → существующий send callback → общий `server/file_transfer.py` → owner-scoped WebSocket команды → `agent/core/file_transfer.py` → локальная ФС.

1. Сервер проверяет обе записи `devices[user_id:device_id]`, owner, online и device-specific path guard. Ни user_id, ни ownership из agent metadata не принимаются.
2. Source агент проверяет regular file, размер, filesystem guards, считает SHA-256 блоками 1 MiB.
3. Target агент разрешает свой Desktop/путь и проверяет отсутствие файла.
4. Source делает потоковый PUT на свой настроенный IRU origin. Сервер пишет блоками, независимо ограничивает фактический размер и проверяет SHA против source metadata.
5. Target делает потоковый GET в уникальный `.iru-*.iru-part` рядом с конечным файлом, проверяет размер и SHA, выполняет fsync.
6. Target запрашивает одноразовое разрешение `/publish`. Failed/cancelled/expired relay не разрешит публикацию. Windows `os.rename` без замены; POSIX `os.link` + unlink временного имени обеспечивают atomic no-clobber publication. Существующий target, в том числе появившийся после preflight, вызывает `target_exists`.
7. Target возвращает по WebSocket path, size, sha256 и verified. Только совпадение source/server/target и ожидаемого пути переводит transfer в completed. Upload или завершённый HTTP GET сами по себе не являются успехом.
8. Сервер удаляет временный файл; controller получает только metadata результата. Partial download удаляет `.iru-part` в finally. При аварийном завершении процесса агента скрытый `.iru-part` может остаться, но конечное имя не публикуется.

## Transport и безопасность

HTTP endpoints:
- `PUT /api/transfers/{random_id}/content` — upload;
- `GET /api/transfers/{random_id}/content` — download;
- `POST /api/transfers/{random_id}/publish` — разрешение atomic publication.

Каждый требует отдельный криптографически случайный 256-bit bearer в Authorization. Tokens выдаются только конкретным агентам через owner-scoped WebSocket и не попадают в controller journal/history. В SQLite хранятся только SHA-256 токенов. Они одноразовые, привязаны к transfer и фазе, отзываются при завершении/ошибке/отмене, живут не дольше TTL. Перед каждой фазой повторно проверяются оба owner-scoped подключения. ID сам по себе не авторизует доступ. Путь и credentials не передаются в query string. Агент не принимает произвольный upload URL: используется только настроенный server_url, без redirects. В production требуется HTTPS/WSS; HTTP разрешён только для loopback-тестов.

Source/target paths проверяются по отдельным контекстам устройств. Агент дополнительно разрешает путь локально и запрещает другой Windows-профиль, системные пути, UNC и ADS. Применяются права ОС. Локальный пользователь, изменяющий ФС одновременно с передачей, находится вне модели изоляции между аккаунтами ИРУ; файл может измениться после завершённой проверки, как и любой пользовательский файл.

## Storage, limits, recovery

Metadata: таблица `file_transfers` в существующей БД `IRU_DB_PATH`. Байты — `IRU_TRANSFER_DIR/<random_id>`; по умолчанию `transfers/` рядом с БД. Для production задайте абсолютный `IRU_TRANSFER_DIR=/var/lib/iru/transfers` и права записи только сервисному пользователю. Каталог не должен находиться под static/public web root; не включайте его в резервную копию как пользовательское хранилище.

TTL **3600 секунд**, cleanup раз в 60 секунд и при старте. После успеха/ошибки немедленная попытка удаления, при занятом файле cleanup повторяет её. После рестарта активные операции становятся failed, credentials отзываются, bytes удаляются. Metadata tombstones хранятся ещё до 7 дней для idempotency и диагностики; bytes не сохраняются на этот срок.

Максимум 2 активные передачи одного пользователя / 8 на сервере. До upload проверяется свободное место (file size + 64 MiB); ENOSPC во время записи завершает операцию ошибкой. Concurrent операции могут исчерпать диск после preflight, это не считается успехом.

Память не пропорциональна размеру файла: agent read/send/read/write по 1 MiB, server ASGI streaming + bounded disk writes/reads. Нет request.body(), BLOB, Base64 или буферизации файла целиком. Reverse proxy должен пропускать 500 MiB без request buffering и слишком коротких таймаутов; штатный Caddy reverse_proxy передаёт поток. Если используется nginx, отдельно проверьте его body-size/buffering/timeouts.

Idempotency key вычисляется сервером из task/run ID и аргументов. Повтор идентичного tool call в том же run возвращает сохранённый результат или transfer_in_progress, без нового upload. Новый пользовательский запрос — новая операция, но существующий target не перезаписывается. Автоматического retry нет. HTTP socket idle timeout 30 секунд, agent upload/download deadline 1500 секунд на фазу, WebSocket ожидание 1560 секунд, общий предел TTL. Cancel опрашивается во время ожидания; credentials отзываются и следующий publish запрещается. Узкая гонка cancel после разрешения publish может оставить целостный конечный файл, но не ложное подтверждение успеха. Потерянный финальный ack означает неопределённость: файл мог быть опубликован, результат остаётся failed, повтор не перезаписывает его.

Логи содержат transfer ID, owner, source/target IDs, size, status, duration, stage и reason. Внутренние agent transfer params редактируются целиком; tokens, file content и chunks не логируются.

## PLAN и обычный режим

Общая реализация в runtime, одна операция для LLM. Planner выделяет передачу отдельным шагом и может задать `completion_check={"tool":"transfer_file","target_device_id":"Second"}`. Подтверждённый transfer немедленно завершает такой шаг. Handoff сохраняет target_path и target device. Любая transfer failure завершает worker и блокирует оставшиеся шаги, даже если старая recovery-эвристика считает ошибку восстановимой. Проверка другого артефакта не может перевести failed transfer в recovered. В обычном режиме failure также возвращается явно.

Существующий `/api/download` не изменён: это другой механизм маленьких пользовательских download links, непригодный для relay 500 MiB. UI file manager, public links, P2P, resume, каталоги, облачный диск и авто-перенос артефактов не добавлены.

## Deployment и ручная проверка

Обновить сервер **и пересобрать/установить агент на обоих устройствах**. Старый агент не знает file.transfer_* и вернёт ошибку; режим совместимости через shell/base64 отсутствует. Сохраняется существующая single-process модель сервера с подключениями в памяти; несколько uvicorn workers для MVP не поддерживаются.

Ветка реализации: `codex/file-transfer`, основа `99ddca437fda5f4d4f1d2f148adb9f4a4565fac2`. После публикации ветки обновление:

```bash
cd /opt/iru/app &&
git fetch origin &&
git switch codex/file-transfer &&
git merge --ff-only origin/codex/file-transfer &&
systemctl restart iru
```

Не применять stash/restore к локальным release metadata без проверки. Не использовать tools/update_server.py для этой ветки: его текущий allowlist не включает её.

Безопасный сценарий (уникальные имена, без удаления чужих файлов):
1. Создать на Desktop givi небольшой `iru-relay-test-<unique>.txt`.
2. Запросить «Передай файл … с givi на Second на рабочий стол».
3. На обоих устройствах выполнить `Get-FileHash -Algorithm SHA256 -LiteralPath '<свой фактический путь>'`, сравнить SHA и размер; проверить содержимое target.
4. Повторить с небольшим ZIP/PNG. Проверить байты/SHA.
5. Повторить передачу новым запросом: target_exists, исходная копия не меняется.
6. Отключить Second и запросить другое уникальное имя: отказ до upload.
7. PLAN: создать на givi → передать → открыть на Second. При отказе transfer третий шаг не запускается.

Автотесты: `tests/test_file_transfer.py`, `tests/test_transfer_controller.py`. Реальный loopback HTTP-поток проверяет agent actions и server, но не заменяет проверку упакованных агентов на двух физических ПК. Тест 500 MiB использует повторяемые chunks и sink без дискового файла, контролирует Python peak memory через tracemalloc, не измеряет полный RSS ОС.
