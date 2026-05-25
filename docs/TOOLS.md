# Tools и Tool Registry

Tool Registry описывает доступные capabilities для LLM и controller. Он нужен, чтобы модель выбирала typed tools вместо произвольного shell fallback и чтобы UI мог показывать used tools.

## Зачем нужен Tool Registry

- перечисляет доступные инструменты по категориям;
- задает canonical names;
- хранит compact metadata: purpose, when-to-use, danger, returns;
- дает schemas для LLM tool calls;
- помогает controller формировать compact tool context;
- дает summary для run journal и UI.

## Typed tools vs fallback

Typed tools имеют явный контракт и структурированный result. `execute_cmd` остается низкоуровневым fallback.

Policy:

```text
typed tool first
execute_cmd only when typed tool is unavailable or insufficient
```

Это особенно важно для:

- activation;
- Python runtime;
- file writes;
- device state;
- window/app observation;
- GUI launch verification.

## Категории

### answer.*

- `answer.text`
- `answer.ask_clarification`
- `answer.report_failure`
- `answer.request_confirmation`

Communication tools завершают iteration/run и создают пользовательский ответ без raw final assistant content.

### device.* / runtime

- `device.get_passport`
- `device.refresh_state`
- `device.activate`
- `device.repair_activation`
- `device.check_runtime`
- `device.prepare_runtime`
- `device.repair_runtime`

Эти tools работают с activation, runtime и state cache.

### File tools

- `write_content`
- `get_file_content`
- `list_dir`

Для создания текстовых файлов предпочтителен `write_content`, а не shell heredoc/echo/Set-Content.

### window.*

- `window.list`
- `window.find`
- `window.verify`
- `window.focus`
- `window.close`

Window tools дают evidence о реальных top-level OS windows.

### app.*

- `app.launch`
- `app.verify_launch`
- `app.close`

App tools запускают приложение и проверяют результат через process/window evidence.

### execute_cmd

`execute_cmd` — fallback для задач, где нет typed tool или typed tool недостаточен. Он должен проходить через policy/budget/confirmation слой.

## Tool result и run journal

Каждый вызов записывается в run journal:

- `step_id`;
- `tool_name`;
- `status`;
- `summary`;
- `result`.

UI использует этот журнал для used-tools transparency. Пользователь видит, какие tools были применены, например `device.refresh_state` или `window.find`.

## Практическое правило

Если задача формулируется как "проверь", "открой", "активируй", "подготовь runtime", "запиши файл", "найди окно", сначала ищется typed tool. Shell fallback должен быть объяснимым исключением, а не default.
