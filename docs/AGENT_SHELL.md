# Desktop AgentShell ИРУ на WebView2

Рабочая Windows-оболочка `IruAgent.exe` загружает текущий сайт ИРУ в Microsoft
Edge WebView2. PySide6 остаётся только для native menu, настроек, диагностики,
tray и единственного UI message loop. Qt WebEngine больше не используется.
AgentRuntime, WebSocket protocol, backend, frontend и voice JS не меняются.

## Запуск и зависимости

Нужны Windows, Microsoft Edge WebView2 Evergreen Runtime, .NET Framework
4.6.2 или новее, Python 3.11+ и зависимости приложения. Установленный Edge
и установленный WebView2 Runtime — разные компоненты. При отсутствии Runtime
окно показывает понятное сообщение и ссылку на официальный установщик;
меню агента и настройки остаются доступны. Автоматической установки Runtime нет.

Закрыть прежний агент через tray → «Выход», затем:

```powershell
git switch codex/agentshell-webview
git pull --ff-only
python -m pip install PySide6 websockets httpx "pywebview==6.2.1" "pythonnet==3.2.0"
python agent/agent.py
```

`pywebview` предоставляет DLL Microsoft WebView2 SDK, `pythonnet` загружает
.NET control. Оболочка не запускает pywebview UI loop и не создаёт JS/native
bridge. Привилегированные host objects и WebMessage API отключены.

URL берётся из `server_url`: `wss://irumode.ru` → `https://irumode.ru/`.
Для локальной проверки допустим `ws://localhost:8000`. `IRU_WEB_URL` может
явно переопределить HTTP(S) URL. Embedded credentials запрещены; query и fragment
не переносятся. Agent token не внедряется в сайт. Вход выполняется формой сайта
отдельно от регистрации локального агента.

## Главное окно, меню и tray

Меню «ИРУ» сохраняет действия «Настройки агента», «Обновить страницу»,
«Открыть в браузере» и «Выход». Настройки сохраняют статус устройства, версию,
обновления, reconnect, перенастройку, диагностику и доступ к логам.

При наличии tray крестик скрывает главное окно, сохраняя тот же документ.
Настройки и диагностика также скрываются. Tray возвращает главное окно
и содержит прежние локальные действия. Если tray недоступен, крестик завершает
приложение, чтобы не оставлять скрытое окно без возможности вернуть его.
«Выход» освобождает WebView2, останавливает runtime и завершает Qt loop.
Shutdown идемпотентен; отложенный startup не запускает runtime после выхода.

WebView2 использует native WinForms child HWND внутри Qt окна. Все вызовы
WebView2 выполняются на STA UI thread. Запросы permission и сохранения используют
асинхронные Qt dialogs и WebView2 deferrals: UI не блокирует .NET Task.Result
до завершения операции и не запускает дополнительный UI loop.

## Сессия, навигация и скачивание

Постоянный профиль: `%LOCALAPPDATA%\IRUAgent\webview2` рядом с конфигом агента.
Сайт сохраняет localStorage и постоянные cookies между запусками. Session cookies
подчиняются стандартному поведению WebView2. Профиль отдельный от Edge/Comet/Chrome
и прежнего Qt `webview`; при переходе на WebView2 нужно войти один раз.
Старый профиль не удаляется, автоматического переноса данных нет. Новый профиль
не следует удалять при обычном обновлении приложения.

Пользовательские внешние HTTP(S) ссылки открываются в системном браузере.
Внутренние остаются в окне. Нестандартные схемы, embedded credentials и
непользовательские popup запрещены. HTTP(S) redirects основного документа
допускаются; сложные OAuth popup flows не гарантированы. TLS не обходится.

Download использует асинхронный системный диалог сохранения. Отмена не сохраняет
файл. Перезапись требует штатного подтверждения save dialog. Путь преобразуется
в native Windows формат перед передачей WebView2.

При ошибке загрузки или сбое renderer страницу можно обновить через меню.
Если завершился весь browser process, нужно перезапустить ИРУ; сообщение
различает эти случаи. Сбои записываются в лог. Автоматического reload/replay задач нет.

## Голос

Неподдерживаемый Qt SpeechRecognition больше не используется и не маскирует
API сайта. Голосовая кнопка работает с существующими `ui/js/voice.js` и
`voice-session.js` через speech API WebView2. Отдельный платный STT backend
не добавлен. WebView2 использует Chromium/Edge runtime, но доступность сервиса
распознавания нельзя гарантировать только наличием API.

Микрофон разрешается только для origin ИРУ при сохранении этого origin
в основном документе и после явного согласия в окне «Микрофон ИРУ».
Камера и foreign-origin permission запрещены. Смена страницы во время запроса
не выдаёт разрешение. После согласия доступ сохраняется в памяти до выхода
из приложения, с проверкой origin при каждом запросе. Перезапуски распознавания
не показывают диалог заново. Отказ можно пересмотреть при следующей попытке.
Разрешение не записывается навсегда в профиль.

