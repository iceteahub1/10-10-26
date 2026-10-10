"""Double-click terminal ordering launcher, with optional owned Ollama service."""
import argparse
import subprocess
import sys
from common import ROOT, ensure_ollama, has_model, stop_owned
from launch_application import check_setup
sys.path.insert(0, str(ROOT))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ai', action='store_true')
    args = parser.parse_args()
    check_setup()
    owned = None
    try:
        if args.ai:
            from bistro.config import load_settings
            settings = load_settings()
            if not settings.ai_enabled:
                raise RuntimeError('AI chat unavailable: disabled. Double-click configure_ai.bat to enable it, or ordering.bat for non-AI ordering.')
            tags, owned = ensure_ollama(settings)
            if not has_model(tags, settings.model):
                raise RuntimeError(f'AI chat unavailable: selected model {settings.model} is missing. Double-click configure_ai.bat. No other model was selected.')
        return subprocess.call([sys.executable, str(ROOT / 'virtual_waiter_prototype.py')] + (['--ai'] if args.ai else []), cwd=ROOT)
    except KeyboardInterrupt:
        return 0
    finally:
        stop_owned(owned)

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
