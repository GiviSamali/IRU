# Browser Bridge v1

Ветка `codex/browser-bridge-v1`, исходный commit `9e0119f4fba2dd4cf74bbd2d85b106001f1c6f46`. Оконное исправление «разверни обратно» уже находится в `main` на этом commit. Оно выполняет одну команду восстановления наблюдаемого окна без LLM; native-проверка ограничена 750 мс. Утверждение о фокусе требует отдельного подтверждённого действия activate.

## Архитектура

`Пользователь → LLM/controller → task_runtime → browser_bridge → отдельный WebSocket расширения → статический content script → DOM/browser APIs → evidence/journal → ответ/UI/DeepTalk`.

Расширение Chromium Manifest V3 работает внутри одного профиля Chrome/Edge. Это отдельная capability конкретного `user_id + device_id`; оно не заменяет подключение агента. Агент должен быть онлайн при привязке и выполнении команды. PLAN и обычный controller используют один серверный service. Сохраняются журнал инструментов, operation cards, один tool call за итерацию и cancel. Обычный Browser Bridge запрос ограничен 12 controller iterations, PLAN сохраняет свой текущий лимит 12. Простые draft/send заканчиваются после достаточного результата; многополевые формы и send→wait→read продолжаются.

Открытие приложения/произвольного URL остаётся в существующих app tools. `web_search` остаётся Yandex Search API. Browser Bridge работает с уже открытыми HTTP/HTTPS вкладками. Имена сайтов отсутствуют в production handlers и контракте.

## Установка

1. Обновить сервер до ветки Browser Bridge (команды ниже).
2. На компьютере нужного устройства получить эту же ветку репозитория. Агент ИРУ должен быть подключён к тому же аккаунту.
3. Chrome: `chrome://extensions`; Edge: `edge://extensions`. Включить режим разработчика, выбрать «Загрузить распакованное расширение», указать каталог `browser_extension`.
4. Открыть настройки расширения. Указать `https://irumode.ru`, точный ID устройства, например `givi` или `Second`, и свой токен аккаунта ИРУ. Это токен, которым выполнен вход в ИРУ, не пароль и не ключ LLM. Привязка также принимает действующий access JWT. Если вход уже выполнен в браузере: DevTools → Application → Local Storage → https://irumode.ru → скопировать значение `iru_access_token` и вставить в настройки расширения. Это действие выполняет пользователь в своём аккаунте; extension не читает токен из страницы автоматически.
5. Нажать «Подключить». Токен аккаунта удаляется из поля и не сохраняется. Расширение хранит только scoped browser credential, bridge UUID и адрес сервера. Дождаться «Подключено».
6. Открыть/обновить нужную вкладку. Старые вкладки также получают только статический content script при первом запросе. При обновлении extension нажать Reload на странице расширений и обновить целевую вкладку.

Поддерживаются Chromium 116+ и Edge с Manifest V3. Только один активный bridge на устройство. Другое действующее подключение не вытесняется произвольно; настройки показывают конфликт. Истёкшую/отозванную привязку надо повторить. При офлайн-агенте сначала подключить агент, затем переподключить расширение.

Host permissions HTTP/HTTPS нужны для универсальных открытых страниц и связи с сервером. Нет permissions cookies, history, debugger. Профиль браузера, cookies и пароли не извлекаются. Для внешнего сервера обязателен HTTPS; HTTP разрешён только localhost. Токены не появляются в query string, prompt или tool result.

## Серверный протокол

- `POST /api/browser/pair`: существующая account auth (`Authorization: Bearer` / `X-Token`), body `{device_id}`. Проверяется owned DB profile и живое owned agent connection. Возвращает случайный scoped token на 30 дней; в SQLite хранится только SHA-256 токена. CORS разрешён только origin расширений, без cookies.
- `GET /api/browser/status?device_id=...`: account auth, строгая проверка ownership, только статус и bridge identity.
- `/ws/browser`: отдельный WebSocket, зарегистрированный до `/ws/{device_id}`. Первое сообщение `{type:"hello",token,device_id,bridge_id}`. Identity фиксируется сервером из scoped credential. Ответ `{type:"ready",connection_id,device_id}`. Ping/pong каждые 20 секунд поддерживают MV3 worker.
- Команда `{type:"command",request_id,operation,params,authorization:{external_action:boolean}}`.
- Ответ `{type:"result",request_id,result}`. Связывается только с pending request этого подключения. Late/unknown replies не завершают чужую команду.

