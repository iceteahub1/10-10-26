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
import webbrowser
from common import ROOT, PYTHON, MARKER, compatible, require_venv, ensure_ollama, has_model, local_json, ollama_executable, stop_owned, verify_libraries
sys.path.insert(0, str(ROOT))

def fingerprint():
    return hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest()

def confirm(message):
    return sys.stdin.isatty() and input(message + ' [y/N] ').strip().lower() in ('y', 'yes')


def validate_environment(target, python):
    """Bounded interpreter check plus recorded project origin; no data mutation."""
    try:
        if not python.is_file() or not (target / '.bistro-root').is_file():
            return False
        if (target / '.bistro-root').read_text(encoding='utf-8') != str(ROOT):
            return False
        result = subprocess.run([str(python), '-I', '-c',
            "import sys,struct,json;print(json.dumps({'prefix':sys.prefix,'version':list(sys.version_info[:2]),'bits':struct.calcsize('P')*8,'venv':sys.prefix!=sys.base_prefix}))"],
            capture_output=True, text=True, timeout=10)
        info = json.loads(result.stdout)
        return (result.returncode == 0 and Path(info['prefix']).resolve() == target.resolve()
                and info['version'] in ([3, 11], [3, 12]) and info['bits'] == 64 and info['venv'])
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False


def prepare_environment():
    target = ROOT / '.venv'
    if target.exists() and not validate_environment(target, PYTHON):
        print('The application environment is broken or was copied from another folder/computer.')
        if not confirm('Keep a backup of only .venv and create a fresh environment? Restaurant data will stay untouched.'):
            raise RuntimeError('Environment recreation was declined. Nothing was deleted. Double-click setup.bat when ready.')
        backup = ROOT / ('.venv-backup-' + str(time.time_ns()))
        target.rename(backup)
        print(f'Old environment safely archived as {backup.name}.')
    if not target.exists():
        print('Creating application environment...')
        venv.EnvBuilder(with_pip=True).create(target)
        (target / '.bistro-root').write_text(str(ROOT), encoding='utf-8')
    if not validate_environment(target, PYTHON):
        raise RuntimeError('The application environment could not be verified. Check folder permissions, then rerun setup.bat.')


def ensure_ollama_installed():
    try:
        return ollama_executable()
    except RuntimeError:
        print('Ollama is not installed. Its official Windows installer is available at https://ollama.com/download/windows.')
        print('Internet is needed for installation. Follow the official installer; Windows may request permission.')
        if not confirm('Open the official Ollama download page in your browser?'):
            raise RuntimeError('Ollama installation was declined. Double-click setup.bat when you are ready.')
        if not webbrowser.open('https://ollama.com/download/windows'):
            raise RuntimeError('The browser could not open. Visit https://ollama.com/download/windows, install Ollama, then rerun setup.bat.')
        if not sys.stdin.isatty():
            raise RuntimeError('Install Ollama using the opened official page, then rerun setup.bat.')
        input('Install Ollama from that page, then press Enter here to continue (or Ctrl+C to stop). ')
        try:
            return ollama_executable()
        except RuntimeError as exc:
            raise RuntimeError('Ollama is still missing. Finish its official installer, then double-click setup.bat again.') from exc


def install_dependencies():
    from download_policy import dependency_plan, approve_download
    import tempfile
    print('Checking pinned library download sizes from official PyPI metadata...')
    try:
        size, manifest = dependency_plan(ROOT / 'requirements.txt')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Fail closed: do not run pip with an unverified or unbounded plan.
        raise RuntimeError('Library download sizes/source hashes could not be verified. Check internet and rerun setup. No downloads were started.') from exc
    required = size * 3 + 100_000_000 if size is not None else None
    if not approve_download('Required pinned libraries', size, required,
                            optional=False, confirm=confirm, location=ROOT):
        raise RuntimeError('Library installation was declined. Double-click setup.bat when ready.')
    try:
        with tempfile.TemporaryDirectory(prefix='bistro-libraries-') as folder:
            locked = Path(folder) / 'verified-requirements.txt'
            locked.write_text(manifest, encoding='utf-8')
            subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
                            '--no-deps', '--only-binary=:all:', '--require-hashes',
                            '-r', str(locked)], check=True, cwd=ROOT)
        verify_libraries(ROOT / 'requirements.txt')
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('Required libraries could not be installed. Check your internet connection and the pip message above, then rerun setup.bat. No restaurant records were removed.') from exc


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
        prepare_environment()
        return subprocess.call([str(PYTHON), str(Path(__file__).resolve()), "--inside"] + (["--pull-model"] if args.pull_model else []), cwd=ROOT)
    require_venv()
    completed = False
    if MARKER.is_file():
        try:
            saved = json.loads(MARKER.read_text(encoding='utf-8'))
            completed = type(saved) is dict and saved.get('root') == str(ROOT) and saved.get('requirements') == fingerprint()
            if completed:
                verify_libraries(ROOT / 'requirements.txt')
                import fastapi, uvicorn, dotenv
        except (OSError, ValueError, ImportError, RuntimeError, subprocess.SubprocessError):
            completed = False
    MARKER.unlink(missing_ok=True)
    # Reuse an unchanged completed setup without repeating external installations.
    if not completed:
        install_dependencies()
    from bistro.config import load_settings
    from bistro.paths import prepare_directories
    prepare_directories()
    settings = load_settings()
    from configure_ai import configure
    configure(settings)
    from bistro.database import initialize_database
    initialize_database(ROOT / "data" / "restaurant.db")
    MARKER.write_text(json.dumps({"root": str(ROOT), "requirements": fingerprint(), "python": sys.version.split()[0]}, indent=2), encoding="utf-8")
    print("Installation complete! Double-click run.bat to open the dashboard.")
    print("Existing restaurant records, receipts and inventory were preserved.")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f"SETUP FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
