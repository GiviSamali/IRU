# Архитектура ИРУ

ИРУ построена как AgentOS/Agent Control система: пользователь работает через UI, сервер оркестрирует задачу, LLM выбирает следующий инструмент, а локальный агент выполняет действия на устройстве.

## Общая модель

```
User
  |
  v
Web UI
  |
  | HTTPS / REST / browser session
  v
FastAPI Server
  |
  | LLM API + Tool Registry
  v
Controller loop
  |
  | WSS command/result
  v
Local Agent
  |
  v
Device OS / Files / Windows / Python Runtime
```

Роли:

- UI — интерфейс управления, Device Passport, used-tools transparency, pipeline progress.
- Server — координирующий слой, auth/API/WebSocket hub, временное зеркало device state, controller loops.
- Agent — локальный исполнитель и владелец локального состояния устройства.
- LLM — выбирает следующий tool по protocol и context, но не выполняет действия напрямую.
- Tool Registry — карта доступных capabilities, схем, категорий и compact summaries.

## Поток команды

```
User message
  -> controller
  -> tool selection
  -> server-side tool or agent-side tool
  -> result
  -> next tool
  -> answer.text
```

Controller не должен принимать raw final assistant content как пользовательский ответ. Обычный текстовый ответ тоже является terminal tool call: `answer.text`.

## Server-side и agent-side tools

Server-side слой отвечает за orchestration, validation, journal, summaries, dispatch и API. Agent-side слой отвечает за локальную работу: файлы, shell fallback, runtime, окна, приложения, state snapshot.

```
LLM tool call
  |
  +-- answer.*              -> server terminal answer
  +-- system.list_tools     -> server registry summary
  +-- device.get_passport   -> server compact passport
  +-- device.refresh_state  -> server dispatch -> agent action
  +-- window.* / app.*      -> server dispatch -> agent action
  +-- write_content         -> server dispatch -> agent action
  +-- execute_cmd           -> server dispatch -> agent fallback
```

## Control surfaces, not pseudo-OS tools

ИРУ управляет средами через реальные control surfaces: PowerShell, cmd, Python, browser, window tools, application APIs, local agent и external APIs. Не нужно вводить псевдо-язык для обычных OS-операций вроде открыть папку, скопировать файл или переименовать объект.

Обычные действия на ПК должны идти через короткий `execute_cmd` с явной проверкой результата, например через `window.find`/`window.verify`. Typed tools стоит добавлять только там, где они дают реальное преимущество: safety contract, evidence normalization, сокращение большого контекста, сложную document/app automation, дорогую повторяемую логику или non-shell capabilities.

## Два режима

### Non-pipeline

Non-pipeline mode — короткий agent loop для прямых задач. LLM вызывает один tool за итерацию, сервер записывает результат в run journal, затем LLM выбирает следующий tool или завершает через `answer.text`.

Используется для коротких проверок, file tools, window/app observation, runtime/state actions и простых пользовательских вопросов.

### Pipeline

Pipeline mode — режим для многошаговых задач. Он включает план/шаги/восстановление/итог, но финальный пользовательский ответ все равно должен идти через `answer.text`. Для recovery важны сохранение неудачных команд, artifacts, step status и итоговая семантика вроде `completed_with_recovery`.

## Почему не просто commands

ИРУ уходит от модели "LLM пишет shell-команду и пересказывает результат".

Причины:

- typed tools дают стабильные контракты для файлов, окон, runtime, activation и state;
- fresh evidence привязывает вывод к текущим tool results;
- run journal сохраняет `step_id`, tool name, status, summary и result;
- UI показывает used tools, поэтому пользователь видит, чем реально пользовалась система;
- semantic auditor проверяет answer payload и не дает выдавать stale context за факт.

## Минимальная диаграмма execution loop

```
Controller iteration
  |
  +-- build tool context
  +-- call LLM
  +-- require exactly one tool
  +-- validate tool args / policy
  +-- execute or dispatch tool
  +-- append run journal entry
  +-- continue until terminal answer.*
```

## State ownership

```
Local Agent IRU_HOME/state
  |  activation/runtime/state/passport receipts
  v
Server memory / DB summaries
  |  compact mirror while connected
  v
UI Device Passport
```

Источник локальной правды — агент. Сервер может хранить compact summaries и live mirror, но после restart сервера offline cache недоступен до reconnect агента.
