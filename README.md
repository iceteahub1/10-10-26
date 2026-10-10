# Perpetual Bistro AI Virtual Waiter

Phase 2 adds a real SQLite menu, persistent table sessions, editable carts,
transaction-safe checkout, and itemized receipts to the working Phase 1 setup,
launcher, configuration, and responsive infrastructure dashboard. FSM and AI
conversation remain deferred to Phase 3. The dashboard still shows infrastructure
status; ordering is currently available through the Python service API.
See [Phase 2 usage and validation](docs/phase2-validation.md).

## Windows 10/11 installation

1. Place the project in a writable folder. Paths containing spaces are supported
   by design; avoid protected locations such as Program Files.
2. Install **64-bit CPython 3.11 or 3.12** from
   https://www.python.org/downloads/windows/. Include the Python launcher.
3. Install Ollama from https://ollama.com/download/windows and launch it.
4. Double-click `setup.bat`. It detects Python, creates `.venv`, installs exact
   pinned dependencies, checks package consistency, and checks local Ollama.
5. The default exact model is `qwen2.5:7b`. Setup offers to download a missing
   model, or you can run `ollama pull qwen2.5:7b` yourself and rerun setup.
   Downloads require internet and several GB of storage. Model presence does
   not prove your hardware can run inference; that is tested in a later phase.
6. Successful setup prints `SUCCESS: Phase 1 environment ready`.

Setup creates data/log/runtime directories and safely initializes
`data/restaurant.db` after prerequisite checks pass. The menu is seeded once.
Repeated setup preserves menu edits, stock, sessions, carts, and receipts.
Unknown existing schemas are rejected without deleting or resetting records.
Repeated setup reuses a validated environment. An invalid or moved environment
can be archived with your consent; no application data is removed.

## One-click startup

Double-click `run.bat`. It uses `.venv\Scripts\python.exe` without activation,
checks setup/imports and the exact model, reuses running Ollama or starts it if
needed, starts the local dashboard, and opens the default browser after readiness.
Keep the console open. Press **Ctrl+C** to stop; the launcher only stops processes
it started. Startup never downloads a model. Closing the console forcibly is
not a reliable cleanup method; use Ctrl+C.

The launcher refuses an occupied dashboard port instead of killing another
application. Logs are in `logs/application.log` and `logs/ollama.log`.
Failure messages remain visible because both batch files pause before exiting.

Optional commands from CMD in the project folder:

```bat
run.bat --check
run.bat --no-browser
setup.bat --pull-model
.venv\Scripts\python.exe virtual_waiter_prototype.py
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The CLI currently reports Phase 1 status; it does not accept orders yet.
`--check` requires an already running Ollama and starts no services.

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
service; later inference will remain local. Fonts/styles are served locally.
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
  locally. Choose a tested smaller Qwen2.5 tag in a later integration phase.
- **Browser did not open:** use the address printed by the launcher.

## Structure and scope

`bistro/` contains shared paths/configuration, SQLite initialization, session
persistence, and the ordering service; AI/FSM modules remain reserved.
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