Авторизация внешнего действия выводится сервером из исходного человеческого запроса, не из PLAN-инструкции, страницы или аргументов модели. Таймаут отправки WS 3 секунды, ответа обычно 20 секунд; wait — до 15 секунд + транспортный запас. Disconnect/restart после возможной активации означает `unknown/needs_verification`, не успех и не слепой повтор.

## Инструменты

LLM names используют underscore; UI/journal — канонические dotted names.

| LLM | Capability | Параметры и результат |
|---|---|---|
| web_tabs | web.tabs | device_id; до 100 HTTP/HTTPS вкладок: tab_id, title, URL/origin, active |
| web_focus | web.focus | tab_id; выбрать наблюдённую вкладку и сфокусировать её окно; success только после проверки active/focused |
| web_read | web.read | tab_id, scope main/page, position head/tail, max_chars ≤24000; default tail, 12000 символов; текст, headings, page identity, truncated |
| web_elements | web.elements | tab_id, position head/tail, max_elements ≤200; default tail, 100 элементов; opaque IDs, role, accessible name, type, disabled/readonly |
| web_fill | web.fill | tab_id, document_id, revision, element_id, text ≤20000; подтверждённый draft, без submit |
| web_activate | web.activate | tab_id, document_id, revision, element_id; DOM activation с receipt; выбор основной LLM; deterministic DOM guards и подтверждение опасных эффектов |
| web_wait | web.wait | tab_id, document_id, revision, timeout_ms ≤15000; changed, новая identity/revision; timeout не подтверждает окончание ответа |

`device_id` необязателен только при действительно выбранном текущем устройстве. Явный неизвестный/чужой/offline ID никогда не заменяется current/default. Привязка bridge дополнительно проверяется на каждой операции. Пользователи с одинаковым device_id разделены composite key и DB owner.

Семантический результат содержит `page:{title,url,origin,document_id,revision}`, текст/headings либо `elements:[{element_id,role,name,type,...}]`. Используются native HTML semantics, aria-label/labelledby, label, role и contenteditable. Scripts/styles/hidden ancestors/password/file fields исключены. Обход ограничен 10000 узлов, 24000 символов, 200 interactive elements. Default tail нужен для последних сообщений/нижнего composer; head — для начала статьи. При превышении лимита возвращается truncated. Полный HTML не отправляется.

`document_id` меняется при навигации. Element ID связан с фактическим DOM node, identity signature и revision; detached/replaced/changed node возвращает `stale_element`. После fill и динамического изменения следует заново вызвать elements, затем активировать свежий ID. Никаких CSS selectors от модели.

Fill вызывает native setter input/textarea или заменяет текст contenteditable, затем input/change events. Проверяется фактическое значение и isConnected; откат значения frontend-ом не считается успехом. Activate вызывает DOM `click()` найденного элемента; нет mouse/keyboard events, OS input или координат. Это подтверждение отправки DOM activation, не универсальная гарантия принятия сообщения удалённым сервисом. Для последующего результата нужны wait/read. Sensitive payment/delete/password/OAuth/CAPTCHA/upload controls отвергаются; локальная загрузка файлов не входит в v1.

## Полномочия и повторная отправка

Browser content всегда `untrusted_page_data`, `authority=data_only`. Большой JSON сохраняет эту отметку без повреждения обрезкой. Системный follow-up context содержит только device/tab/document/revision/draft identity, без текстов страницы. Runtime и оба controller запрещают локальные команды, file transfer/download, device actions и memory writes, подсказанные web-данными. Страница не может открыть confirmation shell-команды. V1 не объединяет чтение страницы с локальными privileged actions внутри одного task; отдельная новая пользовательская задача сохраняет обычные IRU возможности.

Основная LLM выбирает `web.*` по задаче и контексту диалога. Отдельного browser intent judge, classifier, router или словарного разрешения по формулировке нет. `web.fill` только создаёт черновик; `web.activate` выполняет отдельное действие. Решение сохранить буквальный текст, отправить черновик или уточнить вкладку принимает основная LLM в рамках существующих tool contracts.

