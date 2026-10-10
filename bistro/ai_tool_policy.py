"""Model-facing affordances, derived from Python state rather than model size.

This is an additional boundary, not a replacement for strict tool validation,
session isolation, or the transactional FSM. It never interprets or executes an
order: Qwen still supplies the intent and arguments.
"""
from bistro.ai_protocol import INTENT_TOOLS
from fsm_agent import STATE_TAKING_ORDER, STATE_REVIEW

READS = frozenset(('browse_menu', 'get_menu_by_category', 'check_stock',
                   'check_item_allergen', 'view_current_order'))
LINE_EDITS = frozenset(('update_quantity', 'remove_item', 'update_instructions'))


class ToolUnavailable(ValueError):
    pass


def available_tools(context, progress, *, confirmation_allowed=False, review_token=None):
    state = context['state']
    cart = context.get('order', {}).get('items', [])
    # Closed orders permit receipt/history viewing, as in the existing FSM.
    names = {'view_current_order'}
    if state in (STATE_TAKING_ORDER, STATE_REVIEW):
        names |= READS | {'add_item_to_order', 'cancel_order'}
        if cart:
            names |= LINE_EDITS | {'request_review'}
        if cart and state == STATE_REVIEW and context.get('review') and confirmation_allowed and review_token:
            names.add('confirm_order')
    additions = any(goal['name'] == 'add_item_to_order' for goal in progress.remaining)
    if additions and progress.recognized:
        # Read-only prerequisites remain available, especially allergens/stock.
        # Existing-line edits are not equivalent to adding another portion.
        names &= READS | {'add_item_to_order'}
    elif progress.recognized and progress.remaining:
        names &= READS | {goal['name'] for goal in progress.remaining}
    elif confirmation_allowed:
        # Direct consent for a displayed review is not consent to edit/cancel
        # that cart or replace its review before attempting checkout.
        names &= READS | {'confirm_order'}
    tools = [tool for tool in INTENT_TOOLS if tool['function']['name'] in names]
    # Put the requested affordance first in both protocols; never choose its args.
    if additions or not cart:
        tools.sort(key=lambda tool: tool['function']['name'] != 'add_item_to_order')
    return tools


def validate_available(intents, tools):
    names = {tool['function']['name'] for tool in tools}
    for intent in intents:
        name = intent['name']
        if not isinstance(name, str) or name not in names:
            raise ToolUnavailable(f'Tool {name!r} is unavailable for the current state, cart or request. '
                                  'This operation is blocked. Use only: ' + ', '.join(sorted(names)))
