# Изоляция пользователей и устройств

Профиль устройства идентифицируется парой `(user_id, device_id)`. Все внутренние операции чтения, обновления и удаления требуют `user_id`; глобальный поиск по одному `device_id` запрещён. Регистрация использует владельца из авторизации WebSocket, а не из payload. Online registry сохраняет ключ `user_id:device_id`; отправка команды повторно проверяет владельца. Кэш Python runtime также разделён по владельцам.

При первом запуске SQLite автоматически заменяет старую глобальную уникальность `device_id` на `UNIQUE(user_id, device_id)`. Миграция транзакционная, повторяемая, сохраняет существующие строки, их ID, metadata и индексы. Перед развёртыванием нужна резервная копия действующей БД через SQLite backup API. Уже перезаписанные старой версией профили восстановить из миграции невозможно; их агенты должны зарегистрироваться заново.

## Broadcast

Одна задача запускается последовательно отдельным обычным worker на каждом выбранном устройстве. Команды первого ПК не воспроизводятся на других. Worker видит только своё устройство; callback отправки команд, device tools и выдачи ссылок ограничен этим target. Подтверждение ожидается в том же worker; отказ или cancel останавливает дальнейшее выполнение.

Для broadcast история чата не передаётся: она может содержать абсолютные пути и результаты другого ПК. Поэтому запрос должен быть самостоятельным, без «повтори там прошлую команду». Текущий пользовательский запрос и собственный профиль/память устройства сохраняются. Обычный одиночный режим и история PLAN не изменены.

Ответ задачи содержит `results` по каждому устройству и `overall_status`: `success`, `partial_failure` или `failed`. При частичном/полном сбое существующий terminal `status` равен `failed`, а не `done`. Для raw-command API broadcast общий `status` равен `error` при любом сбое. Отчёт содержит статусы каждого ПК. Если обычный worker сообщил ошибку инструмента без явного подтверждения восстановления, broadcast консервативно считает это сбоем.

## PLAN

Указанный явно недоступный, чужой или некорректный target не заменяется текущим устройством. Шаг завершается ошибкой `target_device_not_found`, следующие шаги блокируются; пользователю возвращается детерминированное объяснение. Уже выполненные предыдущие шаги не откатываются. Отсутствующий target означает предусмотренное текущее устройство. Каждый worker может направлять команды и device tools только на устройство своего шага.

## Проверка после обновления

1. Два пользователя регистрируют одинаковый `device_id`: профили и состояние каждого остаются собственными.
2. Broadcast «покажи рабочий стол» на двух ПК с разными путями выполняется отдельно на обоих.
3. Недоступный второй ПК даёт частичный сбой, а не полный успех.
4. Подтверждение опасной команды и cancel продолжают работать на выбранном ПК.
5. PLAN с двумя target направляет каждый шаг на свой ПК; ошибочный target не выполняет действие на первом ПК.

Ветка этой работы: `codex/device-isolation`, база `001b965`. Накопленные предыдущие изменения перенесены в `main` отдельно. Скрипт `tools/update_server.py` разрешает только свои штатные ветки; для проверки этой ветки используются `git fetch`, `git switch codex/device-isolation`, `git merge --ff-only origin/codex/device-isolation` и перезапуск `iru`. Механизм обновления этой задачей не меняется.

## Проверки изменения

- Исходный commit: `001b965f72082fc6eb2d1dde36a8aa87f1f28815`.
- Связанные suites: 159 passed.
- Полный `pytest -q`: остановка collection из-за существующего конфликта `agent` / `agent.shell`.
- Все тесты двумя процессами: 612 passed (основной набор) + 11 passed (два модуля Agent Shell).
- `py_compile`: 25 изменённых Python файлов успешно.
- В `test_device_isolation.py` добавлены 24 параметризованных сценария: оба направления profile isolation, миграция, API профилей, online routing, WebSocket-регистрация, cache isolation, независимые контексты broadcast, три общих статуса, confirmation/cancel, неверные/offline/чужие PLAN targets, точная маршрутизация и malformed device_id.
- Старый тест ожидал fallback на текущий ПК; теперь проверяет отсутствие dispatch и явную ошибку. Тест broadcast теперь проверяет два независимых запуска. Остальные изменения тестов передают owner в заглушках профилей и указывают доступные тестовые устройства. Тест onboarding больше не заменяет общий registry новым объектом.
- Production E2E с реальными ПК и живой LLM не выполнялся.

Изменённые файлы:

- `docs/DEVICE_ISOLATION.md`
- `server/controller.py`
- `server/controller_non_pipeline.py`
- `server/controller_pipeline.py`
- `server/database.py`
- `server/device_context.py`
- `server/python_toolchain.py`
- `server/routers/devices.py`
- `server/routers/tasks.py`
- `server/task_runtime.py`
- `tests/test_chat_onboarding.py`
- `tests/test_controller_pipeline_budget.py`
- `tests/test_controller_trust.py`
- `tests/test_device_activation.py`
- `tests/test_device_isolation.py`
- `tests/test_device_state_grounding.py`
- `tests/test_file_confirmation.py`
- `tests/test_lazy_context.py`
- `tests/test_pipeline_plan_review.py`
- `tests/test_pipeline_planner_budget.py`
- `tests/test_pipeline_recovery_receipt.py`
- `tests/test_plan_confirmation_continuation.py`
- `tests/test_plan_stability.py`
- `tests/test_python_runtime.py`
- `tests/test_task_runtime_recovery_status.py`
- `tests/test_tool_only_protocol.py`


### Подготовка Python на новом ПК

Кнопка подготовки теперь показывает итог: окружение готово, Python отсутствует либо окружение требует исправления; при сбое выводятся предупреждения receipt. Одного уведомления «использован инструмент» больше нет.

Агент дополнительно ищет установленный Python в пользовательском `LOCALAPPDATA/Programs/Python/Python*` и `Program Files/Python*`, поскольку PATH агента может быть устаревшим или пустым. Поиск проверяет найденный интерпретатор запуском, не использует Python внутри frozen EXE и не принимает WindowsApps-заглушку.

`prepare_runtime` создаёт venv из уже установленного Python. Автоматическая загрузка/установка базового Python этим изменением не добавлена. Если его на ПК нет, требуется установить Python и повторить подготовку. Изменение поиска вступает в силу после обновления сборки агента.
