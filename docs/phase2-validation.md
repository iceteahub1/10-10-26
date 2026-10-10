# Phase 2 — SQLite database and order foundation

## Scope and usage

The professor's required tables and fields are preserved in
`init_restaurant_db.sql`. Supporting columns add availability, cart line IDs,
creation timestamps, and versioned seed metadata. `receipts` and
`receipt_line_items` persist PHP totals and item/name/price/instruction snapshots.
The sample menu contains 12 Filipino restaurant items across five categories.
Prices and inventory are demonstration data, not professor-provided prices.
Allergens are declared ingredient tokens; absence is not a cross-contact guarantee.
Special instructions are stored text, not ingredient changes or price modifiers.

Run `setup.bat` as before. Its existing Python setup path now initializes the
project-relative `data/restaurant.db` before recording setup success. Existing
Phase 1 installations may rerun setup, or initialize only the database using:

```bat
.venv\Scripts\python.exe -c "from bistro.database import initialize_database; initialize_database()"
```

Python service example (no model, FSM, or browser ordering flow required):

```python
from bistro.database import initialize_database
from bistro.session_store import create_session
from bistro.order_service import (
    browse_menu, check_stock, change_quantity, customize_item, remove_item,
    view_cart, checkout, get_receipt,
)
from db_tools import (
    get_menu_by_category, check_item_allergen,
    add_item_to_order, view_current_order,
)

initialize_database()
session = create_session(table_no=3)  # Save this ID for later resumption.
sid = session['session_id']
menu = get_menu_by_category('Mains')
contains_peanut = check_item_allergen(3, 'peanut')
cart = add_item_to_order(sid, 'Chicken Adobo', 2, 'less salt')
line_id = cart['items'][0]['line_id']
cart = change_quantity(sid, line_id, 1)
cart = customize_item(sid, line_id, 'sauce on the side')
cart = view_current_order(sid)
# remove_item(sid, line_id) removes a line; quantity zero is rejected.
# Call only once the external caller has confirmed the order.
receipt = checkout(sid)
assert get_receipt(sid) == receipt
assert checkout(sid) == receipt  # Retry returns the original receipt.
```

Every API accepts optional keyword `db_path` for a separate database. Normal
operations require an initialized, existing database and never silently create
one. Reads and writes use fresh connections with foreign keys enabled and a
10-second SQLite lock timeout. Lock timeout errors propagate and roll back;
callers may retry. Session IDs are persistent; a checked-out session cannot be
edited and a new order requires a new session. `create_session` is idempotent for
the same table and ID, but does not reopen a completed session.

Exact case-insensitive menu names take precedence over literal substring matches.
Ambiguous or unknown names are rejected. Quantities must be actual integers from
1 to 10,000 (not booleans, strings, or fractions). Cart line IDs are checked against
the owning session. Identical item/instruction pairs combine quantities; different
instructions remain separate lines. Stock validation aggregates all variants.
Customization collisions are rejected rather than silently merging lines.

## Initialization and checkout safety

Initialization locks with `BEGIN IMMEDIATE`, then creates and seeds a new database
inside that transaction. A seed marker prevents repeated stock replenishment.
Version 1 databases are validated and preserved; unversioned nonempty databases,
future versions, and missing required columns fail explicitly without resets.
There is no destructive migration or schema replacement. Direct SQL initialization
should be wrapped in a transaction; the supported Python initializer supplies it.

Carts do not reserve inventory. Checkout is an explicit service call, with actual
conversational confirmation deferred to Phase 3. Its write transaction serializes
cart writes and competing checkouts across connections/processes. It reads current
database prices, rejects an empty cart, aggregates item quantities, and uses
conditional stock updates requiring availability and enough stock. It then saves
one unique receipt per session, snapshots line details, and marks the session as
checked out. Every step commits together; any exception rolls back stock,
receipt rows, and session status. A repeated checkout returns the stored receipt.
After checkout, cart viewing also returns the saved receipt, unaffected by later
menu price/name changes. Tax/service rates on retries do not recalculate it.

Money arithmetic uses Decimal and integer centavos, with fixed two-decimal PHP
strings for presentation. Tax and service each apply to subtotal and round half
up independently; defaults are zero. Callers may explicitly pass rates from
`load_settings()` if desired. No tax jurisdiction or official restaurant fee is
assumed. Editable carts use current menu prices; final checkout prices are the
persisted receipt snapshots.

## Verification

On Linux, CPython 3.12.14, using the preserved project virtual environment:

```text
.venv/bin/python -m unittest discover -s tests -v
32 tests passed: 11 existing Phase 1 + 21 Phase 2.
```

Phase 2 covers schema and seed initialization, repeated and concurrent setup,
unsupported schema preservation, atomic schema failure, SQL constraints/foreign
keys, category/allergen tools, sessions, invalid/ambiguous inputs, cart edits and
ownership, aggregate stock, exact totals and centavo rounding, checkout-time price
and stock checks, immutable receipt snapshots, injected receipt failure rollback,
duplicate/concurrent checkout, competing sessions, persistence across connections and a fresh Python process,
and paths containing spaces/from another working directory. All database tests
use temporary directories; they do not access the real restaurant database.
Setup integration mocks dependency installation and Ollama prerequisite checks.

The unchanged Phase 1 integration test launches real Uvicorn, checks readiness,
serves dashboard HTML and responsive CSS, then verifies application cleanup.
The first run hit sandbox socket restrictions; rerunning with approved localhost
socket access passed the full suite. The batch files, dependencies, virtual
environment, dashboard source, launcher, ignored-data rules, and reference files
are preserved.

Windows 10/11 batch execution/process behavior and Python 3.11 execution remain
untested in this Linux environment. Real Ollama/Qwen inference is neither tested
nor implemented by Phase 2. Browser cart/checkout UI, conversational confirmation,
and FSM transitions are deferred. Reference documents and evidence logs were not
edited. Phase 3 requires separate user approval.
