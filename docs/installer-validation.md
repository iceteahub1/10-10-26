# Windows installer improvements

## Scope

The `installer-improvement` branch is based on `main` with Phase 4 merged.
It changes bootstrap/setup/launcher behavior only. The FSM, SQLite order service,
conversation memory, Ollama AI engine, professor-required deliverables, original
course files and dashboard architecture are preserved. No Phase 5 UI is added.

## Bootstrap without Python

`setup.bat` and `run.bat` call the Windows-supplied PowerShell executable using
project-relative paths. Neither invokes an unverified `python` or `py` command.
Windows PowerShell 5.1 syntax is used. A process-local execution-policy flag does
not change user/system execution policy or override organization policy.

`scripts/windows_bootstrap.ps1`:

- Checks Windows 10/11 x64 using Windows build number and OS/processor architecture.
- Enumerates PEP 514 registry companies/tags in HKCU/HKLM and both registry views.
- Searches standard local/system folders, the dedicated per-user installation,
  available Python launchers, and every PATH directory, including systems with no `py`.
- Probes candidates with an isolated subprocess, bounded to eight seconds; checks
  actual CPython version, pointer width, base environment, executable path, and
  `venv`/`ensurepip` imports. Registry labels and command names are not trusted as versions.
- Skips Store execution aliases and selects 3.12 before 3.11, regardless of a 3.13
  executable appearing first. Installed micro versions break ties within a minor version.
- If none are compatible, asks for permission before downloading/installing Python.

The pinned download is:

```text
https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
```

This is the last official Python 3.12 Windows installer release. Existing compatible
3.11/3.12 installations are accepted; the project never falls back to Python 3.13.
The downloader requires HTTPS python.org for every redirect, retains normal TLS
certificate verification, bounds transfer size/time and cleans only its unique
scratch download folder. Before execution, Windows Authenticode must report
`Valid` and the certificate's expected publisher must be exactly **Python Software
Foundation**. Windows checks the embedded file digest and trusted certificate
chain; this is signature-based verification, not a fabricated checksum pin.

Installation is per-user into `%LOCALAPPDATA%\PerpetualBistro\Python312`, with
`InstallAllUsers=0`, `PrependPath=0`, `AppendPath=0`, `Include_launcher=0`,
`AssociateFiles=0` and `Shortcuts=0`. Setup refuses to overwrite an existing
unverified dedicated Python folder. Installed Python is probed again before use.
Cancellation (1602), installer failure and reboot-required success are handled.
No automatic elevation or system-wide PATH change is requested. A pre-existing
`py` launcher can discover a newly registered Python through its normal version
selection; the application's own interpreter binding is always explicit.

## Environment, libraries and AI prerequisites

The verified base interpreter creates `.venv`. Existing environments must match
the current project origin, supported version, 64-bit architecture and virtual
environment prefix; the executable probe has a timeout. Invalid/copied environments
are archived only with permission, then recreated. No database directory is renamed
or removed. Failures leave the old environment backup available.

Pinned library installation requires separate permission and uses that environment's
Python. Library versions are checked against active `requirements.txt` entries,
including platform markers, and `pip check` validates their dependencies. These
validation checks are offline. A completed unchanged setup skips reinstallation;
changed/broken dependencies require permission to repair.

Missing Ollama offers **guided installation** by opening
https://ollama.com/download/windows only with permission. The user completes the
official installer, presses Enter, and setup detects Ollama again. No unofficial
Ollama binary is downloaded or executed by this project. An existing installation's
local service is reused or started through the existing owned-process helper.

The exact configured Qwen2.5 tag is checked before/after an approved model pull.
The default 7B weights are approximately 4.7 GB; setup advises at least 6 GB free
storage for the model and explains first-time internet needs. Other tags vary.
`--pull-model` is a request, not a consent bypass. Download/installation prompts
default to No and noninteractive input grants no permission. No cloud AI is used.

Setup completion is recorded only after successful prerequisite and non-destructive
SQLite initialization. Existing stock, receipts, carts and conversations are preserved.

## Startup

`run.bat` checks the project environment and setup record without requiring a global
Python. Missing/broken/moved setup prompts to run setup. Missing Ollama/model errors
use a dedicated exit code so the bootstrap can offer repair, then retry startup once.
Other errors such as occupied dashboard ports do not trigger a repair loop.

A project-specific named mutex prevents duplicate setup/launcher windows and is
held for the application's lifetime. The existing port/readiness checks and browser
launch remain intact. Only owned probe/service/server processes may be terminated;
unrelated applications are never killed. No downloads occur without setup consent.
The mutex is released on exit, including abandoned-lock recovery on the next run.

