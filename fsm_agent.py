"""Deterministic laboratory FSM. Structured actions only; no natural-language AI."""
import logging
import sqlite3
from uuid import uuid4
import db_tools
from bistro import order_service as orders
from bistro.database import connection
from bistro.session_store import create_session, get_session, validate_session_id
from bistro import conversation_store as memory

STATE_GREETING = 'STATE_GREETING'
STATE_TAKING_ORDER = 'STATE_TAKING_ORDER'
STATE_REVIEW = 'STATE_REVIEW'
STATE_CONFIRMED = 'STATE_CONFIRMED'
STATE_CANCELLED = 'STATE_CANCELLED'
TRANSITIONS = {
    STATE_GREETING: {STATE_TAKING_ORDER, STATE_CONFIRMED},  # resuming a Phase 2 receipt
    STATE_TAKING_ORDER: {STATE_REVIEW, STATE_CANCELLED, STATE_CONFIRMED},
    STATE_REVIEW: {STATE_TAKING_ORDER, STATE_CONFIRMED, STATE_CANCELLED},
    STATE_CONFIRMED: set(), STATE_CANCELLED: set(),
}
# Required / optional argument names form the dispatcher contract. Unknown keys fail.
ACTION_ARGUMENTS = {
    'establish_session': ({'table_no'}, {'session_id'}),
    'new_session': ({'table_no'}, set()),
    'browse_menu': (set(), {'category'}),
    'check_availability': ({'item_id'}, set()),
    'check_allergens': ({'item_id', 'allergen'}, set()),
    'add_item': ({'item_name', 'qty'}, {'instructions'}),
    'update_quantity': ({'line_id', 'qty'}, set()),
    'remove_item': ({'line_id'}, set()),
    'update_instructions': ({'line_id', 'instructions'}, set()),
    'view_order': (set(), set()), 'request_review': (set(), set()),
    'confirm_order': ({'confirmed'}, set()), 'cancel_order': (set(), set()),
    'return_to_order': (set(), set()),
}
EDIT_ACTIONS = {'add_item', 'update_quantity', 'remove_item', 'update_instructions'}
LOG = logging.getLogger(__name__)


def validate_action(action):
    if type(action) is not dict or set(action) != {'action', 'arguments'}:
        raise ValueError('Use an action and an arguments object; see help.')
    name, args = action['action'], action['arguments']
    if not isinstance(name, str) or name not in ACTION_ARGUMENTS or type(args) is not dict:
        raise ValueError('Unknown action or malformed arguments; see help.')
    required, optional = ACTION_ARGUMENTS[name]
    if not required <= set(args) or set(args) - required - optional:
        raise ValueError('Missing or unexpected action arguments; see help.')
    memory.encode(action)  # Reject non-JSON arguments/NaN before any effects.
    return name, args


