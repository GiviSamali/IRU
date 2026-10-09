# Desktop AgentShell ИРУ на WebView2

Рабочая Windows-оболочка `IruAgent.exe` загружает текущий сайт ИРУ в Microsoft
Edge WebView2. PySide6 остаётся только для native menu, настроек, диагностики,
tray и единственного UI message loop. Qt WebEngine больше не используется.
AgentRuntime, WebSocket protocol и backend сохраняются. Voice JS сайта сохраняет
прежний lifecycle; обработка wake word учитывает пунктуацию распознавания.

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

Размер host синхронизируется после применения native geometry Qt, через
WinForms SetBounds и layout дочернего WebView2. Одного SetWindowPos недостаточно:
оно оставляло managed bounds и страницу размером 100×30. Берутся физические
пиксели client area, поэтому масштабирование Qt не учитывается дважды.

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

Позднее в тот же день отказ подтверждён на пользовательском ПК:
`microphone permission=granted`, затем `network`, Runtime 154.0.4258.53.
В [Microsoft WebView2Feedback #5724](https://github.com/MicrosoftEdge/WebView2Feedback/issues/5724)
инженер Microsoft предложил отключить новый speech service через
`--disable-features=msSpeechRecognitionServiceUseCetoService`; автор подтвердил
обход на 153. На нашей версии 154 проверены два отдельных процесса с разными
тестовыми профилями и synthetic audio: default → `network`; с флагом →
`start`, `audiostart`, `result`, `result` без `network` за время проверки.
Флаг теперь добавляется автоматически в AdditionalBrowserArguments только
WebView2 окружения ИРУ. Звук распознавания направляется прежнему встроенному
сервису Microsoft `speech.platform.bing.com/speech/recognition/edge/interactive/v1`;
использование этого сервиса явно согласовано с пользователем.
Evergreen Runtime продолжает обновляться. Реестр, системный Edge, profile data,
backend и voice JS не меняются; отдельный платный STT не добавлен.
Это временный workaround: после подтверждённого исправления Microsoft его
следует отдельно перепроверить и удалить. Реальные русские фразы, пунктуация
и полный wake/sleep loop требуют smoke на целевом ПК.

Общий toast сайта скрывает точный SpeechRecognition error. Desktop-оболочка
показывает код и причину в нижней status bar, а в `agent.log` пишет только
`[desktop] speech recognition error=<code> runtime=<version>`.
Служебный JS observer сохраняет native recognizer и обработчики сайта;
он наблюдает только error events главного документа origin ИРУ. Host получает
изолированное console event через DevTools protocol; WebMessage/host objects
остаются отключены. Текст речи, произвольные console messages и auth данные
не выводятся в лог. Диагностика не устраняет отказ провайдера STT и не
подключает другой платный сервис. Обновлять сервер для неё не требуется.

После обновления локального агента нажать микрофон, затем скопировать код
из нижней строки окна или получить диагностические строки в PowerShell:

```powershell
Select-String -Path "$env:LOCALAPPDATA\IRUAgent\logs\agent.log" -Pattern "engine=WebView2", "speech recognition error=", "microphone permission=" | Select-Object -Last 8
```

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
реальный viewport при старте/resize/возврате из tray с масштабом 100/125/150%,
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


Исправление размера 04.10.2026: новый regression воспроизвёл исходный viewport
100×30. После исправления проверены startup, resize и tray/reopen с масштабом
100/125/150%. Frozen onedir smoke теперь проверяет и реальный размер страницы:
1200×746 вместо 100×30. Renderer применяет resize асинхронно; regression
ожидает нужную геометрию с ограниченным deadline.
Основной pytest после исправления: 938 passed; legacy shell отдельно:
11 passed (949 суммарно). py_compile двух изменённых Python файлов и
`git diff --check` прошли. Прежний collection conflict единого запуска
остаётся указанным выше.


Диагностика SpeechRecognition 04.10.2026: 32 связанных Python tests passed;
py_compile трёх изменённых Python файлов и git diff --check прошли.
Новый native regression проверяет code/network в status bar и agent log,
сохранение native recognizer/обработчика сайта и исключение постороннего
console fixture из логов. Он использует synthetic error event, не внешний
STT provider; причина отказа на пользовательском ПК остаётся неподтверждённой.
Полный pytest в этом диагностическом изменении повторно не запускался.


Проверка включённого workaround 04.10.2026: 32 связанных Python tests passed;
py_compile двух изменённых Python файлов и git diff --check прошли.
Повторный live smoke с обычными настройками ИРУ после исправления, Runtime
154.0.4258.53 и synthetic audio дал start/audiostart/result/result без network.
Настройки браузера проверены через настоящий WebView2 control: флаг установлен,
фоновые timers сохранены, sandbox/TLS guards не отключаются. Полный pytest
для этого изменения повторно не запускался. На целевом ПК закрыть агент через
«Выход», обновить ветку и запустить заново; проверка настоящей речи остаётся
ручной. Production ZIP не публиковался.


## Значок панели задач и wake word

Приложение задаёт Windows AppUserModelID `IRU.Agent.Desktop` до первого окна,
включая первичную настройку. Главное окно, приложение и tray используют
существующий `agent/IruIcon.ico`. Исходный запуск `python agent/agent.py`
и сборка `IruAgent.exe` получают одинаковую идентичность приложения вместо
группировки с Python. Для старого закреплённого ярлыка Python снять закрепление
и закрепить новое окно ИРУ после перезапуска.

Wake word распознаётся без учёта регистра: `Иру`, `ИРУ.`, `Иру!`, `Иру,`,
`И Р У`, `И.Р.У.`, `IRU`, `I R U`, `I.R.U.`. Пунктуация возле имени не становится
отдельным запросом: после «ИРУ.» включается обычное десятисекундное окно
слушания. Итоговые фрагменты только из знаков препинания не отправляются.
Пунктуация содержательной команды, например «Создай файл report.docx.»,
сохраняется. Проверка границ слова исключает ложную активацию от «миру»,
«игру», «Ирина» и `IRUser`. Варианты применяются также к «усни», «стоп»,
голосовым решениям PLAN и обычным разрешённым подтверждениям.

Значок меняется после обновления/перезапуска исходного агента или пересборки.
Wake word реализован в `ui/js/voice-session.js`, который загружается с сервера
ИРУ. Одной пересборки агента недостаточно: сервер должен содержать эту версию
frontend, после чего в меню ИРУ выбрать «Обновить страницу». Ветка изменения —
`codex/agentshell-webview`; автоматический updater сервера по-прежнему принимает
только предусмотренные deployment-ветки `main` и `codex/deeptalk-integration`.
Для deployment изменение сначала должно попасть в соответствующую ветку.

Проверки 04.10.2026: 77 Node tests voice/PLAN passed; 54 Python tests desktop,
agent core, packaging/build и voice endpoints passed. Проверка настоящего
Windows process AppUserModelID и значка окна включена в desktop lifecycle test.
Используются localhost и изолированные процессы, без настоящего микрофона.
Полный pytest для этого небольшого изменения повторно не запускался.


Сборка в смешанном Qt-окружении 04.10.2026: pywebview при анализе зависимостей
может найти PyQt5, установленный для другого проекта. PyInstaller запрещает
сборку нескольких Qt bindings в одном приложении. Для `IruAgent` скрипт
явно исключает корневые `PyQt5`, `PyQt6` и `PySide2`; PySide6 остаётся.
Удалять пакеты из пользовательского venv не требуется. Отдельная legacy
сборка `IruShell` не изменялась.

Новый regression исполняет настоящую PowerShell-сборку аргументов и проверяет
передачу исключений PyInstaller. Build/package tests: 11 passed, PowerShell
parser и git diff --check прошли. Полная onedir-сборка из прежнего failed spec
с этими же исключениями в Python 3.13.7 / PyInstaller 6.22.3 / PySide6 6.11.2 /
PyQt5 5.15.10 завершилась с exit code 0. В COLLECT подтверждены WebView2 SDK,
loader и Python.Runtime; остальные Qt bindings и QtWebEngine отсутствуют.
Сборка выполнена в TEMP, без запуска агента и без публикации ZIP.


## Компактная форма WIDGET-B01 (09.10.2026)

Основной `IruMainWindow` стартует компактным: 400×280 Qt logical pixels,
минимум 320×240. Стандартная рамка Windows оставлена ради стабильного HWND.
Внутри — прежний `EdgeWebView`, HTML-документ, профиль и голосовой контроллер.
Меню «Развернуть» и «Компактное окно» меняют только геометрию и видимость
нативной панели; они не вызывают reload, navigation, Runtime.start/stop или
JS voice reset. Изменение размеров мышью использует тот же путь WebView2 bounds.

В компактной форме обычное меню и status bar скрыты; все действия доступны
через кнопку «Меню». Там же находится «Сообщение окна». При ошибке загрузки,
renderer или распознавания появляется отдельная кнопка «Сообщение»; уведомление
показывается только по нажатию. В tray добавлены компактная/полная форма,
скрытие и просмотр сообщения. При отсутствии tray закрытие завершает приложение,
а menu/expand остаются доступны. Новые результаты задач не активируют окно.

`%LOCALAPPDATA%\IRUAgent\window.json` хранит только два прямоугольника —
compact и expanded. Запись через временный файл и os.replace. Токены, сообщения,
tool results, режим голоса туда не попадают. Повреждённые данные игнорируются.
Начальная форма всегда компактная; размеры/позиции форм восстанавливаются.
Если компактное окно вручную увеличили больше 640×480, возврат к нему даёт
400×280. Геометрия ограничивается рабочей областью экрана с учётом рамки,
изменений availableGeometry и удаления/добавления экранов. Always-on-top и
frameless в этом MVP не включены: WindowFlags после создания WebView2 не меняются.

Короткие viewport используют прежний Smart UI и обычную прокрутку. Авторизация
не сжимает форму; composer, microphone/send и опасные действия доступны при
малой высоте. Отдельный widgetMode, renderer или API не введены.

Перед production-сборкой обязательно пройти ручной голосовой smoke: включить
голос, изменить размеры, дождаться TTS, скрыть/вернуть через tray, проверить
wake/sleep, ввод текста и подтверждение. Автоматические проверки используют
localhost, fake transport/STT либо синтетический microphone; они не подтверждают
доступность Microsoft speech service и качество реального микрофона.

Подробный сценарий, команды тестов и ограничения: `IRU-WIDGET-B01.md`.