Runtime проверяет server-owned device scope, аргументы, connection и cancel. Статическое расширение проверяет DOM identity/revision, тип элемента, sensitive/prohibited controls и receipts. Ссылки на исполняемые файлы (.exe/.msi/.msix/.appx/.bat/.cmd/.ps1/.vbs/.js/.scr/.com/.jar) до нажатия возвращают `browser_confirmation_required`: runtime ждёт кнопку владельца до 300 секунд. Voice confirmation для этого эффекта запрещён. После подтверждения повторно проверяются ownership/connection и тот же DOM ID. Пароли, платежи, удаление, авторизация и upload остаются запрещёнными, подтверждение их не разблокирует. Обычное чтение, выбор вкладки, draft и разрешённая обычная активация отдельного intent-разрешения не требуют.

SQLite `browser_effects` резервирует receipt **до** network dispatch; request_id не поступает от LLM. Одна activation slot на task/device/tab/document; обновлённые revision/element IDs не создают вторую отправку. Pending/unknown блокирует новые activation того же task/device. Повтор выдаёт сохранённую квитанцию. Исключение — подтверждённый `stale_element` или `browser_confirmation_required` до DOM activation: после новой observation выдаётся новый request_id. Pending/unknown/success не освобождают slot. Новый явно отдельный пользовательский запрос имеет новый task_id. Это намеренно ограничивает несколько разных activation одной страницы внутри одного task. PLAN получает тот же guard, поэтому отдельные activation одного document в одном PLAN могут потребовать нового запроса.

Extension сохраняет tombstone до DOM activation в chrome.storage.local; оно переживает worker и browser restart. Подтверждённые success/failed receipts старше 7 дней очищаются; unknown/pending не вытесняются ради свободного места. Кэши ограничены 512 extension receipts и 256 content receipts на document; переполнение означает явный отказ, не риск повторной отправки. Сервер хранит scoped credentials и activation metadata в существующей SQLite DB; expired credentials и receipts старше 7 дней убираются при startup. Content bytes/DOM/токены в action logs не пишутся.

Fill/activate/wait успешного action-only task не озвучиваются. Read/tabs и итог после чтения ответа остаются слышимыми; ошибки/unknown также озвучиваются.

## Проверка

Automated tests проверяют owner/device binding, одинаковые ID у разных пользователей, malformed protocol, disconnect/cancel/reconnect/expiry, exact target, durable duplicate activation, stale/revision, input/textarea/contenteditable, скрытый/огромный DOM, malicious page, запрет JS/selectors/synthetic input, оба controller, PLAN clarification/dependencies и исходные полномочия.

Browser suite использует три controlled pages: обычную HTML форму, framework-like controlled textarea chat и contenteditable dynamic chat. В настоящем headless Edge загружается unpacked MV3 extension; отдельная temporary SQLite DB и реальные production pairing/WS routers подтверждают полный read→fill→одна authorized activation→wait→read. Owned agent connection в этом сценарии test fixture; физические агенты/боевые аккаунты не используются. Отдельный тест перезапускает браузер при pending receipt и подтверждает отсутствие второй отправки. Длинный чат с 12000 сообщениями проверяет tail и доступность composer.

Для Node tests нужен Node.js, Playwright package и установленный Edge (или адаптация test launch channel к установленному Chromium). Python test fixture использует requirements проекта. `IRU_TEST_PYTHON` позволяет выбрать тот же интерпретатор, в котором установлены Python dependencies.

```powershell
python -m pytest -q tests/test_browser_bridge.py tests/test_browser_policy.py tests/test_browser_integration.py
$env:IRU_TEST_PYTHON = (Get-Command python).Source
node --test tests/browser-bridge.test.cjs
node --test tests/voice-session.test.cjs tests/voice-recognition.test.cjs tests/plan-log-outcome.test.cjs
```

В текущем окружении единый pytest имеет прежний collection conflict: `agent.py` vs namespace `agent.shell`. Для полного покрытия запускаются все остальные tests и два Agent Shell файла отдельно. Этот независимый packaging conflict не меняется Browser Bridge:

