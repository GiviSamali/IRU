# IRU — WIDGET-B01: результат для Code Review

Основа: codex/agentshell-webview, c997957180fd39aa6c41f50d66bc328c101eeb03.
Дата: 09.10.2026. Изменения оставлены в рабочей копии, без commit/push/PR.

1. Реализована компактная форма основного IruMainWindow: 400×280 Qt logical
   pixels, minimum 320×240. Существующая рамка Windows обеспечивает drag/resize.
   Нативная панель содержит «Развернуть», «Меню», а при ошибке — «Сообщение».
   Полная форма возвращает обычное меню и status bar. Tray получил выбор формы,
   скрытие и просмотр сообщения. Always-on-top не принуждается и не добавлен.
2. Файлы: agent/ui/webview.py, agent/ui/shell.py, новый agent/ui/window_state.py,
   ui/css/smart-ui.css, tests/test_agent_desktop_shell.py,
   tests/smart-ui-browser.test.cjs, docs/AGENT_SHELL.md и этот отчёт.
3. Переключение меняет geometry и visibility нативных панелей. WindowFlags,
   SetParent и порядок COM/STA не меняются. EdgeWebView._resize_native остаётся
   прежним: physical client RECT → WinForms bounds. Сохранённая геометрия
   ограничивается availableGeometry с учётом рамки. Мониторные сигналы вызывают
   только clamp, без show/raise/activate. После drag/resize — debounced сохранение.
4. Один AgentRuntime, один EdgeWebView, один профиль и тот же документ.
   Переключение не вызывает reload/navigate, voice reset, login или запрос задач.
   Native test сравнивает identity view и HWND, JS reference, counter/timer,
   ожидающее подтверждение и число navigation events. Tray/runtime test проверяет
   ровно один start и stop. HostObjects и WebMessage остаются выключенными.
5. Короткий viewport использует существующий Smart UI. CSS уменьшает вертикальные
   отступы, сохраняет отдельную прокрутку чата и composer. Scroll padding не
   вытесняет кнопку подтверждения из короткой области. Авторизация прокручивается
   без flex-сжатия формы. Text/Task/File/Action и прежние API не изменены.
6. Тесты: JS — 148 passed; native desktop — 19 passed без skips; compile() четырёх
   изменённых Python-файлов — OK. Основной Python suite — 1013 passed / 17 skipped,
   shell config/tray — отдельно 11 passed. В обычном полном запуске остаётся
   существующая коллизия agent.py / namespace agent.shell при сборке тестов:
   test_agent_core/local_state добавляют agent в sys.path. Поэтому весь набор
   проверен двумя запуска́ми, без изменения посторонних импортов. Native skips
   основного suite компенсированы отдельным Windows-запуском; новый тест
   восстановления настроек добавлен и также прошёл в native suite.
7. Реальный localhost smoke: Windows, Python 3.13.7, WebView2 154.0.4258.62.
   Одна навигация, один HWND/WebView2, same JS object; текущий mock-чат и pending
   confirmation сохранились. Viewport: 400×251 → 1200×746 → 400×251.
   DPI 100/125/150% проверен через QT_SCALE_FACTOR и сравнение физического RECT
   с CSS viewport × devicePixelRatio. Реальный аппаратный переход между двумя
   мониторами с разными DPI не выполнялся; monitor removal проверен сигналом
   восстановления после вынесения геометрии за доступную область.
8. Реальные снимки одного экземпляра localhost-приложения: iru-widget-compact.png,
   iru-widget-expanded.png и iru-widget-restored.png. Они сохранены отдельно от
   Git в каталоге артефактов Codex. Снято окно с синтетическими данными; реальные
   токены/переписка/содержимое устройств не использовались.
9. Запуск из проекта с существующим Windows venv: `python agent/agent.py`.
   Для демонстрации локального UI запустить Node mock (ниже), затем временно
   задать IRU_WEB_URL=http://127.0.0.1:8773 и запустить агент. Этот override меняет
   только URL страницы: настройки AgentRuntime и его WS-соединение не меняются.
   Mock API не выполняет команды и отключает voice; не проверяет реальное STT.
10. Ограничения: стандартная рамка, без frameless/always-on-top. Геометрия хранится
    только в %LOCALAPPDATA%/IRUAgent/window.json, формы compact/expanded; состояние
    maximized не сохраняется. Для минимальных размеров требуется рабочая область
    экрана, вмещающая 320×240 плюс рамку. Реальный голос, TTS, wake/sleep,
    подтверждение и фокус при вводе требуют ручного smoke на целевом ПК перед
    production-сборкой. Автотесты не записывают реальный микрофон, не оплачивают
    STT/LLM и не выполняют реальные действия на устройствах. Ошибка recognition
    проверяется синтетически; реальный renderer crash не провоцировался.
11. Diff проверен вручную, git diff --check — OK. Полный patch подготовлен отдельно
    для review, включая новые файлы; бинарники и test profiles в Git не добавлены.

## Команды проверок

В этом окружении native Python: C:/Users/russa/PycharmProjects/PythonProject1/.venv/Scripts/python.exe.
Основной suite: bundled Python + PYTHONPATH=$env:TEMP/iru-audit-20261002/deps.
Node: bundled Node + NODE_PATH=<bundled dependencies>/node/node_modules.
IRU_TEST_PYTHON для browser-bridge tests указывал на bundled Python.

```powershell
python -m pytest -q tests/test_agent_desktop_shell.py --basetemp "$env:TEMP/iru-widget-native-complete-20261009"
node --test tests/*.test.cjs
python -m pytest -q --ignore=tests/test_agent_shell_config.py --ignore=tests/test_agent_shell_tray.py --basetemp "$env:TEMP/iru-widget-suite-20261009"
python -m pytest -q tests/test_agent_shell_config.py tests/test_agent_shell_tray.py --basetemp "$env:TEMP/iru-widget-shell-suite-20261009"
git diff --check
```

Последняя проверка коротких viewport после удаления неиспользуемого CSS-правила:
`node --test --test-name-pattern=widget tests/smart-ui-browser.test.cjs` — 4 passed.

## Воспроизводимая демонстрация

1. Активировать существующий Windows venv и открыть корень репозитория.
2. Для mock UI в отдельном терминале:
   `node -e "require('./tests/helpers/smart-ui-fixtures.cjs').createServer({demo:true}).server.listen(8773,'127.0.0.1')"`.
3. В терминале агента: `$env:IRU_WEB_URL='http://127.0.0.1:8773'; python agent/agent.py`.
4. Убедиться, что стартовал компактный IRU с чатом «Smart UI — демонстрация».
5. Прокрутить Text, раскрыть полный текст; проверить running/blocked статусы,
   File и опасное Action. Кнопки подтверждения не нажимать для проверки continuity.
6. Нажать «Развернуть», изменить размер мышью, вернуть «ИРУ → Компактное окно».
7. Убедиться, что тот же чат и ожидающее подтверждение остаются доступными.
8. Закрыть окно в tray, вернуть «Открыть ИРУ»; проверить тот же документ.
9. Системные ошибки доступны через «Меню → Сообщение окна»; они не открывают
   окно автоматически. Явный reload только через меню.
10. Завершить приложение через «Выход». Удалить временный override:
    `Remove-Item Env:IRU_WEB_URL -ErrorAction SilentlyContinue`.

Для production URL выполнить отдельный ручной smoke: настоящий микрофон,
«Иру», команда, resize во время исполнения, TTS, ожидание подтверждения,
compact/full/tray, «усни», повторная активация. Проверить отсутствие повторной
отправки команды и сохранность ввода текста. Здесь этот smoke не выполнен.
