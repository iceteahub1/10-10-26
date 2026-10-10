# Flexible AI setup validation

Implemented from GitHub main `3176a7d7b8f502c89e0752155dfe23222b544ba1`.
Scope is Windows setup and existing terminal Ollama integration. No Phase 5 UI,
business schema changes, seeded inventory resets, or receipt format changes.

## Behavior

- `setup.bat` keeps the existing verified Python detection/install and `.venv`
  repair. SQLite initialization remains idempotent and preserves existing records.
- Installed exact Qwen2.5 tags are detected through Ollama's local `/api/tags`.
  7B appears first as preferred, 3B as lightweight; other installed Qwen2.5 tags
  can be selected, including different sizes/quantizations. Availability is a
  candidate filter, not proof of compatibility. No size is guaranteed to pass.
- Each selected model receives three read-only routing probes with real
  application tool schemas: menu, category, and integer item ID. A fourth probe
  checks the constrained grounded reply shape. Calls are validated but never
  dispatched. No database/session/order/inventory is accessed by this check.
  Native tools are used first; the existing structured JSON route is tested only
  when Ollama explicitly reports native tools unsupported. Invalid calls, wrong
  arguments, hallucinated replies, connection failures and timeouts fail the check.
- A failed check offers another candidate or non-AI mode. A passing smoke test
  does not prove reliable multilingual ordering, ambiguity handling, or hardware
  performance. Existing FSM/tool validation still enforces business safeguards.
- `runtime/ai.json` stores enabled state, exact model, and validated transport.
  It is local/ignored by Git and atomically replaced. Saved selection takes
  precedence over the legacy environment model. Launch does not ask for another
  choice or silently substitute a model. A missing model/service is reported;
  core/dashboard operation continues and the saved choice is retained.
- `configure_ai.bat` changes/enables AI without reinstalling libraries. Users
  without Ollama are directed to its official optional Windows installer.
  `ordering.bat` opens existing deterministic JSON ordering without Ollama;
  `ai_chat.bat` starts/reuses the exact model's local service for terminal AI and
  cleans up only a service it started. Disabled/missing AI produces a real error,
  never synthetic responses. `run.bat` still opens the infrastructure dashboard,
  whose status explicitly states that browser AI chat/ordering is not implemented.

## Downloads and consent

The threshold is **100,000,000 bytes (decimal 100 MB)**. Required, verified,
nonprivileged downloads below it can proceed automatically. At/above it, or with
unknown size, consent is required. Optional downloads always require consent.
Permission defaults to no; flags never grant implicit consent.

Required library sizes and SHA-256 hashes come from HTTPS PyPI metadata. Only
compatible pinned wheels are selected. pip receives exact trusted URLs with
`--require-hashes --no-deps --only-binary=:all:`; no unplanned transitive/source
build downloads are allowed. Redirect metadata is confined to approved HTTPS
hosts. A source/hash verification failure stops library installation before pip.
Unknown wheel sizes still require approval, if their source and hash are verified.
Known sizes show a conservative free-space allowance and the available space.

Optional model sizes come from the official Ollama registry manifest, counting
all blobs even if cached. Ollama performs the actual content-addressed pull.
Known models use a conservative allowance of twice the blob total plus 500 MB.
Checks use `OLLAMA_MODELS` when set, otherwise the default home model directory;
users with a separately configured service folder must check that drive too.
Unknown size/storage requirements are explicitly reported and require approval.
A declined/failed optional pull cannot prevent the core success marker.

Python installation continues to request explicit consent, uses per-user options,
and checks the Python Software Foundation Authenticode signature before execution.
The response's download size and free-space requirement are displayed before the
transfer; unknown/large sizes need explicit approval. The pinned installer is
bounded to 100,000,000 bytes and never changes global PATH/file associations.
No Ollama binary or privileged software is silently installed by the application.

## Results and boundaries

Run: `.venv/bin/python -m unittest discover -s tests -q` on Linux / CPython 3.12.
Result: **146 tests run; 145 passed, 1 skipped** (PowerShell unavailable).
The full suite includes all Phase 1–4 and installer regression tests, with legacy
fatal-AI assertions updated for optional AI behavior. PowerShell simulation is
skipped when PowerShell is unavailable.

`tests/test_flexible_ai.py` covers only 3B, only 7B, both, no models, another size,
missing saved selection, compatible/incompatible/malformed/unsupported models,
download refusal, below/exactly/above 100 MB, unknown size, optional/privileged
small downloads, insufficient disk, trusted sources/hashes, non-AI launch,
AI disabled, enabling later, and saving/reusing the selected model/transport.

**Mocked:** Ollama inference/tag detection, all model downloads, permission input,
download boundary fixtures, and Windows prerequisite decisions. These tests do
not demonstrate actual Qwen quality or actual Windows CMD/PowerShell execution.

**Real Linux:** temporary SQLite ordering/checkout/session/receipt regressions,
FastAPI/uvicorn HTTP readiness/dashboard serving and owned-process cleanup. A live
PyPI metadata preflight found 14 compatible Linux wheels totaling 3,086,738 bytes;
it downloaded metadata only, not wheels/models/installers.

**Unverified:** real Windows 10/11 x64 double-click workflows, PowerShell 5.1 parsing
and registry/signature/install behavior, drive-space checks, Windows process cleanup,
real Ollama pulls, Qwen2.5 3B/7B/other size compatibility and Taglish quality.
No real Ollama service/model or PowerShell is installed in this test environment.

## Windows acceptance checks

1. Extract a fresh ZIP to a folder with spaces; double-click setup/run, with and
   without compatible Python. Verify Python consent/signature and `.venv` repair.
2. Test separate Ollama installations containing only 3B, only 7B, both, another
   Qwen2.5 size, and none. Check displayed choices and real read-only probes.
3. Decline every optional download and confirm setup completes and ordering works.
4. Change models with configure_ai.bat, restart, and confirm the exact saved model.
5. Remove the selected model and stop Ollama; run still opens the dashboard,
   explains AI unavailability, retains preferences, and never substitutes a model.
6. Enable AI later; verify ai_chat.bat starts/reuses Ollama and cleans up only its
   own service. Check both native and structured transport on actual models.
7. Verify real manifest sizes, disk allowances on the actual model drive, cancelled
   pulls, memory/timeouts, and the optional deeper disposable Phase 4 smoke script.
8. Repeat setup with existing orders, receipts, sessions and modified stock;
   compare records before/after. No real restaurant data should enter model probes.
