# Tool Contract / Tool Template v1

Tool Contract v1 - это внутренний контракт описания инструмента ИРУ. Он не добавляет пользовательские инструменты и не меняет выполнение tools. Его задача - зафиксировать, что каждый активный или будущий tool обязан описывать до того, как он попадет в tool registry, UI, sandbox/review pipeline или user tools.

ИРУ использует typed tools как основной способ работы: модель выбирает инструмент, controller выполняет его, run journal сохраняет шаги, UI показывает used tools и evidence. `execute_cmd` остается fallback, но не должен становиться предпочтительным путем там, где есть typed tool.

## Зачем нужен контракт

Контракт нужен, чтобы у каждого инструмента были явные:

- identity и canonical name;
- purpose и условия применения;
- input/output schema;
- permissions и risk level;
- side effects;
- evidence contract;
- timeout и idempotency;
- cleanup/rollback notes;
- examples и test plan;
- правила отображения в UI.

Это foundation для будущих этапов: `tool.propose`, sandbox/review/test pipeline, user tools, UI tool details, cost visibility per tool. Эти этапы не реализованы в v1.

## Типы инструментов

### Typed tool

Typed tool имеет явную schema, canonical name и структурированный result. Примеры: `write_content`, `device.refresh_state`, `window.verify`, `app.launch`.

Typed tools должны использоваться раньше shell fallback, потому что они дают понятный result и evidence для run journal.

### Fallback `execute_cmd`

`execute_cmd` - низкоуровневый fallback для случаев, когда typed tool отсутствует или недостаточен. Он требует повышенного внимания: команда может иметь непредсказуемые side effects, а evidence часто менее структурированное.

### Helper script

Helper script - временный скрипт, созданный для выполнения конкретной задачи, например редактирования Office-документа. Он не является tool contract сам по себе. Если такой сценарий повторяется, его можно позже вынести в typed tool.

### Tool proposal

Tool proposal - будущий объект предложения нового инструмента. В v1 нет UI, storage или исполнения proposals. Контракт только задает форму, к которой proposal должен будет привести будущий tool.

### Future user tool

User tool - будущий пользовательский инструмент, прошедший review/test/sandbox pipeline. В v1 user tools не создаются, не грузятся динамически и не исполняются.

## Required fields

`ToolContract` содержит:

- `name`
- `canonical_name`
- `aliases`
- `category`
- `tool_type`: `system`, `typed`, `answer`, `fallback`, `proposal`
- `label`
- `purpose`
- `when_to_use`
- `when_not_to_use`
- `input_schema`
- `output_schema`
- `returns`
- `permissions`
- `risk_level`
- `side_effects`
- `evidence`
- `timeout_sec`
- `idempotency`
- `cleanup`
- `rollback`
- `examples`
- `test_plan`
- `ui`
- `version`
- `status`: `active`, `experimental`, `deprecated`, `hidden`

## Risk levels

Allowed risk levels:

- `safe`
- `read_only`
- `write`
- `runtime`
- `process_start`
- `process_control`
- `network`
- `destructive`
- `confirmation_required`
- `fallback`

Risk level is descriptive in v1. It does not enforce permissions yet.

## Permissions

Permissions are explicit strings such as:

- `tool_registry.read`
- `memory.read`
- `device.read`
- `file.write`
- `runtime.manage`
- `window.observe`
- `process.start`
- `process.control`
- `shell.execute`
- `answer.emit`

Permissions are observability metadata in v1. They are not an enforcement layer yet.

## Evidence contract

`evidence` describes what the tool produces and what is required before ИРУ may make a real-world claim.

Fields:

- `produced`: evidence artifacts produced by the tool;
- `required_for_claims`: evidence that must exist before claiming success;
- `fresh_run_required`: whether current-run evidence is required.

Example: `window.verify` produces `window_match` and `visibility_status`; claims like "окно видно" must be based on current-run tool result, not old chat history.

## Side effects

Side effects document what can change:

