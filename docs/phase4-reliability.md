# Shared Phase 4 AI reliability — PR #7 follow-up

This update stays on `bugfix/model-agnostic-ai-reliability` and updates the existing
draft PR #7. Its main baseline is `ea303ae1c920bb2d244e6b5dccdd0496f7754a16`.
It changes the shared AI boundary and acceptance tests. The FSM, transactions,
SQLite schema/data, receipts, menu/allergen/stock services, model preferences,
installer, Windows launchers, and dashboard remain intact. No Phase 5 work.

## Root cause

The latest Windows follow-up revealed an intent/completion gap: exact additions
had goals, but natural-language totals/review requests did not. Such requests
received broad state-valid tools, and the first successful lookup could end the
turn. Thus a menu lookup could wrongly fulfill a cart-total or review request.

For requests without an established goal, the same selected Qwen client now makes
a non-executing structured semantic classification first. It understands the
current English/Filipino/Taglish request using bounded, ID-free context. Its strict
allowlisted result selects the action scope; it supplies no action arguments,
session identifiers or permission. A cart-total goal exposes only cart viewing;
a review goal exposes only review. Unrelated tools are rejected before dispatch
and corrected within the existing shared retry bound. Classification errors/timeouts
never dispatch actions. Both native and structured action modes use these goals.
Complex multi-operation requests retain general tool interpretation; ambiguous
classifications require clarification. No customer phrase or model size is coded
into this semantic routing. The old exact-addition guard remains a supplementary
completion/safety check, not the natural-language ordering engine.

Direct checkout consent still comes from Python's strict existing confirmation
check, never from model classification. Without a previously displayed review,
checkout is rejected before inference. With a review, only checkout is exposed;
successful checkout is acknowledged immediately. Repeating direct confirmation
reads the saved receipt without inference or another stock deduction. For unknown
natural-language requests, classification adds one purposeful model call before
tool routing; no acknowledgement-only call is added afterward. Explicit client
adapters/test doubles now need `classify_request` as well as `chat`.

The follow-up test of commit `305f962` exposed a further boundary defect: every
inference still received the full static intent catalogue, including existing-line
quantity/removal/customization tools even when the cart was empty. Their short
descriptions did not clearly distinguish adding NEW portions from setting an
EXISTING portion's quantity. The reported "Which exact cart portion" error comes
from resolving an existing-line edit against an empty cart; that validation was
correct, but it happened after Qwen was offered an inapplicable operation. The
exact edit tool name and internal model reasoning require the missing raw trace.

The model-facing catalogue is now recomputed from authoritative FSM/cart state on
each inference/retry, and checked again before dispatch. An empty cart excludes
all line edits and review/checkout. A clearly established addition puts add first,
retains menu/stock/allergen/cart reads, and excludes edit/cancel/review/checkout.
Generic or ambiguous language gets state-appropriate tools rather than a guessed
mutation. Direct consent to a displayed review exposes only checkout and reads.
Both native tools and structured schemas use this policy; stale schemas are not
retained in the conversation. Unavailable selections are rejected before identity
binding, with bounded corrective feedback. Invalid batches have no effects.

This is an affordance filter, not a deterministic ordering engine. Qwen still
interprets natural language and supplies every action/argument; Python never
converts a wrong update into an addition. No item names or model sizes are coded
in the policy. The existing conservative completion grammar only tracks exact
unconditional requests; unknown/customized wording remains model-interpreted in
bound conversations, and ambiguous retained wording still requires restatement.
The overly broad "I want" completion hint was narrowed so wanting customization
does not falsely demand an addition. Successful saved tools still get immediate
Python/SQLite acknowledgements without another model call.

The first PR #7 fix removed model ownership of protected identifiers, but left
three defects: the pending request was cleared immediately after session binding;
the loop had no completion ledger linking a requested addition to its successful
tool result; and every tool batch still required a subsequent model-generated
JSON acknowledgement. Consequently `view_current_order` could replace the food
addition, and an empty follow-up then failed JSON parsing. Error status counted
any successful tool, including a read-only cart view, as partial order success.

The shared prompt mixed native tool routing with required final JSON. It asked
for a fragile, unnecessary acknowledgement after a saved action. We can reproduce
that protocol failure with controlled native, empty, malformed and structured
responses. Without the raw Ollama trace or real execution here, we cannot prove
why a particular Qwen run emitted empty content (template/runtime/intent behavior).
The fix removes that unnecessary inference step, rather than requiring a model to
produce perfect prose. No size-specific behavior, model switch or download is used.

