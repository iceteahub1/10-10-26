# Perpetual Bistro AI Virtual Waiter

Phase 4 adds real local Ollama/Qwen2.5 HTTP inference, validated dynamic tools,
bounded persistent conversation context, and a labeled AI CLI. The Phase 3 FSM
and transaction-safe Phase 2 order service continue to enforce checkout safety.
See [Phase 4 setup, architecture and validation](docs/phase4-validation.md),
[Phase 3](docs/phase3-validation.md), and [Phase 2](docs/phase2-validation.md).

## AI conversation (Phase 4)

Install/start Ollama and download the exact model, then run from CMD:

```bat
ollama pull qwen2.5:7b
.venv\Scripts\python.exe virtual_waiter_prototype.py --ai --table 3
.venv\Scripts\python.exe scripts\verify_phase4.py
```

The verification script uses **real Qwen** and disposable SQLite. Try `Dalawang
Chicken Adobo po.`, `Magkano na total?`, `Can I review my order?`, then `Yes, confirm
my order.` Inspect the returned cart, tool traces and receipt. Edits require a new
review. Responses select validated database facts and constrained questions;
free-form factual prose is blocked. Configure `BISTRO_OLLAMA_TIMEOUT=120` in `.env`
for slow loading. No cloud AI or API key is used; after setup operation is offline.

**Validation:** 92 automated tests pass on Linux/Python 3.12, including all Phase
1–3 tests. Phase 4 uses controlled mock model responses with real SQLite. Local
Ollama is unavailable in this cloud environment; real Qwen/Taglish quality and
Windows execution remain unverified. See the linked validation checklist.
`run.bat` continues to launch the Phase 1 dashboard; the AI interface is the CLI.

## Terminal ordering (Phase 3)

After dependencies are installed, run `.venv\Scripts\python.exe virtual_waiter_prototype.py`
on Windows or `.venv/bin/python virtual_waiter_prototype.py` on Linux. The CLI
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
5. Approve installation of the required libraries when asked. They are installed
   into this project's `.venv`. If that environment is broken or copied, setup
   offers to keep it as a backup and create a fresh one. **Restaurant data is kept.**
6. If Ollama is missing, approve opening its **official download page**, then click
   the Windows download and finish that installer. Return to the setup window and
   press Enter. Setup automatically detects Ollama again and checks its service.
7. Approve the exact configured Qwen model download. The default `qwen2.5:7b`
   downloads about **4.7 GB**; allow at least **6 GB free storage** for it, plus
   space for Python/libraries. Internet is needed for initial downloads. Other
   Qwen tags have different sizes. Model presence does not prove sufficient RAM/VRAM.
8. Wait for **Installation complete!** Then double-click **run.bat**. The existing
   dashboard opens in your default browser. Keep the launcher window open;
   press **Ctrl+C** there when finished.

Permission prompts default to **No**. Declining safely stops that step; rerun
`setup.bat` when ready. Setup never deletes SQLite records, receipts, or inventory.
Normally no administrator account is needed. Only a prerequisite's official
installer or your organization's Windows policy may require extra permission.

The automatic Python release is pinned because 3.12.10 is the last official
Python 3.12 Windows installer release. Already installed compatible later 3.12
builds are accepted after probing. Windows validates the installer file digest,
trusted certificate chain and expected signed publisher; verification failure
stops installation. Python 3.13 may remain installed alongside the application.
The existing `py` launcher's own automatic version selection may discover the
new installation; application startup always uses the verified project `.venv`.

After successful setup, **run.bat** performs offline checks of the environment,
setup record and pinned libraries. It offers setup if they are missing or broken,
and offers repair for missing Ollama/model prerequisites. Unchanged completed
setup reuses installed libraries and models without repeating downloads. One
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
  Ollama logs. Choosing another exact Qwen2.5 tag requires explicit configuration
  and local validation; setup never silently replaces the model.

Advanced checks (optional; group leads only):

```bat
run.bat --check
run.bat --no-browser
setup.bat --pull-model
.venv\Scripts\python.exe -m unittest discover -s tests -v
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test_windows_bootstrap.ps1
```

`--pull-model` still asks for download permission. `--check` requires a running
Ollama service and starts no services after prerequisite checks.
See [installer design, test results and Windows acceptance checklist](docs/installer-validation.md).

## Configuration and paths

Copy `.env.example` to `.env` only if overrides are needed. Environment variables
have precedence. The default dashboard is loopback port 8000, and Ollama is
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
install Python/Ollama on the destination, and rerun `setup.bat` to recreate the
virtual environment. Recorded project locations detect copied environments.
Ollama models are stored by Ollama, outside this project. Archived environments
are named `.venv-backup-*` and can be removed manually when no longer needed.

## Offline requirements

Initial dependency and model installation normally needs internet access. After
installation the dashboard and local prerequisite checks need no external AI
service; AI inference remains local. Fonts/styles are served locally.
A fully offline first installation needs separately prepared Python/Ollama
installers, compatible dependency wheels, and an available local model.
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
- **Model missing:** run `ollama pull qwen2.5:7b` (or your configured exact tag).
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
