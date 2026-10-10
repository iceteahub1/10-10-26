# Phase 5 browser dashboard

Start with `setup.bat`, then double-click `run.bat`. Keep its console open. Use the
printed local address consistently (localhost and 127.0.0.1 have separate browser
cookies). Enter a positive integer table number. The menu reads actual SQLite
categories, prices, stock, and declared allergens; descriptions appear only if
present in database data. No food descriptions are invented.

Use the item card's quantity and optional instructions to add an item. Cart
controls update quantities/notes or remove lines. Chat uses the existing
`AIWaiter` and configured native/structured Qwen transport, with saved conversation
history. Enter sends; Shift+Enter adds a newline. Chat is disabled when local AI
is unavailable; menu/cart controls stay usable. Use **Check again** after starting
Ollama. Use `configure_ai.bat` to enable AI or change the exact installed model.

**Review order** displays the exact backend review and total in a separate area.
**Confirm & place order** explicitly confirms that review. Changing any item
invalidates it. A successful confirmation shows the persisted receipt. After
confirmation/cancellation, **New table session** creates a fresh order; it never
resumes an unrelated customer's order by matching table number.

## Architecture and security

- FastAPI serves local HTML/CSS/JavaScript without a frontend framework, remote
  fonts, or CDN. `run.bat` retains its checked environment, bounded readiness,
  loopback settings and owned-process cleanup. Setup and both CLI modes are intact.
- The browser's random 256-bit HttpOnly/SameSite=Strict cookie is separate from
  backend session IDs. SQLite stores only its SHA-256 digest and the session
  binding. Cookies persist for 30 days; clearing browser cookies or changing
  browser profiles starts a separate order. There is deliberately no API for
  accessing an existing order with a table number or arbitrary session ID.
- Cookie sessions on loopback HTTP use no Secure attribute because the local
  launcher serves HTTP. This is a trusted single-computer university demo, not a
  public or LAN service. Do not bind publicly or run multiple workers. Anyone
  controlling the computer/browser profile can access its local data.
- Every write requires the exact local Origin, valid loopback Host, JSON content,
  an authenticated browser cookie, a bounded request ID, and validated input.
  Host validation prevents DNS rebinding. No CORS, OpenAPI, or arbitrary-action
  API is enabled. Request bodies are capped at 16 KiB.
- Writes use a durable SQLite request ledger keyed by browser credential and
  request ID. The same ID/input returns its saved result; changed input is
  rejected. Deterministic FSM effects and saved responses commit atomically.
  AI claims commit before inference, preventing duplicate model-driven mutations
  after a crash or lost response. Other writes for that browser wait until the
  claim completes; other browser sessions remain independently usable.
- In-flight claims return `request_pending`. After a server restart they become
  `request_interrupted`, with no replay of earlier effects. Refresh the cart to
  check persisted changes before initiating a new request. The browser saves the
  unresolved request in sessionStorage and **Retry same request** reuses its ID.
  This stores no cookie credential. Cached responses are followed by a fresh
  snapshot so an older successful retry cannot overwrite the visible current cart.
- Review tokens, state transitions, stock checks, price snapshots, receipt
  persistence, and checkout idempotency use the existing FSM/SQLite code. Browser
  AI cannot rebind a finished session or switch to another table's session.
- All message/menu/instruction content is inserted with `textContent`; no model
  HTML is executed. CSP blocks inline scripts and framing. No runtime data,
  cookie secrets, credentials, or disposable test databases are committed.

## Automated validation

Run the pinned project Python, not a different system environment:

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -p test_browser.py -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Browser API tests run a real loopback HTTP server and disposable real SQLite
instances. Only Ollama inference/status failures are mocked. They cover cookie
isolation and persistence, restart/interrupted claims, actual menu values,
English/Taglish AI integration, cart edits, review invalidation, explicit
confirmation, stock changes, persisted receipts, concurrent/repeated checkout,
request deduplication, malformed requests, Origin/Host validation, model/offline
states, transaction rollback, and the shared CLI workflow.

The existing dashboard readiness test now checks the real waiter heading instead
of the removed Phase 1 placeholder text; its HTTP/static/readiness/cleanup
assertions remain in place. No existing tests were removed or skipped.

Cloud validation: the focused browser API suite passed all 20 tests, plus 3 acceptance-log tests.
Full discovery ran 259 tests: 258 passed and the existing PowerShell-only bootstrap scenario
was skipped because PowerShell is unavailable. Real headless Chromium checks
passed at 1440, 1024, 768, and 390 px, including item addition, mocked AI through
the real engine, cart synchronization, review/checkout, receipt refresh, mobile
navigation, no horizontal overflow/JavaScript errors, and literal HTML-like
instructions. No new runtime dependencies were installed.

## Short manual Windows / real Ollama checklist

Use a disposable copy of the project/database so demonstration checkout does not
consume inventory you need to keep. Never delete/replace a working database.

1. In a writable folder with spaces, double-click `setup.bat`, then `run.bat`.
   Confirm the browser opens and its console stays running. Repeat with AI skipped
   or Ollama stopped: menu/cards/cart/review/receipt must still work. Check the
   displayed AI reason, and **Check again** after starting Ollama.
2. Start table 3; add an available item with quantity 2 and a note. Change its
   quantity/note, remove one line, and verify totals against displayed prices.
   Filter categories. Check declared allergens and an out-of-stock item.
3. Review, then edit a quantity. The previous review must disappear. Review again,
   explicitly confirm, note the receipt ID and stock decrement. Refresh and reopen
   `run.bat` using the same browser profile/address: the same receipt must return.
   Repeated confirmation/retry must not create another receipt or stock deduction.
