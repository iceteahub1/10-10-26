"""Bounded inference probe. Never dispatches tools or opens a database."""
import json
from bistro.ollama_client import OllamaClient, ToolsUnsupported, OllamaError
from bistro.tool_schemas import TOOLS, validate_call

def check_model(url, model, client=None):
    client = client or OllamaClient(url, model, timeout=120)
    structured = False
    # Argument types and grounded final output matter as well as empty-argument routing.
    cases = [('Show the menu.', {'name': 'browse_menu', 'arguments': {}}),
             ('Show only the Mains menu category.', {'name': 'get_menu_by_category', 'arguments': {'category': 'Mains'}}),
             ('Check the stock of item ID 1.', {'name': 'check_stock', 'arguments': {'item_id': 1}})]
    try:
        for prompt, expected in cases:
            print(f'Testing {model}: {expected["name"]} (read-only; model loading may take up to 120 seconds per request)...')
            messages = [{'role': 'system', 'content': 'Compatibility test only. Return exactly one requested read-only tool call. Do not establish a session or change any data.'},
                        {'role': 'user', 'content': prompt}]
            def request():
                context = messages
                if structured:
                    context = messages + [{'role': 'system', 'content': 'Return JSON {"calls":[{"name":"tool", "arguments":{}}]}. Schemas: ' + json.dumps(TOOLS)}]
                return client.chat(context, None if structured else TOOLS, structured=structured)
            try:
                response = request()
            except ToolsUnsupported:
                structured = True
                response = request()
            if response.get('tool_calls'):
                calls = [{'name': c['function']['name'], 'arguments': c['function']['arguments']} for c in response['tool_calls']]
            else:
                value = json.loads(response['content'])
                if type(value) is not dict or set(value) != {'calls'}:
                    raise ValueError('Expected a tool-call object.')
                calls = value['calls']
            if type(calls) is not list or len(calls) != 1:
                raise ValueError('Expected exactly one read-only call.')
            validate_call(calls[0])
            if calls[0] != expected:
                raise ValueError('Incorrect tool or arguments: ' + json.dumps(calls[0]))
        evidence = 'Test menu is available.'
        print(f'Testing {model}: grounded final reply...')
        final = client.chat([{'role': 'system', 'content': 'Return JSON with reply and facts only. reply must be Thank you! and facts must contain only this exact evidence: ' + evidence},
                             {'role': 'user', 'content': 'Acknowledge the test menu.'}], structured=True)
        value = json.loads(final['content'])
        if value != {'reply': 'Thank you!', 'facts': [evidence]} or final.get('tool_calls'):
            raise ValueError('Invalid grounded final reply.')
        return True, 'structured' if structured else 'native', 'Read-only tool routing and grounded reply passed. This is a smoke check, not a guarantee of ordering quality.'
    except (OllamaError, ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        return False, 'native', 'Compatibility check failed: ' + str(exc)