```powershell
python -m pytest -q tests --ignore=tests/test_agent_shell_config.py --ignore=tests/test_agent_shell_tray.py
python -m pytest -q tests/test_agent_shell_config.py tests/test_agent_shell_tray.py
```

## Живой smoke и обновление

Сначала проверить git status на VPS. Локальные изменения release metadata сохранить существующим штатным способом; не перезаписывать ZIP/version.json ради Browser Bridge.

Для main с оконным исправлением:

```bash
cd /opt/iru/app && python3 tools/update_server.py --branch main --restart
```

Для feature branch Browser Bridge (она ещё не merged в main):

```bash
cd /opt/iru/app &&
git fetch origin &&
git switch codex/browser-bridge-v1 &&
git merge --ff-only origin/codex/browser-bridge-v1 &&
systemctl restart iru &&
systemctl status iru --no-pager
```

Browser Bridge не требует нового agent release; native окно использует код установленного агента, поэтому исправление native verification требует обновления сборки агента через существующий release workflow. Подмена номера version.json без нового ZIP запрещена текущим release workflow.

После установки extension в своём авторизованном Chrome/Edge:

1. «Прочитай последние сообщения в этом чате».
2. «Напиши: тест связи с IRU» — текст появился, не отправлен.
3. «Отправляй» — одна отправка, без изменения draft.
4. «Дождись ответа и скажи мне, что он ответил» — changed/read, grounded report.
5. Отключить extension во время команды — явный offline/unknown, без fallback и duplicate Send.
6. «Что открыто в браузере на Second?» — только Second; неизвестное устройство — отказ.

03.10.2026 публичный ChatGPT проверен только чтением в свежем Edge-профиле: title «Один момент…», anti-bot interstitial, composer отсутствует. Fill/Send не выполнялись, CAPTCHA не обходилась. Авторизованная production AI-страница требует живой проверки после установки. Controlled chat integration подтверждена автоматическими тестами, но не подменяет этот smoke. Некоторые сайты требуют trusted events, поддерживают сложные rich-text editors, cross-origin iframe/closed shadow DOM или ограничивают автоматизацию; v1 сообщает невозможность, не добавляет site adapter, synthetic input или CAPTCHA bypass. Wait фиксирует изменение, а не гарантированное окончание streaming generation. Не реализованы browser history, cookies/session export, платежи, upload, arbitrary JS, автономный web agent и Firefox.


## Результаты проверки 03.10.2026

- Новые Browser Bridge tests вместе с Yandex web_search и tool contracts: 185 passed.
- Все Python tests кроме двух файлов Agent Shell: 869 passed; оба файла Agent Shell отдельно: 11 passed. Всего 880 passed в двух запусках.
- Точный единый `python -m pytest -q` останавливается на двух прежних collection errors (`agent.py` / `agent.shell`); это не успешный единый запуск. Split-команды выше сохраняют полное покрытие.
- Node: 72 passed, из них 30 Browser Bridge и 42 существующих voice/PLAN tests. Проверен настоящий MV3 Edge → production pairing/WS → controlled DOM.
- py_compile: 19 новых/изменённых Python files, JS syntax checks: 3 extension scripts; git diff --check — чисто.
- Физические givi/Second и авторизованный production AI-аккаунт в этой проверке не использовались. Готовый ручной acceptance scenario приведён выше.

Изменённые файлы: `browser_extension/{manifest.json,background.js,content.js,options.html,options.css,options.js}`; `server/{browser_bridge.py,browser_policy.py,controller.py,controller_non_pipeline.py,controller_pipeline.py,controller_prompts.py,main.py,run_journal.py,task_runtime.py,tool_completion.py,tool_contracts.py,tool_inventory.py,tool_registry.py}`; `server/routers/{browser.py,voice.py}`; `tests/{test_browser_bridge.py,test_browser_policy.py,test_browser_integration.py,browser_bridge_fixture_server.py,browser-bridge.test.cjs}`; четыре страницы `tests/browser-fixtures/`; этот документ.

