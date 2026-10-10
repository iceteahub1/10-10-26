# Perpetual Bistro AI Virtual Waiter

Phase 4 adds real local Ollama/Qwen2.5 HTTP inference, validated dynamic tools,
bounded persistent conversation context, and a labeled AI CLI. The Phase 3 FSM
and transaction-safe Phase 2 order service continue to enforce checkout safety.
See [Phase 4 setup, architecture and validation](docs/phase4-validation.md),
[Phase 3](docs/phase3-validation.md), and [Phase 2](docs/phase2-validation.md).

## AI conversation (Phase 4)

Double-click **configure_ai.bat** to enable AI or change models, then
**ai_chat.bat** for the existing terminal conversation. Setup detects installed
Qwen2.5 tags, prefers 7B in the menu, and lets you select 3B or another compatible
installed size. Every selection must pass a read-only compatibility smoke check.
No 7B download is required. Smaller models can fail the check or perform worse;
passing is not a guarantee of real ordering/Taglish quality.

For group leads, the optional deeper verification is:

```bat
.venv\Scripts\python.exe scripts\verify_phase4.py
```

The verification script uses **real Qwen** and disposable SQLite. Try `Dalawang
Chicken Adobo po.`, `Magkano na total?`, `Can I review my order?`, then `Yes, confirm
my order.` Inspect the returned cart, tool traces and receipt. Edits require a new
review. Responses select validated database facts and constrained questions;
free-form factual prose is blocked. Configure `BISTRO_OLLAMA_TIMEOUT=120` in `.env`
for slow loading. No cloud AI or API key is used; after setup operation is offline.

**Validation:** see [flexible AI setup validation](docs/flexible-ai-validation.md)
for current results and the distinction between mocked Ollama/download tests and
real SQLite/HTTP checks. Real Windows and Qwen quality still require local testing.
`run.bat` launches the existing infrastructure dashboard; it has no browser
ordering or AI chat interface and now displays AI availability explicitly.

## Terminal ordering (Phase 3)

After setup, double-click **ordering.bat** on Windows, or run
`.venv/bin/python virtual_waiter_prototype.py` on Linux. The CLI
initializes/migrates SQLite without requiring Ollama. Use `--db PATH` for a separate
lab database. Enter `help` for all actions, `state` for persistent context, and
`quit` to preserve the session. Example inputs:

```jsonl
{"action":"establish_session","arguments":{"table_no":3}}
{"action":"browse_menu","arguments":{"category":"Mains"}}
{"action":"add_item","arguments":{"item_name":"Chicken Adobo","qty":2,"instructions":"less salt"}}
{"action":"request_review","arguments":{}}
{"action":"confirm_order","arguments":{"confirmed":true}}
```

Resume with the same table and optional printed session ID. After confirmation or
cancellation, `new_session` starts a separate customer context. Cart edits invalidate
review; confirmation requires a fresh review of the unchanged cart. Commands in this default mode are explicit structured inputs; `--ai` enables natural-language model routing.

## Easy installation for Windows 10/11 (Intel/AMD 64-bit)

You do not need to type commands, edit PATH, or activate a Python environment.

1. Download/extract the whole project ZIP into a writable folder such as Documents.
   Do not run the files inside the ZIP. Keep the project files together.
2. Double-click **setup.bat**. Keep its window open and read each permission prompt.
3. Setup looks for **64-bit Python 3.12 or 3.11**, preferring 3.12. It checks the
   registry, normal installation folders, available launchers, and every PATH
   candidate. Python 3.13, 32-bit Python and Store shortcuts are not selected.
4. If compatible Python is missing, type **y** when offered its installation.
   Setup downloads official Python **3.12.10 x64** over HTTPS from python.org,
   verifies its Windows signature and Python Software Foundation publisher, and
   installs it in a dedicated folder under your Windows account. Existing Python
   installations are not overwritten; global PATH and file associations are not
   changed, and another Python launcher is not installed.
5. Setup installs pinned required libraries into this project's `.venv`. It checks
   official PyPI wheel sizes and SHA-256 hashes first. Required verified downloads
   below **100 MB (100,000,000 bytes)** can run automatically. At exactly 100 MB,
   above it, or with an unknown size, setup requests approval. Known downloads
   show their size, a conservative free-space allowance, and available disk space.
