"""Local AI selection, independent of restaurant data and setup markers."""
import json
import re
from bistro.paths import ROOT

PREFERENCES = ROOT / 'runtime/ai.json'

def valid_model(model):
    return isinstance(model, str) and re.fullmatch(r'qwen2\.5:[A-Za-z0-9][A-Za-z0-9_.-]*', model) is not None

def read_preferences(path=None):
    path = path or PREFERENCES
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    if (type(value) is not dict or type(value.get('enabled')) is not bool
            or not valid_model(value.get('model'))
            or value.get('transport') not in ('native', 'structured')):
        raise ValueError('Invalid AI preferences. Double-click configure_ai.bat to repair them.')
    return value

def save_preferences(enabled, model, transport='native', path=None):
    if type(enabled) is not bool or not valid_model(model) or transport not in ('native', 'structured'):
        raise ValueError('Invalid AI selection.')
    path = path or PREFERENCES
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'enabled': enabled, 'model': model, 'transport': transport}, indent=2), encoding='utf-8')
    temporary.replace(path)
