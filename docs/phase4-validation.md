# Phase 4: local Ollama and validated tool calling

## Architecture and files

Reviewed the official Lab Activity 6.1 PDF, Lesson 6 lecture PDF, creative feature
ideas TXT, and the Phase 3 FSM, required tools, order service, session store,
conversation store and CLI before implementation. The lecture's schema → routing
→ Python execution → model feedback pattern is implemented without replacing
working Phase 1–3 services. Course documents and collaboration logs are untouched.

Created:
- `bistro/ai_engine.py`: UI-independent `AIWaiter.respond(text)` and `snapshot()`.
- `tests/test_phase4.py`: controlled model/transport mocks with real disposable SQLite.
- `scripts/verify_phase4.py`: opt-in real Qwen smoke test with disposable SQLite.
- `docs/phase4-validation.md`: this report.

Modified:
- `bistro/ollama_client.py`: replaces the reserved stub with loopback HTTP `/api/chat`.
- `bistro/tool_schemas.py`: replaces the reserved stub with schemas, validation and dispatcher.
- `virtual_waiter_prototype.py`: adds `--ai`, `--table`, trusted `--session` resumption.
- `.env.example`: configurable `BISTRO_OLLAMA_TIMEOUT`.
- `README.md`: setup, usage and validation boundaries.

No dependencies, database migrations, setup/reset logic, or dashboard changes
were needed. Python's standard library supplies HTTP transport. `run.bat` still
launches the existing Phase 1 dashboard; use the explicit CLI command for AI.

The exact configured `qwen2.5:7b` model receives the session, FSM state, current
cart, pending question, recent persistent history and SQLite menu. Native Ollama
function calls use the allowlist. Only an explicit unsupported-tools HTTP error
switches that same model to validated JSON routing. Tools are never executed via
`eval`, arbitrary SQL, shell, or dynamic function lookup.

Each batch is structurally validated before dispatch. Required professor tools
are exposed, plus table capture, full-menu browsing, stock, quantity, removal,
instructions, review, guarded confirmation, and cancellation. Session ID parameters
are checked against the application's binding and stripped before dispatch.
There is no model-accessible resume/switch session tool. Future web adapters must
authenticate session ownership; IDs alone are not credentials.

The existing FSM performs each database action, state update and action transcript
atomically. Actual results go back to Qwen as tool messages. AI messages, validated
calls, failures and pending clarifications reuse the persistent Phase 3 store.
Before table capture, bounded greeting memory is held locally, then transferred
when the session is established. The model reads only the last 24 stored entries;
the database retains the full transcript. Requests through one engine serialize.

`respond()` returns response, source, validated tool traces, inference indicator,
and a snapshot containing history, FSM state, cart, review token, receipt, pending
actions (empty once synchronous execution completes), and connection error.
`snapshot()` is reusable for Phase 5. No Phase 5 browser interface is implemented.

## Safety and output grounding

Qwen interprets order requests, including Taglish, using inference. SQLite resolves
all factual values. AI adds require exact menu names: unknown/incomplete names
fail and ask for clarification rather than substituting an item. Real line IDs
are checked against the bound session. Customizing one of two portions requires
reducing the original line and adding a separately customized portion.

To prevent arbitrary factual prose, Qwen's final response must be JSON selecting
verbatim evidence strings generated from current menu/cart/tool results, together
with a constrained acknowledgement or clarification question. The model chooses
these strings after actual tool feedback; Python renders them. Ungrounded facts,
invented questions mentioning unknown foods, false action claims and stale totals
are rejected with at most one corrective retry. This intentionally limits free-form
phrasing: it is constrained model output, not unrestricted conversational prose.
Facts may be omitted by the model; the CLI always also displays authoritative cart
and tool results. This phase does not claim an unrestricted fluent waiter.

Confirmation additionally requires a narrow explicit customer phrase such as
`Yes, confirm my order.` or `Kumpirmahin ang order`, the review existing before
that turn, and its SHA-256 cart review token. Plain `okay` or an unrelated question
cannot authorize checkout. This deterministic consent interlock is separate from
model-based ordering interpretation. Review and confirmation in one turn are
blocked. Edits clear permission; the FSM also rejects a changed cart/price snapshot,
an empty cart, and edits after confirmation/cancellation. Only the Phase 2 atomic
checkout service creates receipts and deducts stock; duplicate confirmations
cannot deduct again. Database status and conversational state remain distinct.

At most eight tool calls and one malformed-output retry occur per turn. Duplicate
identical calls in a turn are blocked, including mutations after a failed final
model reply. Transport errors are not retried. A later model failure does not roll
back previously committed actions: tool traces and the current cart report those
effects. Inspect the cart before submitting a new request; user repetitions in
new turns are new requests. Service database errors roll back that action and
never become successful tool results.

## Windows installation and usage

Install 64-bit Python 3.11/3.12 and Ollama from
https://ollama.com/download/windows. In CMD from the project directory:

