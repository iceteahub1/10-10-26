# Phase 3 — deterministic FSM, persistent memory, and CLI

## Architecture and scope

Verified baseline: `55e0b1ec541dabb169bfb6519bf25727ca49ff68`, matching
GitHub `main` before implementation. Both official Lesson 6 PDFs were read.
The lab asks for five states but explicitly lists four; `STATE_CANCELLED` is the
fifth state for safely preserving a cancelled customer's order/history.

`fsm_agent.ConversationAgent.execute()` accepts exactly
`{"action": "name", "arguments": {...}}` and returns JSON-safe `ok`, `session_id`,
`state`, `message`, `data`, and (on failure) `error`. Unknown actions, extra keys,
missing arguments, non-JSON values, and invalid business inputs are rejected.
`ACTION_ARGUMENTS` registers required/optional argument names for a future dispatcher.

Actions invoke the existing `db_tools` wrappers and `order_service`; the controller
contains no second implementation of stock, cart, money, or checkout rules.
A `ContextVar` lets synchronous service calls on the same thread/database join an
outer `BEGIN IMMEDIATE` transaction. Cart writes, receipt creation, FSM state, and
successful transcripts commit together. Any exception rolls back those effects;
a separate transaction attempts to store the failed action and friendly error.
`--debug` exposes technical exception details through logging. Do not share an
active SQLite connection/context across threads or catch-and-ignore exceptions
inside an outer transaction.

The controller reloads persistent state for every action. Confirmation requires
`confirmed` to be the actual boolean `true`, REVIEW state, and a saved, nonempty
review matching the authoritative current cart (prices, inventory, customizations,
line identities, and configured rates). Changes require another review. Checkout
uses Phase 2's serialized transaction and persisted receipt; repeated confirmation
returns that receipt without another inventory deduction.

## State transition table

| From | Trigger | To / behavior |
| --- | --- | --- |
| GREETING | Valid table; establish or resume | TAKING_ORDER, or the resumed stored state |
| TAKING_ORDER | Review nonempty authoritative cart | REVIEW |
| TAKING_ORDER / REVIEW | Successful add, quantity, instruction, or remove action | TAKING_ORDER; discard review |
| REVIEW | Return to ordering | TAKING_ORDER; discard review |
| REVIEW | Cart/menu differs at confirmation | TAKING_ORDER; show changed cart; no checkout |
| REVIEW | Explicit boolean confirmation of unchanged review | CONFIRMED; persist receipt |
| TAKING_ORDER / REVIEW | Cancel | CANCELLED; retain cart/history; no stock deduction |
| CONFIRMED | Repeat explicit confirmation / view | CONFIRMED; return original receipt |
| CANCELLED | View | CANCELLED; show retained cancelled cart |
| CONFIRMED / CANCELLED | New customer | New session ID in TAKING_ORDER; old session unchanged |
| Active state | Menu / stock / allergen queries, view | Same state; persist question and actual result |

Other transitions fail. Resuming existing Phase 2 checked-out sessions, or viewing
an order finalized by a trusted Phase 2 caller, reconciles the controller to
CONFIRMED. The low-level Phase 2 API remains a trusted programming interface;
customer-facing confirmation must go through the controller. Cancelled receipt
creation and terminal cart mutation are also blocked by SQLite triggers.

## SQLite extension migration and memory

`initialize_database()` validates the existing Phase 2 tables, then applies
`migrations/001_conversation_memory.sql` within the same transaction. The Phase 2
base keeps `PRAGMA user_version=1` to preserve its existing version contract and
tests. The additive extension is independently versioned by
`schema_metadata.conversation_schema_version=1`; this is not an unversioned patch.
No existing table columns or check constraints are replaced, and no data is reset.
Unknown extension versions, unversioned extension tables, missing required columns,
and missing integrity triggers are rejected. Failed migrations roll back. Repeated
setup validates the installed extension without reseeding/restocking.

New tables are `conversation_sessions`, `conversation_messages`, and
`conversation_transitions`. Foreign keys bind each record to exactly one session.
Messages store ordered customer actions/text and actual assistant results; state,
review, latest menu context, unresolved ambiguous references, and latest
modification are structured separately. Prior modifications remain in the history.
Triggers persist transitions and invalidate reviews on cart writes, including
writes through the existing Phase 2 service. Cancellation remains a conversation
terminal state; the original Phase 2 `table_sessions.status` vocabulary is preserved.