Скрытие в tray не вызывает stopVoice, reload/unload, mute или TrySuspend.
Отключено фоновое throttling таймеров и renderer. В native smoke тот же документ
и JS timers продолжили работу после X. AgentRuntime остаётся в отдельном потоке.
Реальная работа wake word при скрытом окне требует проверки на целевом ПК.

В проверке 04.10.2026 WebView2 Runtime 154.0.4258.53 успешно запросил микрофон
и начал захват synthetic audio без падения страницы. SpeechRecognition выдал
`start`, `audiostart`, затем `error:network`, `end`: доступ к провайдеру STT
в тестовом окружении не подтверждён. Настоящая русская речь, пунктуация,
подтверждения и полный wake/sleep цикл пока не проверены в desktop.
Из этого теста нельзя делать вывод, что качество совпадает с полным Edge.

## Сборка

```powershell
# Подставить выбранную новую версию:
.\deploy\build_windows.ps1 -Version <НОВАЯ_ВЕРСИЯ> -SkipUpload
```

Скрипт устанавливает закреплённые pywebview/pythonnet и собирает SDK DLL,
loader и Qt widgets через PyInstaller. QtWebEngineCore/Widgets/Quick и
QtWebChannel исключены. Evergreen Runtime устанавливается отдельно на ПК;
DLL SDK не заменяют его. Запускать `dist\IruAgent\IruAgent.exe` вместе
с полной onedir-папкой, не копировать один EXE.

Старый отдельный `python -m agent.shell` / optional `IruShell.exe` остаётся
standalone wrapper без локального AgentRuntime. Он не является новой объединённой
оболочкой; `-BuildShell` по-прежнему собирает его отдельно.

Изолированный frozen onedir smoke через PyInstaller прошёл с WebView2:
localhost страница загрузилась, выполнила JS и сохранилась после X.
Это проверка упакованного компонента, не готового production IruAgent ZIP.
В среде Codex потребовалось убрать сторонние Poppler ICU DLL из PATH при сборке
и запуске. Использовать обычное чистое Windows Python environment.

## Ручная проверка

1. Запустить новый агент. Проверить сайт, вход и online устройство.
2. Открыть меню и настройки: reconnect, перенастройка, диагностика и логи доступны.
3. Нажать микрофон, разрешить доступ. Страница должна остаться на месте.
   Проверить настоящую речь, отправку задания, озвучку, подтверждение, wake/sleep.
   Если распознавание не начинается, сохранить лог и версию WebView2 Runtime;
   успешный capture ещё не подтверждает доступность STT.
4. Закрыть окно крестиком, вернуть через tray. Разговор не перезагружается,
   устройство остаётся online. Отдельно проверить wake word в скрытом окне.
5. Через «Выход» перезапустить приложение: вход и localStorage сохраняются.
6. Скачать бинарный файл, сравнить содержимое. Проверить отмену и overwrite prompt.
7. Открыть внешнюю ссылку: она уходит в системный браузер.
8. Завершить через «Выход»: приложение прекращает работу, устройство offline.

## Автоматические проверки

`tests/test_agent_desktop_shell.py` использует настоящий Windows HWND,
Qt widgets и WebView2 в изолированных процессах с localhost страницами.
Проверяются меню/tray/runtime lifecycle, сохранение документа и таймеров,
localStorage и постоянная HttpOnly cookie после process restart,
разрешения synthetic microphone grant/deny, безопасный SpeechRecognition
constructor и существующий voice JS, навигация/popup, настоящий бинарный
save/cancel, отсутствие SDK и идемпотентный shutdown.

Нужны Windows, WebView2 Runtime и указанные Python зависимости. Без Windows
или импортируемых desktop зависимостей тесты явно skip. Настоящие аккаунты,
реальный микрофон и внешний STT provider тестами не используются.
Build-contract tests проверяют SDK inclusion и исключение Qt WebEngine.

Результаты 04.10.2026: 28 связанных Python tests passed. Основной набор
935 passed, legacy shell 11 passed отдельно (946 суммарно). Voice/PLAN Node
42 passed. py_compile пяти Python файлов, PowerShell parser и git diff --check
прошли. В frozen bundle подтверждены Microsoft SDK/loader и отсутствие
QtWebEngine. Production ZIP не публиковался.

Единый pytest имеет прежний collection conflict: другие тесты импортируют
`agent.py` как модуль `agent`, после чего `agent.shell` перестаёт быть package.
До отдельного исправления запускать `test_agent_shell_config.py` и
`test_agent_shell_tray.py` отдельно от основного набора. Это ограничение
существовало до миграции и не является ошибкой WebView2.

Официальные материалы:
[WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/),
[STA и deferrals](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/threading-model),
[User data folder](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder).
