"""Standard-library bootstrap and local prerequisite checks."""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
MARKER = ROOT / "runtime/setup.json"

def compatible():
    return sys.version_info[:2] in ((3, 11), (3, 12)) and struct.calcsize("P") == 8

def require_venv():
    if not compatible() or Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        raise RuntimeError("Use this project's .venv with 64-bit Python 3.11/3.12. Run setup.bat.")

def local_json(url):
    # Local traffic must not be sent through an environment HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=2) as response:
        return json.load(response)

def ollama_executable():
    found = shutil.which("ollama")
    if found:
        return found
    if os.name == "nt":
        candidate = Path(os.getenv("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("Ollama not found. Install it from https://ollama.com/download/windows, then rerun setup.")

def wait_for(url, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            return local_json(url)
        except (OSError, ValueError):
            time.sleep(.25)
    raise RuntimeError(f"Local service did not become ready: {url}")

def ensure_ollama(settings, start=True):
    executable = ollama_executable()
    try:
        return local_json(settings.ollama_url + "/api/tags"), None
    except (OSError, ValueError):
        if not start:
            raise RuntimeError("Ollama is installed but not responding. Start Ollama and retry.")
    default_url = os.getenv("OLLAMA_HOST", "127.0.0.1:11434")
    if default_url not in ("127.0.0.1:11434", "http://127.0.0.1:11434") or settings.ollama_url != "http://127.0.0.1:11434":
        raise RuntimeError("Start your configured local Ollama service manually, then retry.")
    from bistro.paths import LOGS
    LOGS.mkdir(exist_ok=True)
    with (LOGS / "ollama.log").open("ab") as log:
        process = subprocess.Popen([executable, "serve"], stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    try:
        return wait_for(settings.ollama_url + "/api/tags"), process
    except Exception:
        stop_owned(process)
        raise RuntimeError("Ollama failed to start. See logs/ollama.log.")

def has_model(tags, model):
    return any(x.get("name") == model or x.get("model") == model for x in tags.get("models", []))

def stop_owned(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def verify_libraries(requirements=None):
    """Check installed versions against active pinned requirements, entirely offline."""
    from importlib.metadata import version, PackageNotFoundError
    from pip._vendor.packaging.requirements import Requirement
    path = Path(requirements) if requirements is not None else ROOT / 'requirements.txt'
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            installed = version(requirement.name)
        except PackageNotFoundError as exc:
            raise RuntimeError('Required library missing: ' + requirement.name + '. Rerun setup.bat.') from exc
        if installed not in requirement.specifier:
            raise RuntimeError('Required library version mismatch: ' + requirement.name + '. Rerun setup.bat.')
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True, capture_output=True)
