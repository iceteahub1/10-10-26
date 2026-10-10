"""Explicit allowlist. Session identity is supplied by Python, never by Qwen."""
import json
from bistro import order_service as orders

# name: (FSM action, typed required parameters, optional parameters)
REGISTRY = {
    'establish_session': ('establish_session', {'table_no': 'integer'}, {}),
    'get_menu_by_category': ('browse_menu', {'category': 'string'}, {}),
    'check_item_allergen': ('check_allergens', {'item_id': 'integer', 'allergen': 'string'}, {}),
    'add_item_to_order': ('add_item', {'session_id': 'string', 'item_name': 'string', 'qty': 'integer', 'instructions': 'string'}, {}),
    'view_current_order': ('view_order', {'session_id': 'string'}, {}),
    'browse_menu': ('browse_menu', {}, {}),
    'check_stock': ('check_availability', {'item_id': 'integer'}, {}),
    'update_quantity': ('update_quantity', {'line_id': 'integer', 'qty': 'integer'}, {}),
    'remove_item': ('remove_item', {'line_id': 'integer'}, {}),
    'update_instructions': ('update_instructions', {'line_id': 'integer', 'instructions': 'string'}, {}),
    'request_review': ('request_review', {}, {}),
    'confirm_order': ('confirm_order', {'confirmed': 'boolean', 'review_token': 'string'}, {}),
    'cancel_order': ('cancel_order', {}, {}),
}
DESCRIPTIONS = {
    'establish_session': 'Capture the customer table number. Cannot select or switch session IDs.',
    'request_review': 'Present the exact current cart for review before confirmation.',
    'confirm_order': 'Only explicit customer confirmation of the previously displayed review; copy its review_token.',
    'update_instructions': 'Update a whole cart line. For one of multiple portions, first reduce quantity and add a separate customized portion.',
    'check_item_allergen': 'Check declared allergens only; never guarantee absence of cross-contact.',
}
TOOLS = [{'type': 'function', 'function': {'name': name,
          'description': DESCRIPTIONS.get(name, name.replace('_', ' ')),
          'parameters': {'type': 'object', 'properties': {k: {'type': v} for k, v in {**req, **opt}.items()},
                         'required': list(req), 'additionalProperties': False}}}
         for name, (_, req, opt) in REGISTRY.items()]


def validate_call(call):
    if type(call) is not dict or set(call) != {'name', 'arguments'}:
        raise ValueError('Expected a registered tool name and arguments object.')
    name, args = call['name'], call['arguments']
    if not isinstance(name, str) or name not in REGISTRY or type(args) is not dict:
        raise ValueError('Unknown tool or invalid arguments.')
    _, req, opt = REGISTRY[name]
    if not set(req) <= set(args) or set(args) - set(req) - set(opt):
        raise ValueError('Missing or unexpected tool arguments.')
    types = {'string': str, 'integer': int, 'boolean': bool}
    for key, value in args.items():
        if type(value) is not types[{**req, **opt}[key]]:
            raise ValueError('Invalid type for ' + key)
        if isinstance(value, str) and len(value) > 500:
            raise ValueError('Tool argument is too long.')
    json.dumps(args, allow_nan=False)
    return name, dict(args)


def dispatch(agent, call, *, confirmation_allowed=False, review_token=None):
    try:
        name, args = validate_call(call)
        # Accept only the application's binding. A mismatched model ID is rejected.
        if 'session_id' in args and args.pop('session_id') != agent.session_id:
            raise ValueError('Tool session ID does not match this customer.')
        if name == 'confirm_order':
            token = args.pop('review_token')
            if not confirmation_allowed or not review_token or token != review_token:
                raise ValueError('Explicit confirmation of the displayed review is required.')
        if name == 'add_item_to_order':
            # AI must use exact SQLite names; substring substitutions require clarification.
            menu = orders.browse_menu(db_path=agent.db_path)
            if args['item_name'].casefold() not in {m['name'].casefold() for m in menu}:
                raise ValueError('Unknown or incomplete menu name. Ask which exact menu item the customer wants.')
        return agent.execute({'action': REGISTRY[name][0], 'arguments': args})
    except (ValueError, TypeError) as exc:
        return {'ok': False, 'message': str(exc), 'error': type(exc).__name__, 'data': None}
