# Deployment

Этот документ описывает практический deploy flow для текущего прототипа. Команды не содержат секретов; токены и ключи передаются через env или локальные config-файлы.

## Server deployment

Типовой Linux/VPS flow:

```bash
cd /opt/iru/app
git pull origin main
python3 -m venv /opt/iru/venv
source /opt/iru/venv/bin/activate
pip install -r requirements.txt
python -m py_compile server/main.py
systemctl restart iru
systemctl status iru --no-pager
```

Если systemd unit еще не установлен, используйте файлы из `deploy/` как основу:

```bash
cp deploy/iru.service /etc/systemd/system/iru.service
systemctl daemon-reload
systemctl enable iru
systemctl restart iru
```

Логи:

```bash
journalctl -u iru -n 100 --no-pager
```

## Local server smoke check

Bash:

```bash
python -m py_compile server/main.py
cd server
python main.py
```

PowerShell:

```powershell
python -m py_compile server/main.py
Set-Location server
python main.py
```

## Agent build

Windows build script:

```powershell
.\deploy\build_windows.ps1 -Version 3.7 -SkipUpload
```

С загрузкой на сервер:

```powershell
$env:IRU_ADMIN_TOKEN = "<admin-token>"
.\deploy\build_windows.ps1 -Version 3.7 -Server "https://example.com"
```

Сборка использует PyInstaller, публикует `dist/IruAgent` и ZIP. `dist/` и build artifacts не коммитятся.

Auto-update contract агента не меняется:

```text
dist/IruAgent/
dist/IruAgent/IruAgent.exe
dist/IruAgent/VERSION.txt
dist/IruAgent.zip
POST <Server>/api/agent/upload?version=<Version>
```

`IruAgent.zip` — единственный artifact, который загружается в `/api/agent/upload` текущим скриптом.

## Agent Shell build

Agent Shell — отдельный локальный desktop wrapper существующего Web UI. Он не является update artifact для `IruAgent` и в этой версии не загружается в `/api/agent/upload`.

Сборка agent + shell:

```powershell
.\deploy\build_windows.ps1 -Version 3.7 -BuildShell -ShellWebUrl "https://irumode.ru"
```

Локальная сборка без upload:

```powershell
.\deploy\build_windows.ps1 -Version 3.7 -SkipUpload -BuildShell -ShellWebUrl "https://irumode.ru"
```

Результат:

```text
dist/IruAgent/IruAgent.exe
dist/IruAgent.zip
dist/IruShell/IruShell.exe
dist/IruShell/VERSION.txt
dist/IruShell/BUILD_INFO.json
dist/IruShell/shell_config.json
dist/IruShell.zip
```

Если `-BuildShell` указан, а `-ShellWebUrl` нет, Shell URL берется из `-Server`. Это относится только к сборке shell и не меняет upload URL агента. `-Token` никогда не пишется в `BUILD_INFO.json`, `shell_config.json` или artifact files.

Если source-запуск `python -m agent.shell` открывает `http://127.0.0.1:8000`, значит не задан `IRU_WEB_URL` и нет shell config. Для production URL:

```powershell
$env:IRU_WEB_URL = "https://irumode.ru"
python -m agent.shell
```

Или отредактируйте `IRU_HOME/state/shell_config.json` / `IRU_HOME/shell_config.json`.

После обновления проверяйте Task Manager и Startup shortcut: запущенный процесс должен указывать на актуальный `dist/IruAgent/IruAgent.exe` или `dist/IruShell/IruShell.exe`, а не на старый путь.

## Versioning

Версию задает параметр `-Version`. Build script пишет `VERSION.txt` и `BUILD_INFO.json` в собранные bundles `IruAgent` и `IruShell`.

Rebuild required when меняется:

- `agent/agent.py`;
- `agent/core/actions.py`;
- `agent/core/runtime.py`;
- `agent/core/local_state.py`;
- `agent/ui/*`;
- agent-side dependencies или packaging.

Server-only изменения не требуют пересборки агента, если agent protocol/actions не изменились.

## Update checklist

Перед выкладкой:

- слить актуальный `main` в рабочую ветку или fast-forward main после review;
- проверить, что нет случайных изменений в `dist/`, build artifacts, `.venv`, локальных логах, DB;
- выполнить `git diff --check`;
- выполнить `python -m py_compile server/main.py`;
- deploy server;
- rebuild agent, если менялись agent-side файлы;
- проверить activation/runtime/state/window flows.

Smoke checks после deploy:

```text
1. agent connects over WebSocket
2. Device Passport shows connected device
3. device.activate returns valid activation summary
4. device.prepare_runtime returns runtime summary
5. device.refresh_state returns live or explicit unavailable status
6. window.find/window.verify works on Windows GUI target
7. UI shows used tools
```

## Notes

Проект experimental/beta-stage. Текущий deploy flow подходит для внутреннего прототипа и тестовых пользователей, но не должен описываться как production-grade enterprise deployment без отдельной security hardening работы.