class ConversationAgent:
    """One local customer binding. Every action reloads SQLite state under a lock.

    Session IDs isolate context, but are not authentication credentials. Future
    public web adapters must authenticate table/session ownership separately.
    """
    def __init__(self, *, db_path=None, tax_rate=0, service_rate=0):
        self.db_path = db_path
        self.tax_rate = orders._rate(tax_rate)
        self.service_rate = orders._rate(service_rate)
        self.session_id = None

    @property
    def state(self):
        if self.session_id is None:
            return STATE_GREETING
        with connection(self.db_path) as con:
            return memory.load(con, self.session_id)['state']

    def context(self):
        if self.session_id is None:
            return {'state': STATE_GREETING, 'messages': [], 'context': {}}
        result = memory.get_context(self.session_id, db_path=self.db_path)
        result['order'] = orders.view_cart(self.session_id, db_path=self.db_path,
                                          tax_rate=self.tax_rate, service_rate=self.service_rate)
        return result

    def _transition(self, con, sid, target):
        current = memory.load(con, sid)['state']
        if current != target and target not in TRANSITIONS[current]:
            raise ValueError('That state transition is not allowed.')
        con.execute("UPDATE conversation_sessions SET state=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE session_id=?",
                    (target, sid))

    def _establish(self, con, name, args):
        table = args['table_no']
        if type(table) is not int or not 1 <= table <= 2147483647:
            raise ValueError('Please use an integer table number between 1 and 2147483647.')
        if self.session_id is not None:
            current = memory.load(con, self.session_id)['state']
            if current not in (STATE_CONFIRMED, STATE_CANCELLED) and not (
                    name == 'establish_session' and table == get_session(self.session_id, db_path=self.db_path)['table_no']
                    and args.get('session_id', self.session_id) == self.session_id):
                raise ValueError('Resume this customer or cancel their order before starting another session.')
        if name == 'new_session':
            sid = uuid4().hex
        elif 'session_id' in args:
            sid = validate_session_id(args['session_id'])
            session = get_session(sid, db_path=self.db_path)
            if session['table_no'] != table:
                raise ValueError('That session belongs to another table.')
        else:
            rows = con.execute('''SELECT t.session_id FROM table_sessions t
                LEFT JOIN conversation_sessions c ON c.session_id=t.session_id
                WHERE t.table_no=? AND t.status='open'
                  AND (c.state IS NULL OR c.state NOT IN ('STATE_CANCELLED','STATE_CONFIRMED'))''', (table,)).fetchall()
            if len(rows) > 1:
                raise ValueError('Several active sessions exist; supply the intended session_id.')
            sid = rows[0][0] if rows else uuid4().hex
        session = create_session(table, sid, db_path=self.db_path)
        exists = con.execute('SELECT 1 FROM conversation_sessions WHERE session_id=?', (sid,)).fetchone()
        if not exists:
            con.execute('INSERT INTO conversation_sessions(session_id,state) VALUES(?,?)', (sid, STATE_GREETING))
            self._transition(con, sid, STATE_CONFIRMED if session['status'] == 'checked_out' else STATE_TAKING_ORDER)
        return sid, {'session_id': sid, 'table_no': table}, 'Welcome to Perpetual Bistro. Your table session is ready.'

    def _cart(self, sid):
        return db_tools.view_current_order(sid, db_path=self.db_path) if not (self.tax_rate or self.service_rate) else orders.view_cart(
            sid, db_path=self.db_path, tax_rate=self.tax_rate, service_rate=self.service_rate)

    def _run(self, con, sid, name, args):
        stored = memory.load(con, sid)
        state = stored['state']
        # Recover orders finalized through the existing Phase 2 service.
        if get_session(sid, db_path=self.db_path)['status'] == 'checked_out' and state != STATE_CONFIRMED:
            self._transition(con, sid, STATE_CONFIRMED)
            state = STATE_CONFIRMED
        if name == 'view_order':
            return self._cart(sid), ('Here is your persisted receipt.' if state == STATE_CONFIRMED else
                                     'This cancelled order is retained for history; it cannot be confirmed.' if state == STATE_CANCELLED else
                                     'Here is your current order.')
        if state == STATE_CANCELLED:
            raise ValueError('This order was cancelled. Start a new session for a new customer.')
        if state == STATE_CONFIRMED:
            if name == 'confirm_order' and args['confirmed'] is True:
                return orders.get_receipt(sid, db_path=self.db_path), 'This receipt is already finalized; stock was not deducted again.'
            raise ValueError('This order is finalized. Start a new session for a new customer.')
        if name in EDIT_ACTIONS:
            if state not in (STATE_TAKING_ORDER, STATE_REVIEW):
                raise ValueError('Establish a table session before editing an order.')
            functions = {'add_item': db_tools.add_item_to_order,
                         'update_quantity': orders.change_quantity,
                         'remove_item': orders.remove_item,
                         'update_instructions': orders.customize_item}
            call_args = dict(args)
            if name == 'add_item':
                call_args.setdefault('instructions', '')
            functions[name](sid, **call_args, db_path=self.db_path)
            self._transition(con, sid, STATE_TAKING_ORDER)
            con.execute('UPDATE conversation_sessions SET review_json=NULL, context_json=? WHERE session_id=?',
                        (memory.encode({**{k: v for k, v in stored['context'].items() if k not in ('pending_question', 'unresolved_action')},
                                        'last_modification': {'action': name, 'arguments': args}}), sid))
            return self._cart(sid), 'Order updated. Please review it again before confirming.'
        if name == 'browse_menu':
            data = db_tools.get_menu_by_category(args['category'], db_path=self.db_path) if args.get('category') is not None else orders.browse_menu(db_path=self.db_path)
        elif name == 'check_availability':
            data = orders.check_stock(**args, db_path=self.db_path)
        elif name == 'check_allergens':
            data = db_tools.check_item_allergen(**args, db_path=self.db_path)
        elif name == 'cancel_order':
            self._transition(con, sid, STATE_CANCELLED)
            con.execute('UPDATE conversation_sessions SET review_json=NULL,context_json=? WHERE session_id=?',
                        (memory.encode({'cancelled': True}), sid))
            return self._cart(sid), 'Order cancelled. No stock was deducted. Start a new session when ready.'
        elif name == 'return_to_order':
            if state != STATE_REVIEW:
                raise ValueError('Return to ordering is available only during review.')
            self._transition(con, sid, STATE_TAKING_ORDER)
            con.execute('UPDATE conversation_sessions SET review_json=NULL WHERE session_id=?', (sid,))
            return self._cart(sid), 'You can change the order. A fresh review will be required.'
        elif name == 'request_review':
            if state not in (STATE_TAKING_ORDER, STATE_REVIEW):
                raise ValueError('This order cannot be reviewed now.')
            data = self._cart(sid)
            if not data['items']:
                raise ValueError('Your cart is empty. Add an item before requesting review.')
            self._transition(con, sid, STATE_REVIEW)
            # Store the authoritative cart before the state transition; cart status is Phase 2 status.
            con.execute('UPDATE conversation_sessions SET review_json=?,context_json=? WHERE session_id=?',
                        (memory.encode(data), memory.encode({'pending_question': 'Confirm this exact order or return to ordering.'}), sid))
            return data, 'Please verify the items and total. Use confirm_order with confirmed=true to confirm.'
        elif name == 'confirm_order':
            if args['confirmed'] is not True:
                raise ValueError('Explicit confirmation requires the boolean confirmed=true.')
            if state != STATE_REVIEW or not stored['review']:
                raise ValueError('Please request and read an order review before confirming.')
            data = self._cart(sid)
            if not data['items'] or data != stored['review']:
                self._transition(con, sid, STATE_TAKING_ORDER)
                con.execute('UPDATE conversation_sessions SET review_json=NULL WHERE session_id=?', (sid,))
                return {'review_invalidated': True, 'order': data}, 'The order or menu changed. Review the current order again.'
            receipt = orders.checkout(sid, db_path=self.db_path,
                                      tax_rate=self.tax_rate, service_rate=self.service_rate)
            self._transition(con, sid, STATE_CONFIRMED)
            con.execute('UPDATE conversation_sessions SET review_json=NULL,context_json=? WHERE session_id=?',
                        (memory.encode({'receipt_id': receipt['receipt_id']}), sid))
            return receipt, 'Order confirmed. Here is your saved receipt.'
        else:
            raise ValueError('That action is not available in this state.')
        context = stored['context']
        context['last_menu_question'] = {'action': name, 'arguments': args, 'result': data}
        context.pop('pending_question', None)
        con.execute('UPDATE conversation_sessions SET context_json=? WHERE session_id=?', (memory.encode(context), sid))
        return data, 'Here is the information from the restaurant database.'

    def execute(self, action, *, message=None):
        """Return a JSON-safe result; failures never claim a successful write.

        Atomic service effects + state + transcript, with an independent error
        transcript after rollback. Database diagnostics go to Python logging.
        """
        sid = self.session_id
        try:
            name, args = validate_action(action)
            if message is not None and (not isinstance(message, str) or len(message) > 10000):
                raise ValueError('Customer message must be text of at most 10000 characters.')
            with connection(self.db_path, write=True) as con:
                if name in ('establish_session', 'new_session'):
                    sid, data, text = self._establish(con, name, args)
                else:
                    if sid is None:
                        raise ValueError('Welcome! Establish a session with your table number first.')
                    data, text = self._run(con, sid, name, args)
                result = {'ok': True, 'session_id': sid, 'state': memory.load(con, sid)['state'],
                          'message': text, 'data': data}
                memory.record(con, sid, 'customer', {'action': action, 'text': message})
                memory.record(con, sid, 'assistant', result)
            self.session_id = sid
            return result
        except (ValueError, TypeError, OverflowError, sqlite3.Error, OSError, RuntimeError) as exc:
            LOG.debug('Conversation action failed', exc_info=True)
            friendly = str(exc) if isinstance(exc, ValueError) else 'The database operation failed. No action changes were saved; please retry or check the logs.'
            result = {'ok': False, 'session_id': self.session_id, 'state': STATE_GREETING,
                      'message': friendly, 'error': type(exc).__name__, 'data': None}
            if self.session_id is not None:
                try:
                    with connection(self.db_path, write=True) as con:
                        stored = memory.load(con, self.session_id)
                        result['state'] = stored['state']
                        # Keep a recoverable clarification without guessing a menu item.
                        if isinstance(exc, orders.OrderError) and 'Ambiguous' in str(exc):
                            context = stored['context']
                            context['pending_question'] = 'Which exact menu item did you mean? Browse the menu and use its full name.'
                            context['unresolved_action'] = action
                            con.execute('UPDATE conversation_sessions SET context_json=? WHERE session_id=?', (memory.encode(context), self.session_id))
                            result['message'] = context['pending_question']
                        try:
                            memory.record(con, self.session_id, 'customer', {'action': action, 'text': message})
                        except (ValueError, TypeError):
                            memory.record(con, self.session_id, 'customer', {'invalid_action': True})
                        memory.record(con, self.session_id, 'assistant', result)
                except (sqlite3.Error, OSError, RuntimeError, ValueError):
                    LOG.debug('Could not persist error history', exc_info=True)
            return result
