"""Conservative request completion tracking; never executes or authorizes tools.

Only a full, unconditional quantity + exact menu-name clause is recognized as
an addition. Other language still uses the model and existing FSM validation.
Retained ambiguous language must be restated before it can mutate an order.
"""
import re

MUTATIONS = frozenset(('add_item_to_order', 'update_quantity', 'remove_item',
                       'update_instructions', 'confirm_order', 'cancel_order'))
ROUTING_INTENTS = frozenset(('view_current_order', 'request_review', 'browse_menu',
    'get_menu_by_category', 'check_stock', 'check_item_allergen', 'add_item_to_order',
    'confirm_order', 'cancel_order', 'update_quantity', 'remove_item',
    'update_instructions', 'general', 'clarify'))
_QUANTITIES = dict(zip(('one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
                        'isa', 'isang', 'dalawa', 'dalawang', 'tatlo', 'tatlong'),
                       (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 1, 1, 2, 2, 3, 3)))


def table_answer_only(text):
    """A table clarification cannot include changed, negated or additional intent."""
    return bool(re.fullmatch(r'\s*(?:(?:table(?:\s+(?:number|no\.?))?|mesa)\s*(?:is\s+|ko\s+|namin\s+)?[#:]?\s*)?'
                             r'(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|isa|dalawa|tatlo|apat|lima|anim|pito|walo|siyam|sampu)'
                             r'(?:\s+po)?[.! ]*', text, re.I))


def request_goals(text, menu):
    """Return a fully recognized plan, or None. No substring-based authorization."""
    goals = []
    for clause in re.split(r'\s+(?:and|then|at)\s+', text.strip(), flags=re.I):
        clause = clause.strip(' .!')
        read = re.fullmatch(r'(?:please\s+)?(?:show|browse|view)(?:\s+me)?\s+(?:the\s+)?menu(?:\s+please)?', clause, re.I)
        if read:
            goals.append({'name': 'browse_menu', 'arguments': {}})
            continue
        matched = None
        for item in menu:
            match = re.fullmatch(r"(?:please\s+)?(?:(?:add|order|give me|I(?:'d| would) like|I want|I'd like to order)\s+)?"
                                 r'(\d+|' + '|'.join(_QUANTITIES) + r')\s+(?:portions?\s+(?:of\s+)?)?'
                                 + re.escape(item['name']) + r'(?:\s+(?:po|please))?', clause, re.I)
            if match:
                qty = _QUANTITIES.get(match[1].lower(), int(match[1]) if match[1].isdigit() else None)
                if qty is not None and 1 <= qty <= 2147483647:
                    matched = {'name': 'add_item_to_order', 'arguments':
                               {'item_name': item['name'], 'qty': qty, 'instructions': ''}}
                break
        if matched is None:
            return None
        goals.append(matched)
    return goals or None


class RequestProgress:
    def __init__(self, goals):
        self.remaining = list(goals or [])
        self.recognized = goals is not None

    @classmethod
    def from_text(cls, text, menu):
        progress = cls(request_goals(text, menu))
        if not progress.recognized:
            # This is only a completion requirement, never authority to execute
            # a mutation. Complex/current-turn intent still goes through Qwen,
            # strict validation and the FSM; a read alone cannot satisfy it.
            addition_prefix = re.match(r"\s*(?:please\s+)?(?:add|order|give me)\b", text, re.I)
            quantity_prefix = any(re.match(r'\s*(?:\d+|' + '|'.join(_QUANTITIES) + r')\s+' + re.escape(i['name']) + r'\b', text, re.I) for i in menu)
            if addition_prefix or quantity_prefix:
                progress.remaining = [{'name': 'add_item_to_order', 'arguments': {}}]
        return progress

    @classmethod
    def from_routing(cls, intent, fallback):
        """Validate semantic classification; it supplies no executable arguments."""
        if not isinstance(intent, str) or intent not in ROUTING_INTENTS:
            raise ValueError('Invalid request classification; no actions executed.')
        if intent == 'general':
            return fallback  # Complex/multiple operations still need tool interpretation.
        if intent == 'clarify':
            return cls([{'name': 'clarify', 'arguments': {}}])
        return cls([{'name': intent, 'arguments': {}}])

    @staticmethod
    def matches(goal, call):
        return goal['name'] == call['name'] and all(
            call['arguments'].get(key) == value for key, value in goal['arguments'].items())

    def validate(self, calls):
        # A model may inspect stock/cart/allergens, but may not substitute a
        # different mutation for a precisely recognized customer request.
        if self.recognized:
            remaining = list(self.remaining)
            for call in calls:
                if call['name'] in MUTATIONS:
                    index = next((i for i, goal in enumerate(remaining) if self.matches(goal, call)), None)
                    if index is None:
                        raise ValueError('That mutation does not match the outstanding customer request. Ask for clarification instead.')
                    remaining.pop(index)

    def completed(self, call, result):
        if result['ok']:
            index = next((i for i, goal in enumerate(self.remaining) if self.matches(goal, call)), None)
            if index is not None:
                self.remaining.pop(index)

    def incomplete_feedback(self):
        additions = [goal for goal in self.remaining if goal['name'] == 'add_item_to_order' and 'qty' in goal['arguments']]
        if additions:
            descriptions = [f"{g['arguments']['qty']} × {g['arguments']['item_name']}" for g in additions]
            return 'Order request incomplete: ' + ', '.join(descriptions) + ' was NOT added. A cart view does not add food. Please clarify or restate only the unfinished request.'
        if any(goal['name'] == 'add_item_to_order' for goal in self.remaining):
            return 'Order request incomplete: the requested food addition was NOT executed. A cart view does not add food. Please clarify or restate only the unfinished request.'
        return 'Request incomplete: outstanding requested operations were not completed. Please restate only the unfinished part.'
