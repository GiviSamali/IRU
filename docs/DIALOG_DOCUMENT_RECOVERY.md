# Chat 91: dialogue and document recovery

This local fix follows the 2026-10-20 read-only audit of task
`d2b41af1-74ee-46ed-b1d3-0b515b1fd371`. Historical tasks and documents are not modified.

## Changes

- Pipeline receipts reuse WorkerReport positive-evidence checks. A failed or
  unknown write cannot contribute requested paths as created/verified artifacts.
- The existing shared write-path check rejects unresolved shell/home expressions
  before dispatch. It never expands them on the server. Resolve the directory
  on the assigned device, then pass the literal path through normal policy checks.
- PLAN Worker requests use DeepSeek disabled thinking for structured tool output
  from the first iteration. Ordinary Worker reasoning is unchanged. Token and
  iteration budgets are unchanged; the existing single truncation recovery remains.
- PLAN write schemas and dispatch limit content to 4000 characters per operation.
  Prompts require small complete sections, assembly, then document verification.
  This is a per-call bound, not a limit on the final document. Unknown outcomes
  retain the existing mutation/replay block, including append operations.
- Running-task presentation uses stored PLAN steps and avoids stale cached generic
  summaries. Status questions do not start another Worker or regenerate research.
- Orchestrator instructions preserve explicit requirements, discourage repeating
  rejected suggestions, and distinguish proposed demonstrations from verified abilities.

## Acceptance after separate LIVE authorization

1. Ask for the saved report of the failed task. Verify the full existing text is
   returned without new searches. Treat market claims as source material to check.
2. Request a separate continuation using that report. The old task stays failed.
3. During execution, ask the current stage. Completed research and the active
   document stage must appear; no new Worker should be admitted for the question.
4. Verify helper paths are resolved on the assigned device. Inspect completed
   writes, assembly, actual Word/PowerPoint paths, existence and content.
5. Reject an idea and ask for a faster concrete alternative. Check that the reply
   changes the proposal and retains the speed requirement. Prompt changes alone
   cannot establish real dialogue quality.
6. Cancel or disconnect during a mutation. No automatic replay or alternative
   mutation may follow an unknown result. Existing read-only recovery remains.

Only server files need deployment; no agent/UI rebuild is required. Real document
generation and provider behaviour have not been LIVE-tested by this local change.