```bat
setup.bat
ollama pull qwen2.5:7b
ollama list
ollama run qwen2.5:7b "Say hello briefly."
.venv\Scripts\python.exe virtual_waiter_prototype.py --ai --table 3
```

Start the Ollama desktop app; if no service is running, use `ollama serve` in a
separate terminal. Set `BISTRO_OLLAMA_URL`, `BISTRO_MODEL`, and
`BISTRO_OLLAMA_TIMEOUT=120` in `.env` as needed. The URL must be loopback HTTP.
A different exact Qwen2.5 tag requires an explicit configuration change and local
validation. There is no silent model substitution or cloud API.

Without `--table`, say `Hello, table 3 po.` and Qwen must call table capture first.
For a trusted local resume use `--ai --table 3 --session PRINTED_SESSION_ID`.
`state` displays saved context/cart; `quit` preserves it. Start a fresh CLI/table
session after confirmation/cancellation. Structured mode remains the default:

```bat
.venv\Scripts\python.exe virtual_waiter_prototype.py
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe scripts\verify_phase4.py
```

The last command performs **real inference**, prints actual tool traces, and uses
a disposable database. It fails if model interpretation, tool execution or checkout
does not match expectations. It never modifies restaurant order data. After setup,
model/dependencies are local and operation needs no cloud service or internet.

## Example workflow (instructions, not a recorded inference log)

1. `Hello, table 3 po.` → establish the table binding.
2. `Dalawang Chicken Adobo po.` → add two exact SQLite items; inspect actual cart.
3. `Walang sibuyas yung isa.` → split one portion into a customized line.
4. `Add one Bistro Iced Tea.` → add a drink if current stock permits.
5. `Tanggalin yung iced tea.` → remove the correct bound cart line.
6. `Magkano na total?` → view authoritative cart total.
7. `Can I review my order?` → display reviewed cart and enter REVIEW.
8. `Actually, add one Garlic Rice.` → edit invalidates that review.
9. `Can I review my order?` → obtain a new current review.
10. `Yes, confirm my order.` → guarded checkout and persisted receipt.

These are requests to try locally, not fabricated Qwen responses. If a reference
is ambiguous, answer the clarification question and check line-level effects.
The mock suite verifies customization, removal and totals for controlled Taglish
routing; only real local inference can establish Qwen's interpretation quality.

## Actual validation results (October 10, 2026)

Linux / Python 3.12, project-local `.venv`:

```text
.venv/bin/python -m unittest discover -s tests -v
Ran 92 tests
OK
```

Includes all Phase 1–3 regression tests and 32 Phase 4 tests. The Phase 1 server
integration uses a real local HTTP server. Phase 4 model replies and HTTP transport
are mocks; their SQLite actions, transactions, memory and FSM transitions are real.
Coverage includes allowlists/types, session isolation, exact menu validation,
evidence grounding, persistent context, tool feedback, review/confirmation guards,
duplicate stock protection, connection/timeouts/missing model, malformed output,
bounded retry, database failure, repeated calls, loop limits, English/Taglish
controlled routing, customizations, removals, CLI and stale-fact blocking.

Real local Ollama probe: **connection refused at 127.0.0.1:11434**; no `ollama`
executable is installed here. No successful real Qwen inference, native tool
accuracy, hardware performance or robust Taglish support is claimed. No model
was replaced, and no fake inference log or pair contribution was created.

Windows CMD launchers, Windows Ollama installation/process behavior and Python
3.11 were not executed in this Linux/Python 3.12 environment. Existing regression
coverage passes, but those platform claims require local checks.

Local verification checklist:
- Run setup in a folder containing spaces; repeat setup and confirm existing data survives.
- Verify exact `qwen2.5:7b` presence and a real `ollama run` response.
- Run the real smoke script; inspect successful tool traces and receipt, not just prose.
- Try the multi-turn workflow in English and Taglish; test ambiguous/customized portions.
- Reject confirmation before review, after an edit, on an empty cart, and on unrelated `okay`.
- Repeat confirmation and verify stock only deducted once; reject closed-cart edits.
- Stop Ollama and verify clear failure without false success or deterministic AI substitution.
- Exercise missing model and loading timeout; check available RAM/VRAM and Ollama logs.
- Run the full unit suite under Windows Python 3.11 and 3.12.

## Troubleshooting

Connection failure: start local Ollama and verify URL/port. Missing model: pull the
exact configured tag. Loading/inference timeout: check RAM/VRAM, close competing
applications, and increase timeout (maximum 600 seconds). Output/schema rejection:
use `--debug`, inspect traces/current cart, and try an explicit full menu name or
clarification; the application will not relax validation. Do not reset the database
as a troubleshooting step. If inference fails after a tool succeeded, inspect the
cart before repeating an add. Windows antivirus/firewall behavior needs local testing.
