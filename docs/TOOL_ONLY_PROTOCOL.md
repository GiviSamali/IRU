# Tool-Only Agent Protocol v1

Главная идея: ИРУ никогда не "просто отвечает". Даже текстовый ответ пользователю — это tool: `answer.text`.

## Зачем это нужно

- убрать ложь из stale context;
- сделать действия наблюдаемыми в run journal;
- отделить объяснение от фактов реального мира;
- требовать fresh evidence для утверждений о состоянии устройства;
- дать UI показывать used tools.

## Правила

- LLM вызывает ровно один tool за итерацию.
- Raw assistant content как финальный ответ недействителен.
- `answer.text` — terminal tool для обычного ответа.
- `answer.text` использует `answer_type`.
- `grounded_report` требует `basis`.
- `basis` ссылается на `step_id` текущего run journal.
- Старая история чата — context, но не evidence.
- Semantic auditor проверяет answer payload.
- Нельзя смешивать action tool и `answer.text` в одной итерации.

## Communication tools

- `answer.text` — обычный terminal answer.
- `answer.ask_clarification` — вопрос пользователю, когда не хватает данных.
- `answer.report_failure` — terminal failure report.
- `answer.request_confirmation` — запрос подтверждения перед рискованным действием.

## Run Journal

Каждый tool result попадает в journal. Важные поля:

- `step_id` — текущий идентификатор шага, например `step_1`;
- `tool_name` — canonical tool name, например `window.find`;
- `status` — результат выполнения;
- `summary` — компактное описание;
- `result` — payload tool result.

`basis` в grounded answer должен ссылаться на `step_id`, а не на имя инструмента.

## Example

Пользователь:

```text
Проверь, открыт ли Блокнот
```

Tool iteration:

```text
step_1: window.find {"title_contains": "Блокнот"}
```

Terminal answer:

```json
{
  "answer_type": "grounded_report",
  "text": "Блокнот сейчас не найден.",
  "basis": ["step_1"],
  "self_check": {
    "has_sufficient_evidence": true
  }
}
```

## Anti-patterns

- Raw final text без `answer.text`.
- `answer.text` вместе с action tool в одной итерации.
- `basis: ["window.find"]` вместо `basis: ["step_1"]`.
- Использование предыдущего run как evidence для текущего состояния.
- Утверждение "окно открыто" без свежего `window.find` / `window.verify`.