`agent.context()` returns this session's state, history, transitions, pending
question/review, table number, and current order. `conversation_store.get_context()`
is the modular history adapter for Phase 4. No history is copied into new sessions.
Explicit resumption checks the session's table. Table-only resumption selects the
sole active session; multiple active sessions require an explicit session ID.
A current customer must finish/cancel before switching to another customer.
Session IDs provide isolation, not authentication; a future public web adapter
must authenticate table/session access. Full histories are retained; Phase 4 must
choose a bounded model context without erasing stored records.

## CLI

Windows after normal dependency setup:

```bat
.venv\Scripts\python.exe virtual_waiter_prototype.py
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Linux cloud development:

```sh
.venv/bin/python virtual_waiter_prototype.py
.venv/bin/python -m unittest discover -s tests -v
```

The CLI initializes/migrates the project-relative database without requiring
Ollama. Use `--db PATH` for an isolated database and `--debug` for diagnostics.
Enter `help`, `state`, or `quit`; otherwise enter one JSON action per line.
EOF/Control+C preserves the session. These commands are deterministic customer
inputs, not natural-language understanding or AI output.

| Action | Required arguments | Optional arguments |
| --- | --- | --- |
| establish_session | table_no | session_id (must already exist and match table) |
| new_session | table_no | — |
| browse_menu | — | category |
| check_availability | item_id | — |
| check_allergens | item_id, allergen | — |
| add_item | item_name, qty | instructions |
| update_quantity | line_id, qty | — |
| remove_item | line_id | — |
| update_instructions | line_id, instructions | — |
| view_order | — | — |
| request_review | — | — |
| return_to_order | — | — |
| confirm_order | confirmed (boolean true) | — |
| cancel_order | — | — |

Use exact menu names; ambiguous substring references produce a persisted follow-up
question rather than a guessed item. Table numbers must be integers 1–2147483647.
Quantities must be integers 1–10000, never booleans; line IDs come from cart results.
Instructions are stored text and do not assert ingredient substitutions or prices.
Money remains PHP centavos and fixed two-decimal strings; configured fractional
tax/service rates are passed to the existing service.

Example deterministic input:

```jsonl
{"action":"establish_session","arguments":{"table_no":3}}
{"action":"browse_menu","arguments":{"category":"Mains"}}
{"action":"add_item","arguments":{"item_name":"Chicken Adobo","qty":2,"instructions":"less salt"}}
{"action":"request_review","arguments":{}}
{"action":"confirm_order","arguments":{"confirmed":true}}
{"action":"confirm_order","arguments":{"confirmed":true}}
```

For an unchanged fresh seed and zero rates, subtotal/total is PHP 370.00, stock
moves from 30 to 28 once, and both confirmations return the same receipt ID.
To resume after exit, establish table 3 with its printed session ID. To start a
new customer after confirmation/cancellation, use `new_session` with table 3.

## Validation evidence and limitations

Linux, 64-bit CPython 3.12.14:

```text
.venv/bin/python -m pip check
No broken requirements found.
.venv/bin/python -m unittest discover -s tests -v
60 tests passed: 11 Phase 1 + 21 Phase 2 + 28 Phase 3.
```

A fresh project-local `.venv` installed the unchanged pinned requirements; the full
suite also passed using that interpreter. A separate real CLI subprocess demo
verified PHP 370.00 and identical receipt IDs on repeated confirmation.

Phase 3 tests use temporary databases and cover valid/invalid states, table and
JSON validation, session creation/resumption/misuse, independent tables, restart
in a fresh Python process, structured menu/cart actions, ambiguity memory, edits,
review invalidation by direct service writes or price changes, explicit confirmation,
empty carts, cancellation and direct checkout rejection, immutable orders,
concurrent duplicate confirmation, atomic action/history rollback, receipt failure,
legacy receipts, CLI help/restart/interrupts, preserved Phase 2 records, repeated and
failed migrations, unknown extension versions, and nested transaction boundaries.
The unchanged Phase 1 suite launches a real dashboard and checks serving/cleanup.
Phase 1 Ollama checks are mocked; no real model or inference is claimed.

Windows CMD batch execution, Python launcher discovery, Windows process cleanup,
browser opening, and CPython 3.11 execution remain untested here. No pinned
requirements, batch scripts, dashboard files, or existing tests were changed.
The PDFs, creative ideas TXT, pair log, and submission conversation JSON are
preserved. No partner participation or AI conversation traces were fabricated.

Deferred to Phase 4: Ollama/Qwen client, model-selected tool calls, natural-language
responses, and model context selection. Voice and customer ordering web UI are
not implemented. Phase 3 ends here; Phase 4 requires separate approval.
