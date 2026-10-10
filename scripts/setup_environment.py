"""Repeatable environment setup with non-destructive SQLite initialization."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import venv
from common import ROOT, PYTHON, MARKER, compatible, require_venv, ensure_ollama, has_model, local_json, ollama_executable, stop_owned
sys.path.insert(0, str(ROOT))

def fingerprint():
    return hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--pull-model", action="store_true")
    args = parser.parse_args()
    if args.probe:
        return 0 if compatible() else 1
    if not compatible():
        raise RuntimeError("Install 64-bit CPython 3.11 or 3.12.")
    if not args.inside:
        target = ROOT / ".venv"
        if target.exists():
            valid = False
            if PYTHON.is_file():
                result = subprocess.run([str(PYTHON), "-c", "import sys,struct;print(sys.prefix);print(sys.version_info[:2] in ((3,11),(3,12)) and struct.calcsize('P')==8)"], capture_output=True, text=True)
                lines = result.stdout.strip().splitlines()
                valid = result.returncode == 0 and len(lines) == 2 and Path(lines[0]).resolve() == target.resolve() and lines[1] == "True"
            # A recorded location prevents reuse of a moved/copy-installed environment.
            origin = target / ".bistro-root"
            if origin.exists():
                try:
                    valid = valid and origin.read_text(encoding="utf-8") == str(ROOT)
                except (ValueError, OSError):
                    valid = False
            else:
                valid = False
            if not valid:
                if not sys.stdin.isatty() or input("Unverified/copied .venv. Archive it and recreate? [y/N] ").lower() != "y":
                    raise RuntimeError("Move/remove only .venv, then rerun setup. Existing data is preserved.")
                target.rename(ROOT / (".venv-backup-" + str(time.time_ns())))
        if not target.exists():
            print("Creating project .venv...")
            venv.EnvBuilder(with_pip=True).create(target)
            (target / ".bistro-root").write_text(str(ROOT), encoding="utf-8")
        return subprocess.call([str(PYTHON), str(Path(__file__).resolve()), "--inside"] + (["--pull-model"] if args.pull_model else []), cwd=ROOT)
    require_venv()
    MARKER.unlink(missing_ok=True)
    print("Installing pinned dependencies...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(ROOT / "requirements.txt")], check=True, cwd=ROOT)
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    from bistro.config import load_settings
    from bistro.paths import prepare_directories
    prepare_directories()
    settings = load_settings()
    owned = None
    try:
        tags, owned = ensure_ollama(settings)
        if not has_model(tags, settings.model):
            print(f"Missing model: {settings.model}. Download requires internet and several GB of storage.")
            pull = args.pull_model or (sys.stdin.isatty() and input("Download it now? [y/N] ").lower() == "y")
            if not pull:
                raise RuntimeError(f"Run ollama pull {settings.model}, then rerun setup.bat.")
            subprocess.run([ollama_executable(), "pull", settings.model], check=True, env={**os.environ, "OLLAMA_HOST": settings.ollama_url})
            if not has_model(local_json(settings.ollama_url + "/api/tags"), settings.model):
                raise RuntimeError("Model download finished but exact tag is unavailable.")
        from bistro.database import initialize_database
        initialize_database(ROOT / "data" / "restaurant.db")
        MARKER.write_text(json.dumps({"root": str(ROOT), "requirements": fingerprint(), "python": sys.version.split()[0]}, indent=2), encoding="utf-8")
        print("SUCCESS: Phase 1 environment ready. Run run.bat.")
        print("Phase 2 database ready. Existing records and stock were preserved.")
    finally:
        stop_owned(owned)
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f"SETUP FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
