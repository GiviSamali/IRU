# Device identity, runtime state and request diagnostics

Fix-pass выполнен в `codex/agentshell-webview` от `4972f44` (последующие изменения после указанного в задании `c13322d` сохранены).

## Подтверждённые причины

Регистрация и activation вызывают `agent/platforms/windows.py:get_machine_guid`: Windows registry `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`. Прежние `agent/core/actions.py:_agent_snapshot_command` и `server/task_runtime.py:_snapshot_command_for_device` помещали `Win32_ComputerSystemProduct.UUID` в `observed_machine_guid`. Agent сравнивал разные типы ID, а сервер ориентировался на hostname и мог перезаписать один hardware mismatch последующим совпадением другого поля.

Теперь canonical Windows ID — registry MachineGuid, чтение всегда из 64-bit registry view. Linux использует `/etc/machine-id` с fallback `/var/lib/dbus/machine-id`. Тип хранится в `machine_guid_type`; SMBIOS UUID — отдельно `system_uuid`. При сравнении типы должны совпадать; generic `uuid` больше не становится machine GUID. Любое несовпадение сравнимых stable IDs остаётся mismatch. Старые нетипизированные наблюдения сохраняют прежний hostname fallback; для полноценной проверки GUID требуется новый snapshot/обновлённый агент. Ownership и отказ для unknown/foreign device не менялись.

Activation раньше проверял только наличие `IRU/runtime/python/python.exe`. Это давало missing даже при рабочем managed venv, созданном на базе системного Python. Паспорт объединял activation/runtime cache без общего правила свежести. Теперь activation вызывает существующий runtime `check`, проверяющий venv/Python/pip, без установки и обновления пакетов.

## Authoritative runtime facts

Текущие runtime_status, python capability/version, pip status/version и venv_python берутся из verified runtime receipt или его verified summary. Выбирается самое новое проверенное наблюдение; activation остаётся источником static identity/paths/capabilities, его исторический runtime не используется как current evidence. Исходные receipts не переписываются ради исправления состояния. Full activation context handle явно помечает свой runtime как historical_only. Нормальные check/prepare/activation операции обновляют только собственные receipts и производный passport.

Freshness window — 24 часа; timestamp обязателен, допустимый clock skew в будущее — 5 минут. Без даты/provenance или после истечения окна: runtime_fresh=false, current status/version/path не подтверждаются; last_known_runtime_status сохраняет исторический статус. Это не непрерывный мониторинг: для нового evidence нужен `device.prepare_runtime(mode=check)`. Python toolchain converter/cache соблюдает тот же срок и не обновляет время проверки просто при чтении. Agent cache, server manifest, device API и compact passport используют согласованные поля. last_activation_check — дата receipt, а не время чтения cache.

## Observability

Переиспользованы `server/run_journal.py` и штатный Uvicorn/systemd журнал. Нового trace storage/backend нет. ContextVar связывает события с task_id при обычном NL, PLAN и onboarding:

request_started → classification/path → controller_selected → tool_result → recovery/answer_adjusted → request_finished.

В событиях только allowlisted metadata: controller/mode, classification, canonical tool/status, iteration/step, terminal answer type/source/reason, elapsed_ms/counts. Нет текста запроса, аргументов, DOM, содержимого файлов, ответов, credentials или literal payloads. Убраны также старые previews команд/ответов из diagnostic debug logs агента и controller. Ошибки tracing не останавливают выполнение. До 256 событий доступны владельцу через существующий `/api/tasks/{task_id}` (runtime TTL задачи); завершающее событие сохраняется при достижении лимита. Полный поток metadata пишет штатный server logger; срок хранения определяет journald deployment.

Для production-run:

```bash
journalctl -u iru --no-pager --since '10 minutes ago' | grep 'iru_lifecycle' | grep 'TASK_ID'
```

Исторические локальные `logs/traces` не содержат trace прежнего conversational failure. Сообщение «Это сложная задача, нужен план» соответствует существующей ветке classifier=PLAN; новый trace покажет, какой путь классификации выбран. Причина конкретного старого неверного решения модели не установлена. Для ответа про memory также нет исходного воспроизводимого trace. GUID mismatch не объявляется причиной этих сообщений; memory authority не изменена.

## Проверки и ограничения

Regression tests покрывают typed identity, настоящие mismatches, раздельные SMBIOS/registry IDs, fresh/stale runtime, согласованность passport/API/manifest, expiry toolchain, обычный NL/PLAN/onboarding trace, приватность metadata/debug logs, owner-only доступ и best-effort tracing.

Единый pytest имеет прежний collection conflict: `agent.py` загружен как module `agent`, после чего `agent.shell` не импортируется в `test_agent_shell_config.py` и `test_agent_shell_tray.py`. Эти файлы проверяются отдельным запуском. Desktop tests требуют отсутствующих PySide6/pywebview/pythonnet и пропускаются. Live Windows snapshot и повтор исторической фразы нужно проверить на актуальном агенте; автоматические tests не доказывают состояние конкретного ПК.
