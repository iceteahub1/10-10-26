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

Cloud validation: the focused browser suite passed all 18 tests. Full discovery
ran 254 tests: 253 passed and the existing PowerShell-only bootstrap scenario
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
