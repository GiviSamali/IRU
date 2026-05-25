# Window/App Observation

Window/app слой нужен, потому что GUI-программа может запуститься, но процесс быстро завершится, окно появится у другого PID, окно будет скрыто или приложение передаст работу child process. Для пользовательского утверждения "приложение открыто" нужен evidence реального GUI-окна.

## Tools

- `window.list` — список top-level OS windows.
- `window.find` — поиск окна по title/class/process/pid/visibility.
- `window.verify` — проверка, что окно существует и видимо.
- `window.focus` — восстановить/сфокусировать одно найденное окно.
- `window.close` — закрыть конкретное окно.
- `app.launch` — запустить приложение и попытаться проверить окно.
- `app.verify_launch` — проверить запуск через pid/title/process/window.
- `app.close` — закрыть приложение через window/process layer.

В текущей реализации window tools ориентированы на Windows GUI. На других платформах поддержка может быть ограниченной.

## Зачем проверять окно

`subprocess.Popen` или shell command могут вернуть pid, но это не доказывает, что пользователь видит приложение. Надежнее проверять:

- процесс жив;
- появилось top-level окно;
- окно visible;
- title/process/class соответствуют ожиданию.

## Сценарии

### Проверить, открыт ли Блокнот

```text
window.find {"title_contains": "Блокнот"}
answer.text with basis ["step_1"]
```

### Открыть файл и проверить окно

```text
app.launch {"command": "...", "expected_title": "..."}
app.verify_launch {"pid": 1234, "expected_title": "..."}
answer.text with basis ["step_1", "step_2"]
```

### Запустить PyQt приложение и найти окно

```text
device.prepare_runtime
app.launch {"executable": "...venv python...", "args": ["app.py"], "expected_title": "Demo"}
window.verify {"pid": 1234, "title_contains": "Demo"}
answer.text with basis ["step_1", "step_2", "step_3"]
```

## Ограничения

- Браузеры и ассоциированные приложения могут открыть окно в уже запущенном процессе.
- Некоторые приложения стартуют через launcher и передают окно child process.
- PID из launch не всегда совпадает с PID окна.
- `window.find` нужен как универсальная проверка по title/process/class.
- Для headless/server окружений GUI evidence может быть недоступен.
