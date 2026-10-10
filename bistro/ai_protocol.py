"""Normalize model intent, then bind identities using authoritative Python state.

This adapter never executes actions. The legacy dispatcher stays strict and
session-isolated; only this model-facing boundary accepts equivalent encodings.
"""
import copy
import json
import re
from bistro.tool_schemas import TOOLS, validate_call

PROTECTED = {'id', 'session_id', 'item_id', 'line_id', 'receipt_id', 'review_token'}

def public_context(value):
    if isinstance(value, dict):
        return {k: public_context(v) for k, v in value.items() if k not in PROTECTED}
    if isinstance(value, list):
        return [public_context(v) for v in value]
    return value

INTENT_TOOLS = copy.deepcopy(TOOLS)
for tool in INTENT_TOOLS:
    name = tool['function']['name']
    params = tool['function']['parameters']
    for key in PROTECTED:
        params['properties'].pop(key, None)
        if key in params['required']:
            params['required'].remove(key)
    if name in ('check_stock', 'check_item_allergen', 'update_quantity', 'remove_item', 'update_instructions'):
        params['properties']['item_name'] = {'type': 'string'}
        params['required'].append('item_name')
    if name in ('update_quantity', 'remove_item', 'update_instructions'):
        params['properties']['current_instructions'] = {'type': 'string'}
    if name == 'add_item_to_order':
        params['required'].remove('instructions')  # An omitted customization means none.
    if name == 'confirm_order':
        tool['function']['description'] = 'Express confirmation intent only. Python checks direct customer consent and the previously displayed review.'

def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key.')
        result[key] = value
    return result

