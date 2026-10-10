"""Launch the Phase 1 dashboard with bounded readiness and owned-process cleanup."""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
import webbrowser
from common import ROOT, MARKER, require_venv, local_json, ensure_ollama, has_model, stop_owned
sys.path.insert(0, str(ROOT))

def check_setup():
    require_venv()
    if not MARKER.is_file():
        raise RuntimeError("Setup is incomplete. Run setup.bat first.")
    state = json.loads(MARKER.read_text(encoding="utf-8"))
    digest = hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest()
    if state.get("root") != str(ROOT) or state.get("requirements") != digest:
        raise RuntimeError("Project moved or dependencies changed. Rerun setup.bat.")
    import fastapi, uvicorn, dotenv

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--check", action="store_true", help="Check setup and prerequisites without starting services")
    args = parser.parse_args()
    check_setup()
    from bistro.config import load_settings
    from bistro.paths import prepare_directories, LOGS
    settings = load_settings()
    prepare_directories()
    app_process = ollama_process = None
    url = f"http://{settings.host}:{settings.port}"
    token = uuid.uuid4().hex
    try:
        tags, ollama_process = ensure_ollama(settings, start=not args.check)
        if not has_model(tags, settings.model):
            raise RuntimeError(f"Missing {settings.model}. Run setup.bat; startup never downloads models.")
        if args.check:
            print("Setup, imports, Ollama API, and exact model tag checks passed.")
            return 0
        with socket.socket() as sock:
            if sock.connect_ex((settings.host, settings.port)) == 0:
                raise RuntimeError(f"Port {settings.port} is occupied. Close the existing application or change BISTRO_PORT. No process was terminated.")
        with (LOGS / "application.log").open("ab") as log:
            app_process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", settings.host, "--port", str(settings.port)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "BISTRO_LAUNCH_TOKEN": token})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if app_process.poll() is not None:
                raise RuntimeError("Web server exited. See logs/application.log.")
            try:
                health = local_json(url + "/health")
                if health.get("launch_token") == token and health.get("project") == "perpetual-bistro":
                    break
            except (OSError, ValueError):
                pass
            time.sleep(.25)
        else:
            raise RuntimeError("Dashboard did not become ready. See logs/application.log.")
        print(f"Dashboard ready: {url}. Keep this window open. Press Ctrl+C to stop.")
        if not args.no_browser and not webbrowser.open(url):
            print("Browser could not be opened; open the address above manually.")
        code = app_process.wait()
        if code:
            raise RuntimeError(f"Server exited with code {code}. See logs/application.log.")
    except KeyboardInterrupt:
        print("Stopping application...")
    finally:
        stop_owned(app_process)
        stop_owned(ollama_process)
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
