# Локальный агент

Агент — локальный runtime ИРУ на устройстве. Он подключается к серверу как WebSocket client, выполняет actions, хранит `IRU_HOME` и является владельцем локального состояния устройства.

## Роль агента

- поддерживает WebSocket connection с сервером;
- отправляет registration payload при connect/reconnect;
- выполняет agent-side actions из `agent/core/actions.py`;
- работает с локальной файловой системой и OS APIs;
- хранит receipts, snapshots, passport cache, logs и runtime в `IRU_HOME`;
- отдает cached passport при reconnect, если он есть.

## IRU_HOME

Windows:

```text
%LOCALAPPDATA%\IRU
```

Linux:

```text
~/.iru
```

## Структура

Activation создает и использует локальные директории:

```text
IRU_HOME/
  state/
  cache/
  scripts/
  tools/
  logs/
  traces/
  artifacts/
  runtime/
```

`runtime/` содержит managed Python runtime и receipts, если runtime был подготовлен.

## Локальное состояние

Основные state-файлы:

```text
state/activation_receipt.json
state/python_runtime_receipt.json
state/state_snapshot.json
state/device_passport.json
```

В архитектурных обсуждениях runtime receipt может называться `runtime_receipt`; текущая agent-side реализация пишет его как `state/python_runtime_receipt.json`.

Дополнительно activation пишет совместимые файлы вроде `activation.json`, `identity.json`, `capabilities.json`, `python_receipt.json` и `health.json`.

## Registration payload

При подключении агент собирает compact system info и добавляет cached passport, если он найден локально. Payload может включать:

- `activation_summary`;
- `runtime_summary`;
- `state_snapshot_summary`;
- `hardware_summary`;
- `cached_passport`.

Сервер сохраняет эти данные как временное зеркало подключенного устройства и использует их для Device Passport UI.

## Actions

Агент поддерживает основные actions:

- `device.activate` — создает/проверяет `IRU_HOME`, пишет activation receipt и summary.
- `device.prepare_runtime` — готовит managed Python venv и пишет runtime receipt.
- `device.refresh_state` — собирает live snapshot, identity receipt, GPU/hardware summary и обновляет passport cache.
- `device.get_cached_passport` — возвращает локальный cached passport без нового сбора snapshot.
- `write_content` — пишет текстовый файл без shell escaping.
- `get_file_content` — читает файл.
- `list_dir` — показывает директорию.
- `window.list`, `window.find`, `window.verify`, `window.focus`, `window.close` — OS window tools.
- `app.launch`, `app.verify_launch`, `app.close` — запуск и проверка GUI-приложений.
- `execute_cmd` — shell fallback, когда typed tool недоступен или недостаточен.

Также есть `agent.shutdown`, обрабатываемый runtime-слоем агента.

## Reconnect behavior

При reconnect агент снова отправляет registration payload. Если `state/device_passport.json` существует, он отправляет compact cached passport. Это позволяет UI после обновления страницы увидеть последний agent cache, пока агент подключен и прислал его серверу.

Если агент offline, сервер не может прочитать локальный `IRU_HOME/state` напрямую.
