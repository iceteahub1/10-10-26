# Shared Phase 4 AI reliability

Base: latest main `ea303ae1c920bb2d244e6b5dccdd0496f7754a16` (flexible model
selection merged). This fix is independent of model size. It changes the shared
AI boundary, not the FSM, SQLite services, migrations, inventory rules, receipts,
installer, Windows launchers, or browser dashboard. No Phase 5 work is included.

## Root cause

The old model-facing `add_item_to_order` and `view_current_order` schemas required
`session_id`, and confirmation required a model-supplied review token. Python's
dispatcher correctly rejected a made-up session ID, but the schema invited the
model to invent it. The old tool loop also required rigid final `reply`/`facts`
JSON to acknowledge an action already committed by SQLite. Invalid follow-up
output returned an application error without explaining the saved cart, leaving
the user uncertain whether an order had changed. Equivalent serialized arguments,
fenced JSON, and other valid envelopes were not handled consistently.

The Windows raw-response/error log was not supplied, so the precise subsequent
response shape cannot be identified. These boundary defects are reproduced with
controlled outputs, including the reported fake-session + invalid-follow-up path.
No claim is made that the fix was run against a real Qwen model here.

## Behavior and safeguards

- New model-facing intent schemas omit session/database IDs and review tokens.
  Item/portion intent uses exact menu names and current customization text.
  Python resolves these against SQLite; ambiguous portions require clarification.
- Python binds/resolves a session using the customer's explicit table number.
  Missing, conflicting, negated, or invalid table references prompt/return clear
  feedback instead of guessing. Bare numbers are accepted only after asking for
  the table. English/Filipino number words 1–10 following table/mesa and explicit
  numeric references are recognized. An initial ordering request is retained
  while asking for the table and sent once after the binding is established.
- The adapter accepts native tools with object/serialized JSON arguments, fenced
  JSON, structured `calls`/`tool_calls`, single calls, and call arrays. Exact
  integer strings can be normalized, but booleans/fractions are not quantities.
  Duplicate keys, unexpected fields, unknown tools, invented items, invalid
  references, prices, inventory/state overrides, and ungrounded prose are rejected.
- Native tools fall back only when Ollama explicitly reports them unsupported.
  Saved structured mode gets schemas from its first request. Both transports use
  the same parser, identifier binding, strict dispatcher and FSM.
- Legacy model-supplied session/token strings are discarded; Python supplies the
  current binding and prior-review token. Legacy menu/line references are accepted
  only if present in authoritative menu/current-customer cart data. They never
  authorize another session or create a database identity. The direct trusted
  dispatcher retains its existing strict validation and session isolation.
- Entire batches are normalized/validated before effects, including duplicate
  detection. Canonical per-turn fingerprints survive corrective retries, so a
  repeated call with a different fabricated ID cannot repeat a saved mutation.
  Failed schema output has bounded retries (existing default one, maximum two).
  Timeouts do not automatically replay actions. Distinct tools retain existing
  per-action transactions: a later business-rule failure can leave an earlier
  action saved, which is reported explicitly.
- Customer-visible acknowledgements are generated from Python tool results and
  the current SQLite cart/receipt. A malformed/empty/timed-out model follow-up
  cannot suppress the successful action's real quantity, total, or receipt.
  Saved actions are reported separately from the failed follow-up; no AI prose
  is used to invent results. If a database read itself fails, recorded tool
  messages are shown with a warning to inspect saved results before retrying.
- Checkout still needs direct customer consent and a previously displayed review.
  Python owns the token. Edits and any new review in the same turn revoke that
  turn's confirmation permission. The FSM still rejects changed carts, invalid
  state, cancelled orders, and duplicate inventory deductions.

## Backward compatibility

Existing direct dispatcher/FSM/service contracts, persisted data, model preferences,
terminal CLI, installer and launchers are retained. No application model size is
hardcoded by this fix. Existing tests were not removed, edited, weakened, or given
new skip conditions.

The AI result object retains `ok`, `response`, `source`, `inference_verified`,
`tools`, and `snapshot`, and adds `partial_success`. `ok=false` still means the
whole turn was incomplete/failed; `partial_success=true` says at least one tool
succeeded and the authoritative acknowledgement is in `response`/`tools`.
Consumers must inspect this before asking a customer to repeat a request.
Successful action acknowledgements use `source=application_results`; genuine
model-only grounded clarifications keep `source=ollama`; errors keep
`source=application_error`. Pre-inference table prompts are explicitly labeled
`application_prompt`, never simulated model inference.

## Automated validation

Full suite: `.venv/bin/python -m unittest discover -s tests -q`.
**179 tests run: 178 passed, one existing PowerShell simulation skipped because
PowerShell is unavailable.** No new skips were introduced. All 146 baseline tests
are preserved unchanged. New model behaviors are mocked; SQLite/session/order/
receipt operations and the local HTTP dashboard regression are real on Linux.

New regressions cover the reported failure under three model labels, protected
identities absent from schemas, string arguments/quantities, JSON envelopes,
nullable Ollama fields, duplicate keys, empty/malformed recovery, bounded retries,
unsupported native tools, configured structured transport, fabricated sessions,
item/allergen/stock tools, ambiguous portions, table prompting/resolution,
duplicate calls, timeouts, partial business-rule success, feedback read failure,
review tokens/direct consent, same-turn review/edit guards, and the acceptance
script itself with mocked inference and a real disposable SQLite database.

## Repeatable Windows acceptance with real installed models

1. Run `setup.bat` normally. Start Ollama. Use `configure_ai.bat` and `ai_chat.bat`
   for the selected model as usual. Model preference changes remain explicit.
2. In a terminal in the extracted project folder, run the optional test for an
   **already installed** exact tag (examples below; any compatible tag is allowed):

   ```bat
   .venv\Scripts\python.exe scripts\verify_phase4.py --model qwen2.5:3b
   .venv\Scripts\python.exe scripts\verify_phase4.py --model qwen2.5:7b
   .venv\Scripts\python.exe scripts\verify_phase4.py --model YOUR_INSTALLED_QWEN_TAG --transport structured
   ```

3. The script creates/deletes a temporary database, asks for a table without
   inference, then exercises `Dalawang Chicken Adobo po.`, table 3, total, review,
   withholding consent, and explicit confirmation. It checks two portions only,
   the actual total, the persisted receipt, and stock deducted exactly once only
   after consent. It never downloads a model or changes preferences/real inventory.
4. Record the tag, Ollama version, Windows version, RAM/VRAM, selected transport,
   output, and exit code for each model. `--transport native` chooses native first
   and may fall back if explicitly unsupported. The final report shows transport.
   A PASS with degraded-follow-up warnings verifies saved actions/feedback, not
   flawless model responses. Inspect warnings; never blindly replay the request.
5. For manual fault checks, stop Ollama during a follow-up, then restart it and
   inspect `state`. A saved addition must remain exactly two portions. Request a
   fresh review before confirmation after any edit. Try ambiguous customization,
   out-of-stock amounts, and an unrelated message during review; no checkout may
   occur without direct consent.

**Remaining limitations:** real Windows and real Qwen/Ollama execution have not
been performed in this environment. Language/intent interpretation is still
model-dependent; valid schemas cannot guarantee perfect intent accuracy. Tables
are local session bindings, not authentication; ambiguous existing sessions need
trusted local resume controls. Unbound pending requests last for that process,
while bound conversation history remains persisted. The backend reports partial
success rather than promising cross-tool atomicity. Model-only free-form facts
remain blocked, and some final responses can still exhaust retries; completed
actions remain visible through authoritative feedback. Browser ordering/chat
remains unimplemented.