## Behavior and safeguards

- A pending request survives the table question and remains outstanding until a
  matching validated tool succeeds. Python binds table/session deterministically.
  `Dalawang Chicken Adobo po.` + `Table 3 po.` gives the model the original request,
  authoritative table context and an explicit outstanding-addition goal.
- Completion tracking recognizes only full unconditional quantity + exact menu
  name clauses, simple menu requests, and conjunctions of those clauses. Quantity
  digits and common English/Filipino words are supported. Tracking never executes
  tools. A model must still propose a valid matching action through the original
  strict dispatcher, stock checks and FSM. Wrong quantities, changed instructions
  and unrelated mutations cannot substitute for a recognized addition.
- Ambiguous, conditional or customized requests retained across a table question
  require restatement. A table answer containing changed intent also requires
  restatement. A correction before the table answer replaces the earlier request.
  These restrictions prevent old history from silently authorizing an addition.
  Complex requests in an already-bound conversation still use model intent and
  the existing validators. Clear addition intent cannot be fulfilled by a read.
- Successful tools produce authoritative Python/SQLite acknowledgements immediately.
  Further inference is allowed only for known outstanding operations, with the
  existing call/retry bounds and per-turn duplicate fingerprints. A read preceding
  an outstanding addition may lead to another inference; a finished addition does
  not need another inference solely to say it succeeded. A rejected action stops
  with the actual failure and unfinished-request feedback; it is not blindly retried.
- Native tool calls, serialized arguments and structured JSON use the same parser,
  identity binding and strict dispatch. Fenced JSON, single calls, arrays and
  `calls`/`tool_calls` envelopes remain supported. Unsupported native tools switch
  to structured mode. Invalid/empty output can use the bounded corrective retry
  in structured mode, with explicit schemas and only unfinished work requested.
  Fallback normalizes saved native call/result history to structured JSON data,
  avoiding a mixed native-tool/JSON conversation protocol.
  No output can bypass argument validation or replay a previous action.
- A cart view followed by empty/non-JSON output reports an incomplete order,
  names the addition that was NOT executed, and reports `partial_success=false`.
  Saved mutations remain visible if another part fails. Raw model prose cannot
  invent an acknowledgement or suppress saved quantities, totals or receipts.
- Session IDs, database identities, prices, inventory, order state and review
  tokens remain Python-owned. Checkout still requires direct consent and a
  previously displayed review. Same-turn review/edits revoke confirmation permission.
  Allergen, customization, stock, cancellation and receipt rules remain in the FSM.

## API and backward compatibility

Existing result fields remain. `partial_success=true` now means a successful
**order mutation** plus an incomplete turn; read-only actions and establishing or
reviewing a session do not qualify. `order_mutation_succeeded` explicitly reports
whether a successful mutation occurred, including on wholly successful turns.
`snapshot.pending_request` exposes the retained text. Successful tools use
`source=application_results`; genuine grounded model clarifications retain
`source=ollama`; Python prompts and errors remain explicitly labeled.

Callers must not blindly replay a partially completed request. Pending text is
never automatically replayed on later unrelated turns. The pre-session request
and its live completion tracker are process-local; bound messages/results and
clarification questions remain in existing SQLite conversation memory. Restarting
the process does not automatically execute a historical request.

## Automated validation

Run `.venv/bin/python -m unittest discover -s tests -q`.
**229 tests: 228 passed, one pre-existing PowerShell simulation skipped because
PowerShell is unavailable.** No new skip or removal was introduced. Existing
follow-up fault tests now use a genuinely unresolved multipart request, retaining
mutation-count, duplicate, timeout, grounding and transaction assertions. Tests
that previously demanded an acknowledgement call now assert that it is absent.
Checkout tests now additionally assert that a batch containing an unavailable
operation is rejected before any effects, then explicitly exercise a valid edit
and demonstrate that the old review token cannot check out the changed cart.
No safety check was weakened; no test was removed or new skip introduced.
Existing mocked clients now explicitly mock semantic classification; their action,
fault and safety fixtures are retained. New focused tests cover total/review routing,
wrong-menu corrections/exhaustion, prior-review/consent enforcement, valid checkout,
repeated confirmation, classifier protocol/retry/timeout failures, both transports,
and English/Filipino/Taglish wording under different model labels. One test exercises
the actual classifier client method with mocked transport and verifies exactly one
classification plus one action call, with no final acknowledgement call.