## Tests executed — October 10, 2026

Linux, Python 3.12, project-local `.venv`, plus official portable Microsoft
PowerShell **7.4.6 for Linux**, used only in ignored scratch space for tests:

```text
python -m unittest discover -s tests -v
Ran 112 tests
OK
```

All 92 existing Phase 1–4 tests pass, including the real local dashboard HTTP
integration. Twenty installer tests cover supported/unsupported virtual environments,
32-bit rejection, copied/missing/broken/timed-out environments, declined/approved
recreation, retained database bytes, permission-gated libraries, installation failures,
official Ollama guidance/redetection, model download failure/decline, pinned versions,
service-free readiness and batch entry point contracts.

One of those tests executes `scripts/test_windows_bootstrap.ps1`, which passes
**25 simulated checks** against actual bootstrap functions. These include:

- Missing Python and missing `py`; multiple versions; 3.13 first in PATH; prefer
  3.12 / accept 3.11; reject 32-bit; launcher probe and Store alias handling.
- Declined consent; failed download; signature failure; wrong publisher;
  hash mismatch; trusted-publisher fixture; installer cancellation/failure/success.
- Per-user installer switches that avoid PATH, launcher and association changes.
- Legacy batch flags; declined/approved startup repair; unchanged setup reuse;
  one prerequisite repair followed by one startup retry.

These are **simulations**, not actual Windows installations. Registry discovery,
Windows build/architecture APIs, signature trust and installer processes are mocked
in the PowerShell harness. Python tests use temporary filesystem data or mocks for
external prerequisites. No Python installer, Ollama installer or model download was
executed in this cloud environment. The existing Phase 4 model tests remain mocked;
no new real Qwen inference result is claimed.

The standard unit runner executes PowerShell scenarios when `powershell.exe` or
`pwsh` is available; otherwise it clearly skips that one test. On Windows, the
supplied PowerShell normally makes it available. Group leads can run it directly:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test_windows_bootstrap.ps1
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Real Windows acceptance checklist — unverified here

Use disposable Windows 10/11 x64 accounts/VMs before distributing the installer:

1. Place the project in a folder containing spaces and launch by double-click.
2. Try no Python; Python 3.13 first in PATH; 3.11+3.12 together; no `py`; registry-only
   or standard-folder Python; 32-bit Python; Store aliases; damaged Python.
3. Verify 3.12 is selected and its real `.venv` is 64-bit; never select 3.13.
4. Decline each permission prompt and confirm the corresponding download/install
   did not occur. Approve Python and verify the real official signature, publisher,
   certificate chain and resulting per-user installation on Windows PowerShell 5.1.
5. Confirm existing Python folders, PATH, file associations and launcher binaries
   are unchanged. Record the pre-existing launcher's automatic default selection.
6. Exercise installer cancellation, UAC/policy denial, download interruption,
   certificate failure, insufficient disk and reboot-required results.
7. Copy/break `.venv`; decline then approve recreation; check its backup and verify
   SQLite records/receipts/stock remain unchanged. Rerun setup with existing data.
8. Install missing Ollama through the official page; verify redetection, owned service
   behavior, exact model pull and interruption recovery; decline model download.
9. Double-click run; test missing setup/model/Ollama repair, occupied ports, simultaneous
   double-clicks, browser opening and Ctrl+C cleanup. Do not kill unrelated processes.
10. Disconnect internet after setup and confirm normal launch and local AI usage.
11. Run regressions on Windows Python 3.11 and 3.12, and the real Phase 4 Qwen smoke
    test if hardware permits. A model download alone does not validate inference.

Windows PowerShell 5.1, real registry enumeration, Authenticode trust, installer/UAC
behavior, Windows console/batch quoting, per-user path permissions and browser
startup were not executed on a real Windows machine. Managed Windows policy may
block script execution; ask IT rather than changing global policy automatically.

## Files

Created: `scripts/windows_bootstrap.ps1`, `scripts/test_windows_bootstrap.ps1`,
`tests/test_installer.py`, `docs/installer-validation.md`.

Created also: `.gitattributes` for portable Windows batch line endings.

Modified: `setup.bat`, `run.bat`, `scripts/common.py`, `scripts/setup_environment.py`,
`scripts/launch_application.py`, `README.md`, `tests/test_phase1.py`, `tests/test_phase2.py`.
The two existing test files were updated only to simulate the newly required consent
and Ollama installation check in their setup tests.