4. Open a separate browser profile/private window, enter the same table number,
   and verify its cart is empty and the first profile's order is inaccessible.
5. For each **already installed and configured compatible** Qwen2.5 size, use
   `configure_ai.bat` and restart `run.bat`. Try “I'd like one Chicken Adobo,”
   “Dalawang Chicken Adobo po,” “Magkano na total?”, “Can I review my order?”,
   then “Yes, confirm my order.” Verify real saved quantities, review and receipt
   after every turn. Try ambiguous requests, edits after review, and unavailable
   stock; failures must not claim a successful order. No extra downloads required.
6. Resize to desktop/tablet/mobile, use the three mobile tabs, and navigate with
   Tab/Shift+Tab. Check focus outlines, labels, Enter/Shift+Enter, loading/errors,
   and receipt printing. Stop with Ctrl+C and verify the port is released. Launch
   `ordering.bat` and `ai_chat.bat` to check both existing CLI modes.

Real Windows batch/PowerShell behavior, browser opening/printing and real Qwen
quality/latency across installed sizes require this manual verification. Mocked
inference is not evidence of actual model quality.

## Offline UI follow-up and acceptance reports

AI availability now controls only chat. Menu/category navigation stays available;
cart writes depend on an active table session, stock, and a request currently in
progress. The old global disabled-flag restoration has been replaced with
explicit state rules. After failed chat, a background AI recheck cannot hold cart
controls disabled; status probes time out after five seconds. The banner explicitly
shows **AI unavailable**, the Ollama reason, and how to order without AI. Start a
table session first to enable Add, regardless of AI availability.

The footer provides **View acceptance report** and **Export report**. The JSON
report contains recent backend observations from the latest server run:
startup, AI/menu availability, cart action type and outcome, review transitions,
checkout results, receipt creation, and workflow error codes. Use it alongside
the Windows checklist; it does not certify UI behavior or real model quality.

Events are written separately to `logs/acceptance.jsonl`, with one rotated backup
and a 1 MiB limit per file (plus the final small event). The report is limited to
the latest 1000 events for the current startup. Restarting starts a new QA run;
the old bounded file history may remain locally. Timestamps include UTC offsets.
No chat text, special instructions, table numbers, menu/receipt/session IDs,
request IDs, cookie values, credential hashes, or free-form exception messages
are logged. The report contains only fixed codes, boolean outcomes, counts,
state names, timestamps and a non-authorizing QA run identifier. Report access
uses the existing browser cookie and loopback Host protection.

Logging file failures do not prevent ordering; the report then shows
`logging_available: false`. `application.log` and its existing launch behavior
are unchanged. Cached confirmation results are labeled as replayed and do not
log another receipt creation. Keep exported reports outside Git.

Focused checks:

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -p test_browser.py -v
.venv\Scripts\python.exe -m unittest discover -s tests -p test_acceptance_log.py -v
```

Optional developer browser regressions use an existing Node/Playwright/Chromium
installation; these are not application dependencies and setup installs none of
them. The test server uses a disposable real database and a closed loopback Ollama
port for offline tests. AI-mode regression inference alone is mocked.

```text
node --test tests/test_dashboard.cjs
```

If using a system Chromium, set `BISTRO_CHROMIUM` to its executable path. Set
`BISTRO_TEST_PYTHON` only if the pinned test environment is elsewhere. The browser
checks cover complete offline ordering, an initially working AI becoming
unavailable with a stalled recheck, and preserved chat-to-cart integration.

For manual QA, stop Ollama before starting the dashboard, complete a deterministic
order, then export the report. Repeat by stopping Ollama after an initially ready
AI status. Confirm that only chat becomes unavailable, then complete another
order using the menu/cart. Include the exported report with the observations.

Follow-up validation: all 3 Chromium regressions passed (offline checkout, stalled
AI recheck recovery, and preserved AI mode). The focused API/log tests passed
before a single complete regression run. Real Windows/Ollama retesting remains
required using the checklist above.

### Live Ollama connectivity follow-up

Every enabled-AI status check now probes the configured local `/api/tags` endpoint,
including when a test inference client is injected; client injection no longer
implies availability. Saved model preferences and launcher environment flags are
not used as evidence of connectivity. The status banner includes the actual
endpoint: compare Windows `curl` results against that address, especially when
`BISTRO_OLLAMA_URL` selects a custom port. The existing launcher may automatically
start Ollama, so check the process/endpoint after launching as well as beforehand.

The browser rechecks every 15 seconds, on window focus/visibility, and on **Check
again**. Older overlapping probes cannot overwrite a newer result. Status means
Ollama answered the live model-list request and lists the selected model; it does
not guarantee inference success. Fresh chat checks availability again, and actual
model-request failures remain errors with no fabricated success or new cart
mutation. Previously saved transcripts/cached request results remain historical
results; their display is not evidence of current inference.

Regression coverage uses a disposable live HTTP endpoint to test available →
unreachable → reconnected, verifies injected inference cannot bypass offline
status, completes offline deterministic checkout, and rejects a failed real model
request even when the model-list endpoint answers. Chromium checks cover manual
rechecking/reconnection and out-of-order status responses. No FSM, ordering,
SQLite, receipt, or session authorization code was changed.

Connectivity validation: 22 focused API tests and 4 Chromium checks passed.
Complete pinned-environment discovery ran 261 tests: 260 passed and the existing
PowerShell-only test was skipped. Real Windows/Ollama verification still requires
the documented manual checks.
