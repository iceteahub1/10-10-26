# Phase 1 validation

Validated in the Linux cloud environment using 64-bit Python 3.12.14:

- Fresh project-local virtual environment and installation of pinned requirements.
- `pip check`: no broken requirements.
- `python -m unittest discover -s tests -v`: 11 tests passed.
- Real Uvicorn launch, matching readiness identity, HTML/CSS requests, and cleanup.
- Configuration validation, working-directory-independent paths, exact model-tag
  selection, existing-service reuse, and refusal to start services in check mode.
- Failed setup preserved an existing data file and removed stale success state.
- Real dependency installation/check steps repeated twice, with Ollama/model
  presence simulated. This does not establish real Ollama readiness.
- Windows AMD64 dependency wheels downloaded for Python 3.11 and 3.12;
  Windows-only colorama wheel verified separately. This is artifact availability,
  not a Windows installation test.
- Required CLI currently emits the explicit Phase 1 placeholder message.

No real Ollama/Qwen inference, Windows batch execution, Windows browser launch,
or Windows process management was tested. Python 3.11 was not executed here.
No database schema initialization or restaurant behavior is implemented.
The setup success marker used during simulated validation was removed so it
cannot imply real Ollama/model checks succeeded on this machine.

## Local Windows acceptance checklist

1. Use a writable folder containing spaces, with 64-bit Python 3.11/3.12.
2. Install/start Ollama and double-click setup.bat.
3. Verify missing-model instructions or explicitly approve its download.
4. Confirm successful setup and `.venv\\Scripts\\python.exe` existence.
5. Repeat setup and confirm existing data remains unchanged.
6. Double-click run.bat; confirm browser opens only after dashboard readiness.
7. Press Ctrl+C; confirm the application stops and existing Ollama stays running.
8. Test run.bat --check and run.bat --no-browser from CMD.
9. Test missing setup, missing model, unavailable Ollama, and occupied app port.
10. Move/copy the project without reusing .venv; run setup again.
11. With prerequisites prepared, disconnect internet and run the dashboard.
12. Run `.venv\\Scripts\\python.exe -m unittest discover -s tests -v`.
