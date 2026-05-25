# Device Activation и Managed Python Runtime

Device Activation v1 делает устройство пригодным для управляемой работы ИРУ. Runtime layer готовит IRU-owned Python environment для Python/PyQt задач.

## Device Activation

Поддерживаемые режимы:

- `soft` — обычная активация/проверка без агрессивного восстановления;
- `full` — расширенная активация, если поддерживается agent-side реализацией;
- `repair` — восстановление деградированного состояния, если поддерживается.

Activation создает или проверяет:

```text
IRU_HOME/
  state/
  runtime/
  cache/
  scripts/
  tools/
  logs/
  traces/
  artifacts/
```

## Activation receipt

Activation receipt содержит:

- `identity` — hostname/computer name/user/machine identity;
- `paths` — `IRU_HOME` и важные локальные директории;
- `runtime` — базовое состояние Python/runtime на момент activation;
- `capabilities` — доступные возможности агента;
- `health` — состояние agent/runtime/path checks;
- `created_at` и activation metadata.

Сервер принимает activation как валидную только после проверки receipt. Затем он сохраняет compact `activation_summary`, а не делает LLM источником правды.

## Managed Python Runtime

Runtime actions:

- check — проверить runtime без создания;
- prepare — подготовить managed venv;
- repair — восстановить broken/degraded runtime, если поддерживается.

Runtime receipt описывает:

- статус runtime;
- путь к venv;
- `venv_python`;
- Python version;
- pip status;
- packages;
- receipt hash / timestamps.

Агент пишет runtime receipt в локальное state/runtime хранилище, а сервер хранит compact summary.

## UI

Device Passport UI показывает:

- activation status;
- runtime status;
- Python version;
- pip status;
- managed venv path, если он есть;
- кнопки/controls для activation, runtime и state refresh.

Used-tools transparency показывает, какие typed tools были вызваны, например `device.activate`, `device.prepare_runtime`, `device.refresh_state`.

## Что v1 не делает

- Не скачивает Python автоматически, если это не реализовано в текущем агенте.
- Не делает полноценный sandbox.
- Не является production-grade isolation boundary.
- Не автообновляет agent как часть activation/runtime prepare.
- Не гарантирует, что shell fallback безопасен для любых команд без политики и подтверждения.
