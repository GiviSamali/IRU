# Token budgets and context compaction

Status: implementation branch; critical isolated tests passed. No LIVE run
or production migration performed for this change.

## Budgets

The complete text Chat Completions request is measured at the shared request
boundary, after thinking-history preparation: system instructions, messages,
tool calls/results and tool schemas. A separate output reserve and 1,024-token
window margin are included in the endpoint-window check.

Defaults (optional fields of the existing `server/llm_config.json`):

```json
{
  "orchestrator_input_tokens": 16384,
  "orchestrator_expanded_input_tokens": 32768,
  "worker_input_tokens": 65536,
  "worker_expanded_input_tokens": 131072,
  "max_tokens": 32768,
  "context_compaction_enabled": true
}
```

Expansion is based on the required request size after attempting useful
compaction, not keywords. Unknown/custom endpoints require an explicit
`context_window_tokens` to enable expansion beyond the normal budget.
The official DeepSeek V4/Flash endpoint uses its documented 1M window.
No other provider/model is assumed to have that window.

Counters are deliberately labelled estimates. A cached cl100k tokenizer is
used with a 25% margin; it is NOT the DeepSeek tokenizer. Provider `usage`
remains the actual count. If tokenizer/cache is absent, UTF-8 bytes plus
framing allowance provide a conservative bound. That fallback can stop a
request earlier than a model-compatible tokenizer would. This is visible in
`llm_usage_events.metadata.context_budget`, not silently reported as exact.

Install updated server requirements and provision the tokenizer cache under
the service's user/environment before deployment (this downloads tokenizer
data, not models and makes no LLM call):

```sh
/opt/iru/venv/bin/pip install -r /opt/iru/app/requirements.txt
/opt/iru/venv/bin/python -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")'
```

Keep `TIKTOKEN_CACHE_DIR` consistent between provisioning and the service if
set. Tokenizer state is cached in the process; restart after provisioning.
Normal request processing does not intentionally download tokenizer assets.

## What is compacted

At roughly 75% of the normal input budget, sufficiently large old material
can be summarized using one tool-free base-model call (2,048 output tokens,
thinking disabled). It runs on demand, not in a background process. Small
prefixes are not summarized just because fixed instructions are large.

Orchestrator retains six recent nonempty dialogue messages and the current
request, all structured jobs, devices and facts. Its old dialogue plus prior
summary is compressed; new turns load that checkpoint and uncovered messages.
The old 5,000/14,000-character history/package caps are removed.

Worker retains system instructions, the literal current request, structural
handoff (scope, source references, interpretation) and at least six recent
messages. Older complete assistant/tool groups can be compacted together.
The size target is approximately 55% of the normal budget, best effort:
pinned instructions and recent results are never removed to hit a number.
There is no new smaller character cap on the assembled handoff. Per-field
transport bounds and the existing Orchestrator decision schema remain.

Runtime journal, permission checks, confirmations, cancellation, dependencies,
WorkerReport and artifact receipts are not replaced by model notes. Summary
and archive text are explicitly historical data, never permission or new-run
proof. Cancellation is checked again after the summary call.

If compaction fails, is truncated, increases size or cannot be saved, the
original input remains intact. Worker disables further summary attempts for
that run; expansion may still permit the original input. Beyond the hard
budget, the run stops explicitly without sending an oversized request.
Restricted browser/window routes are not given extra tools for retrieval;
when they lack the archive-reading tool, their history is not compacted.

## Storage and recovery

An additive `context_checkpoints` table is initialized lazily in the existing
SQLite database. It is scoped by authenticated owner, chat and contour/task.
It has a chat foreign key with cascading deletion. No vector store or second
database is introduced. Source digests detect corrupted archives.

Conversation checkpoints store source message IDs and a previous-checkpoint
link, not another copy of all dialogue. Worker checkpoints store displaced
message groups and their full selected journal entries before removal from
model input. Current-run evidence indices retain step IDs and outcome fields.

The existing `system_get_last_run_summary` accepts:

- `context_checkpoint_id`, plus `report_offset`: archive pages;
- `context_step_id`: one exact archived journal step of the current Worker;
- `context_before_message_id`: older dialogue references in this owned chat;
- `context_message_id`, plus `report_offset`: the original selected message.

Worker archives cannot be read as another task's current-run evidence. Raw
Worker messages are not exposed by dialogue retrieval: use the existing
device-scoped source-report path. Checkpoint source records remain data only.
Page offsets/character limits are transport controls, not model-input budgets.

The conversation read window has a 1,000-row resource ceiling. An older gap is
marked, retained in checkpoint metadata and can be read by message ID; absence
from the request is not evidence that earlier context never existed. A giant
single source beyond the summary-call budget is not silently clipped: summary
fails safely and normal hard-budget handling applies.

## Validation

Critical isolated tests cover token boundary/output reserve handling, stopping
before a provider call, tool-pair preservation, summary failures/no reduction,
ownership/chat/task isolation, deletion and checkpoint recovery. Related
Orchestrator/Worker, routing, lazy-context, LLM compatibility and historical
result tests also ran. Old character-cap assertions were migrated to the new
collection-versus-request-boundary contract. The complete suite was not run.
No real document/device actions or live LLM requests were used for validation.
LIVE quality, cost and latency of summaries remain unverified.

Provider references: [DeepSeek model window](https://api-docs.deepseek.com/quick_start/pricing/),
[token counting and authoritative API usage](https://api-docs.deepseek.com/quick_start/token_usage/).
