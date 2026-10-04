# Desktop AgentShell ИРУ

Рабочая Windows-оболочка `IruAgent.exe` использует существующий PySide6 UI
из `agent/ui/shell.py`. Главное окно теперь показывает текущий сайт ИРУ через
`QWebEngineView`. Backend, frontend, agent actions, WebSocket protocol и voice JS
не меняются. Локальной копии сайта и второго frontend нет.

## Запуск и URL

Из репозитория с установленными `PySide6`, `websockets`, `httpx`:

```powershell
python agent/agent.py
```

Сначала работает существующая настройка локального агента и проверка обновлений.
Затем появляется окно ИРУ. URL берётся из текущего `server_url` агента:
`wss://irumode.ru` → `https://irumode.ru/`. Поэтому другой явно настроенный сервер
не смешивается с production. Для локального теста допустим `ws://localhost:8000`.
`IRU_WEB_URL` может явно переопределить HTTP(S) URL. Credentials и query с токенами
в адрес WebView не переносятся. Вход в сайт выполняется обычной формой сайта,
отдельно от регистрации локального агента; agent user_token не внедряется в JS.

## Главное окно, локальные настройки и tray

- Главное окно — существующий сайт, плюс стандартное native menu «ИРУ».
- «Настройки агента» открывает прежнее окно статуса: устройство, соединение,
  версия, обновления, переподключение, перенастройка и диагностика.
- Окно диагностики и папка логов остаются доступны.
- Крестик главного окна при доступном tray скрывает окно, не уничтожая страницу.
- Крестик настроек/диагностики также только скрывает соответствующее окно.
- Tray: «Открыть ИРУ», «Настройки агента», диагностика, существующие локальные
  действия, «Выход». Нажатие на tray icon возвращает главное окно.
- Только «Выход» выполняет обычный runtime.stop(wait=True) и завершает Qt loop.
  Shutdown идемпотентен; отложенный startup callback не запускает runtime после выхода.
- Если ОС не предоставила system tray, крестик главного окна завершает приложение:
  скрывать окно без возможности вернуть его нельзя. Native menu «Выход» тоже доступно.

## Сессия и WebView

Qt WebEngine выбран как встроенный компонент текущего PySide6 стека.
Сайт получает обычные JS/CSS, cookies и localStorage, без native bridge к runtime.
Named profile сохраняется рядом с конфигом локального агента:
`%LOCALAPPDATA%\IRUAgent\webview\storage` и `webview\cache`.
Профиль сохраняет session cookies и localStorage между запусками. Это отдельный
профиль, он не импортирует сессию Comet/Chrome. На первом запуске нужен вход.
Не удалять этот каталог при обычном обновлении приложения.

Внешние HTTP(S) ссылки открываются в системном браузере. Ссылки внутри origin
ИРУ остаются в WebView. Download использует системный диалог сохранения;
отмена не сохраняет файл, существующий файл требует штатного подтверждения диалога.
Всплывающие окна с пустым/нестандартным адресом и сложные OAuth popup flows
не гарантируются первой версией. Сертификаты TLS не обходятся.
При недоступном сайте появляется сообщение с предложением обновить страницу.

## Voice и wake word

В текущем проекте распознавание и wake word находятся в `ui/js/voice.js` и
`voice-session.js`, а не в AgentRuntime. Нативная оболочка не вызывает stopVoice,
не делает reload/unload, не мутит страницу и не переводит её в Frozen/Discarded
при скрытии. Документ остаётся активным и видимым для renderer; окно Windows
при этом скрыто. WebSocket локального агента живёт в прежнем отдельном runtime thread.

В PySide6 6.11.2 конструктор `SpeechRecognition` завершает renderer:
`No binder found for interface media.mojom.SpeechRecognizer`, bad IPC reason 123.
Это происходит до запроса доступа к микрофону. В изолированном тесте
`webkitSpeechRecognition` не обрушал страницу, но за 20 секунд не вызвал ни
`onstart`, ни запрос микрофона. Замена имени API не подтверждает рабочий STT.

Оболочка скрывает оба неподдерживаемых speech API только в главном документе
origin ИРУ, до загрузки скриптов сайта. Существующий voice JS отключает кнопку
и горячую клавишу; status bar объясняет ограничение и предлагает «Открыть в браузере».
Это защита от падения, а не реализация голоса в desktop. Для полноценного desktop
voice нужен отдельно согласованный поддерживаемый STT path.

Обычный `getUserMedia` работает в проверке с synthetic audio device.
Доступ к микрофону запрашивается только для origin ИРУ и требует пользовательского
разрешения. Диалог неблокирующий, использует `permissionRequested`; при смене
страницы или невалидном permission доступ не выдаётся. Завершение renderer
записывается в лог с status/code и показывает сообщение об обновлении страницы;
автоматического reload нет, чтобы не повторять задачи.
Сеть и реальный микрофон проверяются отдельно на целевом ПК.
Автоматические тесты доказывают сохранность документа/таймеров после X,
но не распознавание настоящей речи и не доступность провайдера STT.
Без успешного voice smoke критерии G и полного voice loop остаются непроверенными.
Голосовая архитектура и браузерный fallback не переделываются в этой задаче.