6. If Ollama is available, choose an installed Qwen2.5 model. 3B is lightweight;
   7B is preferred. Other installed Qwen2.5 tags are offered and must pass the
   same check. If no model is installed, choose an optional 3B/7B download or **0**
   to continue without AI. Every optional download asks permission, even if small.
   Model manifests come from the official Ollama registry; Ollama verifies its
   downloaded blobs. Unknown sizes are stated explicitly and require consent;
   their storage requirements cannot be verified in advance.
7. If Ollama is missing, unavailable, or you skip AI, **core installation succeeds**.
   To enable AI later, install Ollama from its official Windows download page
   (https://ollama.com/download/windows), start it, and double-click
   **configure_ai.bat**. No privileged installer runs silently.
8. Wait for **Installation complete!** Then double-click **run.bat**. Keep its
   window open and press **Ctrl+C** when finished. Use **ordering.bat** for the
   existing non-AI terminal ordering, or **ai_chat.bat** for terminal AI.

Permission prompts default to **No**. Declining an optional AI download keeps the
core application usable. Declining a required Python/library installation stops
that required step. Existing SQLite records, receipts and inventory are preserved.
Broken/copied `.venv` folders are archived only with consent, as before.

The automatic Python release is pinned because 3.12.10 is the last official
Python 3.12 Windows installer release. Already installed compatible later 3.12
builds are accepted after probing. Windows validates the installer file digest,
trusted certificate chain and expected signed publisher; verification failure
stops installation. Python 3.13 may remain installed alongside the application.
The existing `py` launcher's own automatic version selection may discover the
new installation; application startup always uses the verified project `.venv`.

After successful setup, **run.bat** performs offline checks of the environment,
setup record and pinned libraries. It offers setup if they are missing or broken,
and continues without AI if Ollama or the selected model is unavailable. Unchanged completed
setup reuses installed libraries without repeating downloads. Launch never downloads
models, prompts for a new model, or silently substitutes another model. One
project can have only one setup/launcher window running at a time. Occupied ports
are reported; unrelated applications are never terminated.

The dashboard remains the existing dashboard. Phase 4 AI conversation is available
through its documented CLI; this installer change does not build the Phase 5 UI.

### If something goes wrong

- **Download failed:** check internet and free disk space, then double-click setup
  again. Keep the whole project folder; do not remove `data`.
- **Python signature failed:** check Windows date/time and Windows certificate
  updates. Retry later or ask your group lead; setup will not run an unverified installer.
- **Python dedicated folder is incomplete:** ask your group lead to repair that
  installation. Setup refuses to overwrite an existing unverified Python folder.
- **Copied/broken environment:** approve the `.venv` backup/recreation prompt.
  Copying the source/database is supported; reusing a copied `.venv` is not.
- **Ollama still missing:** finish its official Windows installer and rerun setup.
- **Model download interrupted:** rerun setup and approve that download again.
  The exact configured model is checked after the download finishes.
- **Already running / port occupied:** return to the existing application window,
  or close it normally. Setup never kills another application's process.
- **Windows blocks PowerShell:** ask your group lead or IT. The batch files use a
  process-local execution-policy setting and do not change system policy; managed
  Windows policies can still block scripts.
- **Model too slow / out of memory:** close competing applications and check
  Ollama logs. Use **configure_ai.bat** to choose another installed model and run its compatibility check; setup never silently replaces the model.

Advanced checks (optional; group leads only):

```bat
run.bat --check
run.bat --no-browser
setup.bat --pull-model
.venv\Scripts\python.exe -m unittest discover -s tests -v
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test_windows_bootstrap.ps1
```

`--pull-model` never grants download permission or forces 7B. Selection and consent
remain interactive. `--check` starts no services; an unavailable AI model is reported
and the core check can still pass.
See [installer design, test results and Windows acceptance checklist](docs/installer-validation.md).

## Configuration and paths

Copy `.env.example` to `.env` only if overrides are needed. Environment variables
have precedence for server/tax settings. A saved selection in `runtime/ai.json`
has precedence over the legacy `BISTRO_MODEL` setting. Use **configure_ai.bat**
to change that selection without reinstalling. The default dashboard is loopback port 8000, and Ollama is
`http://127.0.0.1:11434`. Only local HTTP Ollama origins are accepted. A custom
Ollama port must already be running; automatic service startup supports the
standard port only. Model configuration requires an exact `qwen2.5:` tag.

All paths derive from the project directory, including `data/restaurant.db`,
`logs`, `runtime`, templates, and `.venv`. The configurable currency defaults to
PHP. Tax/service rates default to zero and are fractional demonstration settings,
not professor-specified rates. Phase 2 service totals use PHP and zero tax/service by default; callers can pass
validated fractional rates explicitly. Receipt totals use integer centavos and
round tax/service separately to the nearest centavo (half up).

## Moving to another computer or folder

Do **not** copy and reuse `.venv`. Copy source and any database you intend to keep,
rerun `setup.bat` to detect/install Python and recreate the
virtual environment. Recorded project locations detect copied environments.
Ollama models are stored by Ollama, outside this project. Archived environments
are named `.venv-backup-*` and can be removed manually when no longer needed.

## Offline requirements

Initial dependency and model installation normally needs internet access. After
installation the dashboard and local prerequisite checks need no external AI
service; AI inference remains local. Fonts/styles are served locally.
A fully offline first installation needs separately prepared Python/Ollama
and a preinstalled verified project library environment; AI additionally needs
Ollama and an installed compatible model. First-time library setup reads PyPI metadata.
No paid cloud AI dependency or secret is required.

## Troubleshooting

- **Python missing:** install 64-bit CPython 3.11/3.12 with the launcher. Microsoft
  Store aliases are not sufficient unless the version/architecture probe passes.
- **Setup incomplete:** rerun setup; an absent success marker blocks startup.
- **Dependency install failure:** check internet access and the console output;
  rerun setup after fixing the cause. TLS verification is never disabled.
- **Moved/invalid `.venv`:** allow setup to archive it, or move only `.venv`
  manually and rerun setup. Keep `data` intact.
- **Ollama unavailable:** start Ollama, check `ollama --version`, then rerun setup.
- **Model missing:** use `configure_ai.bat` to explicitly choose/download a model,
  or continue with `ordering.bat`. No alternative model is selected automatically.
- **Port occupied:** close your previous launcher or change `BISTRO_PORT`.
- **Server failure:** inspect `logs/application.log`; check folder permissions.
- **Insufficient memory:** the 7B model's inference requirements must be checked
  locally. Explicitly configure and locally validate a smaller exact Qwen2.5 tag if needed; the application never substitutes one.
- **Browser did not open:** use the address printed by the launcher.

## Structure and scope

`bistro/` contains shared paths/configuration, SQLite initialization, session
persistence, the ordering service, and conversation memory; `fsm_agent.py` controls the FSM.
`ai_engine.py`, `ollama_client.py` and `tool_schemas.py` provide Phase 4 AI integration.
`scripts/` implements setup and process launching. `app.py`, `templates/`, and
`static/` provide only the Phase 1 dashboard. `seed/`, `docs/`, and `tests/` hold
future supporting assets. Generated databases, caches, logs, `.venv`, and secret
configuration are ignored by Git.

Required filenames are preserved: `init_restaurant_db.sql`, `db_tools.py`,
`fsm_agent.py`, `virtual_waiter_prototype.py`, `pair_collaboration_log.md`, and
`sample_conversation_log.json`. The original PDFs/TXT remain untouched.

## Validation boundaries

Linux cloud testing can validate dependency installation, configuration, helper
logic, local HTTP readiness, and dashboard serving. It cannot prove Windows CMD
batch execution, Windows process termination, Python launcher discovery, or
opening your Windows browser. No real Qwen inference is claimed in Phase 1.

Before declaring Windows compatibility verified, test setup in a folder with
spaces, repeat setup with existing data, run/check/stop the dashboard, exercise
missing prerequisites and port conflicts, and verify your chosen model locally.