All AI/model responses in this environment are **mocked**. SQLite/FSM, transaction,
receipt, inventory and local HTTP regressions use real disposable data on Linux.
New policy regressions cover the exact empty-cart/update failure and recovery,
retry exhaustion without dispatch, other pending menu items under 3B/7B/14B
labels, both transports' restricted schemas, catalogue refresh after completion,
cart/state/review gating, safe allergen/stock prerequisites, and read-only recovery
that must not falsely claim an unknown food request was fulfilled. Varied English,
Filipino and Taglish fixtures exercise six menu items; a new SQLite menu item
also works without item/phrase-specific backend code. These are mocked semantic
interpretations, not evidence of actual language quality from those models.
The exact Windows sequence is reproduced with a successful read followed by empty
responses, including incorrect status prevention. Further cases cover native-to-
structured recovery, unsupported native tools, non-JSON claims, incorrect quantity,
ambiguous/changed retained intent, timeouts, partial additions, no replay on later
reads, model labels 3B/7B/14B/custom, and the acceptance script's failure reporting.

Modified files for the tool-selection follow-up: `bistro/ai_engine.py`,
`bistro/ai_protocol.py`, `bistro/ai_request.py`, new `bistro/ai_tool_policy.py`,
`scripts/verify_phase4.py`, `tests/test_ai_reliability.py`, `tests/test_phase4.py`,
new `tests/test_ai_tool_policy.py`, and this document. The earlier pending-request
regressions remain in `tests/test_pending_ai_request.py`.
The semantic-routing update changes `bistro/ai_engine.py`, `bistro/ai_request.py`,
`bistro/ai_tool_policy.py`, `bistro/ollama_client.py`, the existing mocked AI test
fixtures, new `tests/test_ai_semantic_routing.py`, and this document.

## Repeatable real Windows acceptance

Run normal `setup.bat`, start Ollama, and use `configure_ai.bat`/`ai_chat.bat` for
normal operation. No reinstall, preference change or forced model download is
required by this fix. For group leads, run the optional acceptance against each
**already installed** exact Qwen2.5 tag:

```bat
.venv\Scripts\python.exe scripts\verify_phase4.py --model qwen2.5:3b
.venv\Scripts\python.exe scripts\verify_phase4.py --model qwen2.5:7b
.venv\Scripts\python.exe scripts\verify_phase4.py --model YOUR_INSTALLED_QWEN_TAG --transport structured
.venv\Scripts\python.exe scripts\verify_phase4.py --model YOUR_INSTALLED_QWEN_TAG --item "Garlic Rice" --qty 1 --order-text "Isang Garlic Rice po."
.venv\Scripts\python.exe scripts\verify_phase4.py --model YOUR_INSTALLED_QWEN_TAG --bound --item "Pork Sisig" --qty 2 --order-text "Pakidagdag po ng dalawang Pork Sisig."
```

Any compatible Qwen2.5 size/tag uses the same backend. `--transport native` starts
native but may fall back; output reports the actual final transport. The script
creates/deletes a temporary database and never changes real orders/inventory,
model preferences or downloads. It performs the exact two-turn sequence, total,
review, withholding consent, and explicit confirmation. It checks exactly two
portions, authoritative feedback, receipt and inventory deduction only once after
consent. Every incomplete turn fails acceptance, even if another mutation succeeded;
a read-only cart result cannot pass the addition step.
`--item`/`--qty` are test assertions, never hints or arguments passed to Qwen.
`--order-text` supplies customer wording. Default mode exercises the table question;
`--bound` additionally tests varied natural language after trusted table binding.
It does not relax the safety rule for ambiguous requests retained before binding.

Record exact model tag, Ollama/Windows versions, RAM/VRAM, command, output and exit
code. For manual fault testing, stop Ollama while **unfinished** work remains,
restart it and inspect the cart. Never replay saved additions to repair prose.
Repeat customization, allergen queries, out-of-stock requests and unrelated messages
during review; checkout must still require direct consent.

**Unverified/limitations:** real Windows and Qwen/Ollama execution are unavailable
here. Passing schema/installer smoke tests does not guarantee language quality.
Semantic classification accuracy and latency remain unverified with real Ollama;
a wrongly classified request can still require clarification/correction. General
multi-operation interpretation remains model-dependent. All reported language and
model-label regressions use mocked semantic and action responses, not real Qwen.
The completion grammar is deliberately conservative; retained complex/conditional
requests need restatement. Multi-tool operations retain per-action transactions,
not cross-tool atomicity. Table bindings are local session controls, not customer
authentication. Browser ordering/AI chat remain unimplemented.