## Сборка

Обычный `deploy/build_windows.ps1` теперь включает QtWebEngineCore/Widgets и
позволяет PyInstaller собрать WebEngine process/resources через стандартные hooks.
Размер ZIP увеличится из-за Chromium. Ветка предназначена для проверки desktop
опыта; не публикуйте экспериментальную сборку в production до smoke.

```powershell
# Подставьте выбранную новую версию:
.\deploy\build_windows.ps1 -Version <НОВАЯ_ВЕРСИЯ> -SkipUpload
```

Запускать полученный `dist\IruAgent\IruAgent.exe` вместе с полной onedir-папкой.
Не копировать только EXE. Установленный агент другой сборки предварительно
закрыть через его tray «Выход», чтобы не создать второе подключение устройства.

Старый отдельный `python -m agent.shell` / optional `IruShell.exe` использует
pywebview и pystray и не владеет локальным AgentRuntime. Это прежний standalone
wrapper; он не изменён и не является точкой входа новой объединённой desktop-оболочки.
Параметр `-BuildShell` по-прежнему собирает его отдельно и не загружает в agent API.

## Ручная проверка acceptance criteria

1. Запустить новую onedir-сборку. Главное окно должно показать сайт ИРУ.
2. Войти в аккаунт, выполнить обычную безопасную задачу и проверить устройство online.
3. Открыть «Настройки агента»: статус, reconnect, настройка, диагностика и логи доступны.
4. В текущем Qt WebEngine кнопка голоса должна быть недоступна, а сайт — оставаться
   рабочим. В status bar показано объяснение. «Открыть в браузере» позволяет проверить
   существующий голос сайта в поддерживаемом браузере. Desktop voice и wake word
   пока не являются пройденными acceptance criteria.
5. Нажать X. Убедиться, что приложение осталось в tray, а устройство online
   видно с другого браузера/телефона. Wake word в Qt сейчас недоступен: см. ограничение STT.
6. Нажать «Открыть ИРУ» в tray: та же страница и разговор возвращаются без перезагрузки.
7. Перезапустить приложение через «Выход»: login должен сохраниться.
8. Сохранить созданный сайтом файл; проверить отказ от сохранения и штатный overwrite prompt.
9. Через tray «Выход» закрыть приложение. Убедиться, что IruAgent.exe и его
   QtWebEngineProcess завершились, а устройство стало offline.

## Автоматические проверки

`tests/test_agent_desktop_shell.py` запускает реальный offscreen Qt/WebEngine
в отдельных процессах, использует localhost страницы и synthetic runtime.
Проверяет главное окно и меню, X/reopen, активный документ/JS timers,
localStorage и session cookie после process restart, shutdown, внешние ссылки,
границу microphone origin, настоящий synthetic media grant/deny, защиту от
конструктора SpeechRecognition до inline site scripts и отмену download. Реальные аккаунты и микрофон не используются.
Без PySide6 эти desktop integration tests явно skip; для их полноценного запуска
установить PySide6 в тестовое окружение. Build-contract tests проверяют inclusion
WebEngine; готовую onedir-сборку нужно проверить отдельно на Windows.

Qt API: [QWebEngineProfile](https://doc.qt.io/qtforpython-6/PySide6/QtWebEngineCore/QWebEngineProfile.html)
и [QWebEnginePage](https://doc.qt.io/qtforpython-6/PySide6/QtWebEngineCore/QWebEnginePage.html).

Проверки 04.10.2026: 20 связанных Python tests passed; полный набор split-запусками
929 + 11 = 940 passed. Единый pytest сохраняет две прежние collection errors
`agent.py` / `agent.shell`. Node voice/PLAN: 42 passed. py_compile четырёх файлов,
PowerShell parser и git diff --check прошли. Отдельный frozen onedir WebView smoke
через PyInstaller и настоящий QtWebEngineProcess прошёл: localhost документ
загрузился, исполнил JS и сохранился после X. Это проверка компонента, а не
готового IruAgent ZIP и не реальных аккаунтов/микрофона. Для frozen smoke пришлось
убрать из PATH стороннюю ICU из среды Codex; обычный Windows Python со стандартными
Qt/PyInstaller hooks собирает компонент без изменений приложения.


Проверка исправления падения 04.10.2026: 26 связанных Python tests passed;
основной набор 933 passed, legacy shell 11 passed отдельно (944 суммарно).
Единый pytest по-прежнему остановлен двумя прежними collection errors
`agent.py` / `agent.shell`. Реальный `voice.js` сайта на localhost использует
unsupported-browser state без создания аварийного recognizer. Synthetic
getUserMedia grant/deny прошли. Это подтверждает защиту страницы и capture API,
но не работу распознавания. Новый frozen ZIP после этого исправления не собирался.
