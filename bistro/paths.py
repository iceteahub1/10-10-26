"""Paths never depend on the caller's working directory."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
DATA = ROOT / "data"
DATABASE = DATA / "restaurant.db"
LOGS = ROOT / "logs"
RUNTIME = ROOT / "runtime"
def prepare_directories():
    for directory in (DATA, LOGS, RUNTIME):
        directory.mkdir(exist_ok=True)
