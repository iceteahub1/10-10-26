"""Interactive optional AI setup. Core installation never depends on Ollama."""
import os
from pathlib import Path
import subprocess
import sys
from common import ROOT, ensure_ollama, ollama_executable, local_json, has_model, stop_owned
sys.path.insert(0, str(ROOT))
from bistro.ai_preferences import valid_model, save_preferences, read_preferences
from bistro.model_check import check_model
from download_policy import model_download_size, approve_download

def confirm(message):
    return sys.stdin.isatty() and input(message + ' [y/N] ').strip().lower() in ('y', 'yes')

def installed_models(tags):
    models = {item.get('name') or item.get('model') for item in tags.get('models', [])}
    return sorted((m for m in models if valid_model(m)), key=lambda m: (m != 'qwen2.5:7b', m != 'qwen2.5:3b', m))

def download_model(settings, model, ask=confirm):
    try:
        size = model_download_size(model)
    except (OSError, ValueError, KeyError, TypeError):
        size = None
    # Conservative storage allowance includes transfer/cache overhead. For unknown tags
    # do not invent a size or a disk guarantee.
    required = size * 2 + 500_000_000 if size is not None else None
    location = Path(os.getenv('OLLAMA_MODELS', str(Path.home() / '.ollama/models')))
    while not location.exists() and location != location.parent:
        location = location.parent
    print(f'Optional Ollama model. Checking storage at {location}. If your Ollama service uses another model folder, verify free space there too. Smaller models may fail tool routing. Internet is required.')
    if not approve_download(model, size, required, optional=True, confirm=ask, location=location):
        return False
    subprocess.run([ollama_executable(), 'pull', model], check=True,
                   env={**os.environ, 'OLLAMA_HOST': settings.ollama_url})
    return has_model(local_json(settings.ollama_url + '/api/tags'), model)

def configure(settings, *, change=False, ask=confirm, choose=None, save=save_preferences):
    owned = None
    choose = choose or (lambda prompt: input(prompt) if sys.stdin.isatty() else '0')
    if not change and not settings.ai_enabled:
        print('AI remains disabled. Double-click configure_ai.bat to enable it later.')
        return False
    try:
        tags, owned = ensure_ollama(settings)
        models = installed_models(tags)
        print('Installed Qwen2.5 models: ' + (', '.join(models) or 'none') + '. Every selection is tested before enabling AI.')
        # Recheck a saved selection; never substitute another installed model.
        if not change and settings.ai_enabled and settings.model in models and read_preferences() is not None:
            ok, transport, detail = check_model(settings.ollama_url, settings.model)
            print(detail)
            if ok and ask(f'Keep using {settings.model}?'):
                save(True, settings.model, transport)
                return True
        while True:
            print('\nChoose optional AI (0 = continue without AI):')
            for number, model in enumerate(models, 1):
                print(f'{number}. Use installed {model}' + (' (preferred)' if model == 'qwen2.5:7b' else ''))
            options = {str(i): m for i, m in enumerate(models, 1)}
            for model in ('qwen2.5:3b', 'qwen2.5:7b'):
                if model not in models:
                    number = str(len(options) + 1)
                    options[number] = model
                    print(f'{number}. Optional download {model}' + (' (lightweight)' if model.endswith(':3b') else ' (preferred)'))
            selected = choose('Enter a number [0]: ').strip() or '0'
            if selected == '0':
                save(False, settings.model, settings.ai_transport)
                print('AI chat unavailable. Core installation and deterministic ordering remain available. Enable later with configure_ai.bat.')
                return False
            if selected not in options:
                print('Please enter one of the displayed numbers.')
                continue
            model = options[selected]
            if model not in models:
                try:
                    if not download_model(settings, model, ask):
                        print('Download skipped. Choose an installed model or 0 for non-AI mode.')
                        continue
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    print(f'Optional model download failed: {exc}. Core installation can continue.')
                    continue
                models.append(model)
            ok, transport, detail = check_model(settings.ollama_url, model)
            print(detail)
            if ok:
                save(True, model, transport)
                print(f'Saved AI model: {model}. The browser dashboard does not expose AI chat; use ai_chat.bat.')
                return True
            print('Choose another model or 0 for non-AI mode.')
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'AI unavailable: {exc}. Continuing without AI. Install/start Ollama from https://ollama.com/download/windows and use configure_ai.bat later.')
        if read_preferences() is None:
            save(False, settings.model, settings.ai_transport)
        # Preserve an enabled saved choice on transient failure; launch reports unavailability.
        return False
    finally:
        stop_owned(owned)

def main():
    from bistro.config import load_settings
    from common import require_venv
    require_venv()
    try:
        read_preferences()
    except ValueError:
        # A corrupt preference file may be replaced explicitly through this launcher.
        from bistro.ai_preferences import PREFERENCES
        if not confirm('AI configuration is invalid. Archive it and choose again?'):
            return 1
        import time
        PREFERENCES.rename(PREFERENCES.with_name('ai-backup-' + str(time.time_ns()) + '.json'))
    settings = load_settings()
    configure(settings, change=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