def decode_json(text):
    if not isinstance(text, str) or len(text) > 2_000_000:
        raise ValueError('Invalid JSON text.')
    text = text.strip()
    if text.startswith('```'):
        match = re.fullmatch(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', text, re.I)
        if not match:
            raise ValueError('Malformed JSON fence.')
        text = match[1].strip()
    return json.loads(text, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON value.')))

def _call(value):
    if type(value) is not dict:
        raise ValueError('Expected a tool-call object.')
    if 'function' in value:
        if set(value) - {'function', 'id', 'type', 'index'}:
            raise ValueError('Unexpected tool metadata.')
        value = value['function']
    if type(value) is not dict or set(value) - {'name', 'arguments', 'index'} or 'name' not in value:
        raise ValueError('Expected a tool name and arguments.')
    arguments = value.get('arguments', {})
    if isinstance(arguments, str):
        arguments = decode_json(arguments)
    if type(arguments) is not dict:
        raise ValueError('Tool arguments must be an object.')
    return {'name': value['name'], 'arguments': arguments}

def parse_response(message):
    """Return (unbound intents, final reply). Reject mixed/ambiguous plans."""
    if type(message) is not dict or not isinstance(message.get('content', ''), str):
        raise ValueError('Invalid assistant message.')
    native = message.get('tool_calls')
    if native is not None and type(native) is not list:
        raise ValueError('Invalid native tool-call list.')
    if native:
        return [_call(call) for call in native], None
    value = decode_json(message.get('content', ''))
    if type(value) is list:
        return [_call(call) for call in value], None
    if type(value) is not dict:
        raise ValueError('Expected a JSON object or tool-call list.')
    if 'calls' in value or 'tool_calls' in value:
        if set(value) not in ({'calls'}, {'tool_calls'}):
            raise ValueError('Unexpected fields in an action plan.')
        calls = value.get('calls', value.get('tool_calls'))
        if type(calls) is not list or not calls:
            raise ValueError('Expected nonempty tool calls.')
        return [_call(call) for call in calls], None
    if 'name' in value or 'function' in value:
        return [_call(value)], None
    return [], value

def _integer(value):
    # JSON integer strings are equivalent; floats and booleans remain invalid.
    if isinstance(value, str) and re.fullmatch(r'-?(?:0|[1-9][0-9]*)', value) and len(value) <= 12:
        return int(value)
    return value

def bind_intent(call, *, agent, menu, review_token, table_no=None):
    """Resolve names/legacy visible references, never let a model select a session."""
    name, args = call['name'], dict(call['arguments'])
    if not isinstance(name, str):
        raise ValueError('Invalid tool name.')
    # Legacy model encodings can contain IDs; discard session/token values, not
    # the isolation check in the trusted dispatcher. They can never select state.
    for key in ('session_id', 'review_token'):
        if key in args:
            allowed_legacy = ('establish_session', 'add_item_to_order', 'view_current_order') if key == 'session_id' else ('confirm_order',)
            if name not in allowed_legacy:
                raise ValueError('Unexpected protected argument for this tool.')
            if not isinstance(args[key], str) or len(args[key]) > 500:
                raise ValueError('Invalid legacy identifier.')
            del args[key]
    for key in ('qty', 'item_id', 'line_id', 'table_no'):
        if key in args:
            args[key] = _integer(args[key])
    if name == 'establish_session':
        current = agent.context().get('table_no') if agent.session_id else table_no
        allowed = table_no if table_no is not None else current
        if type(args.get('table_no')) is not int or allowed is None or args['table_no'] != allowed:
            raise ValueError('Python requires the customer\'s explicit table number; the model cannot choose a table.')
    if name in ('add_item_to_order', 'view_current_order'):
        args['session_id'] = agent.session_id or ''
    if name == 'add_item_to_order':
        args.setdefault('instructions', '')
    if name == 'confirm_order':
        args['review_token'] = review_token or ''
    if name in ('check_stock', 'check_item_allergen'):
        if 'item_name' in args:
            matches = [item for item in menu if isinstance(args['item_name'], str) and item['name'].casefold() == args['item_name'].casefold()]
            if len(matches) != 1 or 'item_id' in args:
                raise ValueError('Use one exact menu item name.')
            args.pop('item_name')
            args['item_id'] = matches[0]['id']
        elif type(args.get('item_id')) is not int or not any(item['id'] == args['item_id'] for item in menu):
            raise ValueError('Unknown menu item reference.')
    if name in ('update_quantity', 'remove_item', 'update_instructions'):
        cart = agent.context().get('order', {}).get('items', [])
        if 'item_name' in args:
            item_name = args.pop('item_name')
            supplied_qualifier = 'current_instructions' in args
            qualifier = args.pop('current_instructions', None)
            if supplied_qualifier and (not isinstance(qualifier, str) or len(qualifier) > 500):
                raise ValueError('Current instructions must be text of at most 500 characters.')
            matches = [line for line in cart if isinstance(item_name, str) and line['name'].casefold() == item_name.casefold()
                       and (qualifier is None or line['special_instructions'] == qualifier)]
            if len(matches) != 1 or 'line_id' in args:
                raise ValueError('Which exact cart portion would you like changed? Use its item name and current instructions.')
            args['line_id'] = matches[0]['line_id']
        elif type(args.get('line_id')) is not int or not any(line['line_id'] == args['line_id'] for line in cart):
            raise ValueError('Unknown cart portion reference.')
    bound = {'name': name, 'arguments': args}
    validate_call(bound)  # Keep the strict existing allowlist/type validator.
    return bound

def model_call(bound, *, menu, cart):
    """Serialize validated intent history without passing database identities back."""
    args = dict(bound['arguments'])
    args.pop('session_id', None)
    args.pop('review_token', None)
    if 'item_id' in args:
        item_id = args.pop('item_id')
        item = next((item for item in menu if item['id'] == item_id), None)
        if item is None:
            raise ValueError('The menu changed. Check it again before continuing.')
        args['item_name'] = item['name']
    if 'line_id' in args:
        line_id = args.pop('line_id')
        line = next((line for line in cart if line['line_id'] == line_id), None)
        if line is None:
            raise ValueError('The cart changed. Review the current portions before continuing.')
        args['item_name'] = line['name']
        args['current_instructions'] = line['special_instructions']
    return {'name': bound['name'], 'arguments': args}

_NUMBERS = dict(zip(('one','two','three','four','five','six','seven','eight','nine','ten',
                    'isa','dalawa','tatlo','apat','lima','anim','pito','walo','siyam','sampu'),
                   (*range(1,11), *range(1,11))))
_TABLE = re.compile(r'\b(?:table(?:\s+(?:number|no\.?))?|mesa)\s*(?:is\s+|ko\s+|namin\s+)?[#:]?\s*(\d+|' + '|'.join(_NUMBERS) + r')\b', re.I)

def customer_table(text, *, awaiting=False):
    if re.search(r"\b(?:not|hindi|don't use|do not use|never use|huwag(?: sa)?)\s+(?:at\s+)?(?:table|mesa)\b", text, re.I):
        raise ValueError('Please state your table number directly; I will not infer it from a negated table reference.')
    values = [_NUMBERS.get(match.lower(), int(match) if match.isdigit() else None) for match in _TABLE.findall(text)]
    if awaiting and re.fullmatch(r'\s*\d+\s*[.!]?\s*', text):
        values.append(int(text.strip().rstrip('.!')))
    if not values:
        return None
    if len(set(values)) != 1 or not 1 <= values[0] <= 2147483647:
        raise ValueError('Please provide one integer table number between 1 and 2147483647.')
    return values[0]