- `write_content` creates or overwrites a file;
- `device.prepare_runtime` modifies managed runtime files;
- `app.launch` starts a process;
- `window.close` changes window/process state;
- `execute_cmd` depends on the command.

## Idempotency

Allowed values:

- `idempotent`
- `safe_repeat`
- `not_idempotent`
- `unknown`

Examples:

- `device.get_passport` is idempotent;
- `device.prepare_runtime` is safe_repeat;
- `write_content` is not_idempotent because repeating it can overwrite user-visible content;
- `execute_cmd` is unknown.

## Cleanup and rollback

V1 stores cleanup/rollback notes but does not execute rollback automatically.

Future pipeline stages may use these fields for:

- sandbox cleanup;
- generated helper script cleanup;
- temporary artifact cleanup;
- review before destructive actions.

## Existing tool example: `write_content`

```json
{
  "name": "write_content",
  "canonical_name": "write_content",
  "aliases": [],
  "category": "files",
  "tool_type": "typed",
  "label": "Запись файла",
  "purpose": "Create or overwrite a text file without shell escaping",
  "when_to_use": ["create txt/json/py/html file", "write text to a file"],
  "when_not_to_use": ["binary files", "Office documents", "when shell execution is specifically needed"],
  "permissions": ["file.write"],
  "risk_level": "write",
  "side_effects": ["creates_or_overwrites_file"],
  "evidence": {
    "produced": ["file_path", "write_status"],
    "required_for_claims": ["file_path"],
    "fresh_run_required": true
  },
  "idempotency": "not_idempotent",
  "ui": {
    "show_in_used_tools": true,
    "show_details_by_default": false,
    "sensitive_fields": []
  },
  "version": "v1",
  "status": "active"
}
```

## Future candidate example: `office.create_docx`

This is an example only. It is not an active production tool in v1.

```json
{
  "name": "office.create_docx",
  "canonical_name": "office.create_docx",
  "aliases": [],
  "category": "office",
  "tool_type": "proposal",
  "label": "Create Word document",
  "purpose": "Create a .docx document from structured title, sections, and paragraphs",
  "when_to_use": ["user asks to create a Word document", "task needs repeatable Office document output"],
  "when_not_to_use": ["binary patching", "editing an existing protected document without confirmation"],
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {"type": "string"},
      "title": {"type": "string"},
      "sections": {"type": "array", "items": {"type": "object"}}
    },
    "required": ["path", "title", "sections"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "path": {"type": "string"},
      "created": {"type": "boolean"},
      "file_size": {"type": "integer"}
    }
  },
  "returns": "created .docx path and validation summary",
  "permissions": ["file.write"],
  "risk_level": "write",
  "side_effects": ["creates_docx_file"],
  "evidence": {
    "produced": ["file_path", "file_exists", "file_size"],
    "required_for_claims": ["file_path", "file_exists"],
    "fresh_run_required": true
  },
  "timeout_sec": 60,
  "idempotency": "not_idempotent",
  "cleanup": "remove temporary helper scripts after execution",
  "rollback": "delete generated file only if it was created by this run and user approved rollback",
  "examples": [
    {"input": {"path": "C:/Users/user/Desktop/brief.docx", "title": "Brief", "sections": []}}
  ],
  "test_plan": ["create docx in temp folder", "verify file opens", "verify no helper scripts remain"],
  "ui": {
    "show_in_used_tools": true,
    "show_details_by_default": false,
    "sensitive_fields": []
  },
  "version": "v1",
  "status": "experimental"
}
```

## What v1 intentionally does not implement

V1 does not implement:

- `tool.propose`;
- database tables for proposals;
- user custom tools;
- dynamic loading;
- sandbox execution;
- automatic code generation;
- permissions enforcement;
- billing or wallet enforcement;
- changes to DeepSeek configuration;
- larger prompt payloads with full schemas.

`system.list_tools` remains compact. Full contracts are internal and should not be dumped into normal model/user responses.
