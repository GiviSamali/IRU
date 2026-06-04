# Agent Shell WebView v1

Agent Shell v1 — минимальная desktop-обертка для существующего Web UI ИРУ.

Это не второй интерфейс, не отдельный чат и не desktop rewrite. Shell открывает тот же сайт ИРУ в отдельном окне, чтобы на локальном ПК не держать вручную вкладку браузера.

## Что делает

- открывает существующий Web UI URL;
- использует `pywebview`, если он установлен и доступен;
- если WebView недоступен, открывает тот же URL в браузере через `webbrowser.open`;
- запускает optional tray/status слой, если доступны `pystray` и `Pillow`;
- не внедряет scripts в сайт;
- не обходит auth;
- не хранит пароль, access token или refresh token в shell config.

Если сайт требует вход, пользователь входит через тот же Web UI, что и в браузере.

## Что не делает

- не дублирует Web UI logic;
- не копирует `ui/index.html`;
- не запускает второй chat frontend;
- не меняет agent WebSocket behavior;
- не меняет Tool Registry, Device Passport, Memory, task progress или auth;
- не добавляет voice, wake-word, overlay или screen automation.

## Запуск

Из корня репозитория:

```bash
python -m agent.shell
```

Также можно запустить напрямую:

```bash
python agent/shell/main.py
```

На Windows есть короткий launcher:

```powershell
.\agent\shell\run_shell.ps1
```

## Настройка URL

URL выбирается в таком порядке:

1. `IRU_WEB_URL` environment variable.
2. `IRU_HOME/state/shell_config.json`.
3. `IRU_HOME/shell_config.json`.
4. fallback: `http://127.0.0.1:8000`.

Пример:

```powershell
$env:IRU_WEB_URL = "https://irumode.ru"
python -m agent.shell
```

Если config отсутствует, Shell создает файл:

```json
{
  "web_url": "http://127.0.0.1:8000",
  "window": {
    "title": "ИРУ",
    "width": 1200,
    "height": 800,
    "min_width": 900,
    "min_height": 600
  }
}
```

URL можно изменить вручную в JSON. Не добавляйте туда секреты: Shell config не предназначен для токенов, паролей или auth cookies.

## IRU_HOME

По умолчанию:

Windows:

```text
%LOCALAPPDATA%\IRU
```

Linux:

```text
~/.iru
```

Для разработки можно указать:

```bash
IRU_HOME=/tmp/iru python -m agent.shell
```

## WebView dependency

`pywebview` опционален. Если он не установлен, Shell не падает, а пишет:

```text
WebView недоступен, открываю ИРУ в браузере: <url>
```

и открывает URL в браузере.

Для desktop-окна можно установить зависимость вручную:

```bash
pip install pywebview
```

## Tray dependency

Tray тоже опционален. Если `pystray` или `Pillow` недоступны, Shell пишет понятное сообщение и продолжает работать без tray.

Для desktop-окна и tray:

```bash
pip install pywebview pystray pillow
```

Tray menu v1:

- `Открыть ИРУ` — фокусирует WebView window, если это поддерживает текущий backend `pywebview`; иначе открывает URL в браузере.
- `Открыть в браузере` — всегда открывает текущий `web_url` через браузер.
- `Настройки` — открывает shell config в Explorer/Finder/file manager, если возможно; иначе печатает путь.
- `Статус` — показывает или печатает минимальный status: `web_url`, `config_path`, доступность WebView и tray.
- `Выход` — останавливает tray и закрывает shell window, если это поддерживается.

Если WebView недоступен, но tray доступен, Shell открывает URL в браузере и остается в tray до выхода через menu.

## Roadmap

Планируется позже, не реализовано в v1:

- hotkey / push-to-talk;
- voice input;
- overlay;
- более плотная интеграция с локальным agent lifecycle.