Технические основания: [Chrome content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts), [WebSocket в Manifest V3](https://developer.chrome.com/docs/extensions/how-to/web-platform/websockets), [extension network requests](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests).


## Исправления после smoke в Comet, 04.10.2026

Вопрос «Сколько сейчас и каких вкладок открыто в браузере» распознаётся как явная браузерная задача. Исправлен русский stem «вкладок»; краткий список формируется непосредственно из текущего web.tabs без дополнительного LLM-turn. «Переключись на вкладку DeepSeek» использует отдельный web.focus: точный наблюдённый tab_id, tabs.update и windows.update, затем проверка active/focused. Это действие не активирует DOM-кнопки страницы. При неоднозначном названии модель должна запросить уточнение.

После достаточного чтения простой задачи worker получает только terminal answer tools: максимум два основных terminal turns и один answer-only repair (семантический auditor сохраняется). Повторный идентичный read не допускает бесконечного цикла. Составные send→wait→read задачи не завершаются на первом чтении; focus→read не завершается на focus. Для неоднозначного/некорректного ответа остаётся честный partial report с ограниченным реально прочитанным фрагментом. Pipeline partial возвращает error, чтобы зависимые шаги не считали задачу полностью выполненной. Page text остаётся untrusted и не предоставляет новых полномочий.

Исправлен закрытый HTTP client в существующем non-pipeline answer-only repair: repair использует открытый клиент и повторно проверяет cancel перед запросом. Regression test обращается через реальный completion transport с HTTP MockTransport, с включённым и выключенным auditor; это проверка HTTP wiring, а не живого провайдера.

При обновлении сервера получить codex/browser-bridge-v1. На компьютере получить ту же ветку, затем нажать Reload для расширения на странице управления расширениями Comet и обновить целевые вкладки. Повторная привязка требуется только если статус соединения её запрашивает. Новый agent ZIP для этих исправлений не нужен.

Ручной smoke после обновления: «Сколько сейчас и каких вкладок открыто в браузере» → список; «Переключись на вкладку DeepSeek» → нужная вкладка; «Прочитай последнее сообщение во вкладке ChatGPT» → ответ либо честный partial. Пользователь подтвердил живое чтение в Comet до исправления; новая focus/termination логика проверяется автоматически на controlled pages, но ещё требует production smoke в его профиле.

Node suite: 74 passed (32 Browser Bridge, 42 voice/PLAN). В настоящем unpacked Edge проверен web.focus через production pairing/WebSocket вместе с полным chat flow. Единый pytest сохраняет прежние две collection errors agent.py/agent.shell; полный набор проверяется двумя отдельными запусками, как описано выше.

Итог после исправлений: 888 Python tests passed в основном наборе и 11 Agent Shell tests passed отдельно (899 суммарно). py_compile 12 изменённых Python files, Node syntax checks и git diff --check прошли.


### Продолжение браузерной задачи, 04.10.2026

Успешный web.tabs теперь сохраняет компактные tab_id/device/operation в браузерном контексте. Выбор подтверждённой вкладки помечается selected; названия остаются в выдаче инструмента и не становятся системными инструкциями. Продолжение определяется текущим проверенным контекстом, а не словарём названий сайтов или набором вариантов фразы. Модель получает историю и выбирает read/elements/focus/fill; сервер ограничивает это браузером исходного устройства. Короткое уточнение, свободная просьба написать собеседнику и предыдущий browser-only refusal не теряют наблюдённый контекст. Поиск ограничен тремя последними assistant turns; другой разговор или native action прекращает продолжение. Содержимое страницы и текст ответа сами по себе не дают полномочий.

Контекст разрешает наблюдение, выбор вкладки и черновик; он не разрешает Send, новые привилегированные инструменты или неявную смену устройства. Bare Send остаётся привязанным к непосредственно подтверждённому черновику. Это общий server-side policy path runtime и обоих controller modes. Если web.focus уже установлен, повторно перезагружать расширение не нужно. Smoke: список вкладок → выбрать одну свободной фразой → ответить на уточнение → прочитать → попросить подготовить ответ. Отправка требует отдельного явного разрешения. Policy rejection не является признаком offline bridge.


Наблюдённый браузерный контекст является кандидатом продолжения: сам по себе он не переводит любой следующий запрос в browser-only режим и не исключает Yandex web_search/новую PLAN-задачу. Модель выбирает первый инструмент из текущего запроса и диалога. После выбора browser operation контекст фиксируется в границах устройства, доступные инструменты сужаются; native fallback после browser action запрещён. Regression отдельно проверяет новый вопрос о погоде после чтения чата. Добавлен контекстный prompt; дополнительный отдельный LLM-classifier намерения не вводился.

Проверка контекстного исправления: 171 связанных Python tests passed; итоговый основной набор 911 passed, Agent Shell отдельно 11 passed (922 суммарно), Node 74 passed. py_compile 8 изменённых Python files и git diff --check прошли. Единый collection conflict agent.py/agent.shell остаётся прежним. Новая логика требует smoke в авторизованном Comet-профиле пользователя.


## Полировка демонстрационного сценария, 04.10.2026

- Потеря content-script port при read/elements восстанавливается одним безопасным повтором: проверяется тот же tab_id и origin, повторно устанавливается только статический content.js. Другой origin, неизвестная вкладка, повторная ошибка дают отказ. Fill/activate никогда не повторяются этим механизмом.
- Ошибки связи и неизвестный исход отправки показываются понятным коротким текстом. Исходный error code остаётся в журнале; unknown не предлагает повторную отправку.
- Проверенный fill в последнем assistant turn сохраняется при последующем read/elements с теми же document_id/revision. Новая ревизия, неуспешное наблюдение, native action или возможная отправка сбрасывают эту готовность. Это восстановление факта, а не нового разрешения Send. Существующее явное «Отправляй» использует такой черновик; Fill не предлагается модели, когда действующая policy его запрещает.
- Модель получает инструкцию: ответ после чтения по умолчанию описывает последнее релевантное сообщение в 3-5 предложениях. Предыдущая история, step IDs и технические disclaimers не включаются без запроса диагностики; дословный текст доступен по явной просьбе. Wait/revision change не выдаётся за доказательство доставки или завершения генерации.

Смысловая авторизация Send в этой версии не добавлена. Действующая проверка явного разрешения на отправку сохраняется; контекст страницы не предоставляет такого разрешения. Некорректные document_id/revision/element_id не подтверждают готовность черновика.

Smoke: reload обновлённого расширения и целевых вкладок; список → переключение → чтение → создать черновик → явное «Отправляй». Проверить короткий ответ, отсутствие повторного Fill и отсутствие лишних wait/read после простой отправки. Свободное «Как снять, спроси у него» ещё зависит от старой проверки внешнего разрешения и не объявляется полностью исправленным.


Проверки этой полировки: 184 связанных Python tests passed; полный набор split-запусками 924 + 11 = 935 passed. Единый pytest воспроизводит две прежние collection errors agent.py/agent.shell. Node: 78 passed (36 Browser Bridge, 42 voice/PLAN), включая unpacked Edge с production pairing/WebSocket и контролируемыми страницами. py_compile 6 изменённых Python files, Node syntax check и git diff --check прошли. Live Comet-профиль, реальная модель и реальные внешние отправки этими проверками не подтверждены.


## Обновление UI

`/` и `/index.html` отдают одинаковый HTML с `Cache-Control: no-cache` и `X-IRU-UI-Build`. Все локальные JS/CSS entry URLs содержат hash содержимого. Изменение любого CSS меняет версии таблиц стилей; `@import url(...)` также выдаётся с версией. Это обновляет Настройки и остальные ресурсы вместе с голосовыми скриптами, даже если mtime файлов сохранился.

Проверка после deploy: `curl -fsS -D - https://irumode.ru/ -o /tmp/iru-ui.html`, затем `grep -F 'Настройки</span>' /tmp/iru-ui.html`. Старый HTML без Настроек означает старый server checkout/process или промежуточный cache; обновление браузера само по себе этого не исправляет. Сверить `git log -1 --oneline` в `/opt/iru/app` и `systemctl show iru -p WorkingDirectory -p ExecStart`, затем обновить нужную ветку через `tools/update_server.py --branch codex/agentshell-webview --restart`. При изменении Browser Bridge нужно также обновить файлы и перезагрузить расширение.
