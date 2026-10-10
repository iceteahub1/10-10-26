"""Browser authentication and transport; shared FSM owns every order operation."""
import hashlib
import json
import logging
import os
import secrets
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler
from fastapi import Request
from fastapi.responses import JSONResponse
from bistro import conversation_store as memory, order_service as orders
from bistro.database import connection, initialize_database
from bistro.paths import DATABASE
from bistro.event_log import AcceptanceLog
from bistro.ai_engine import AIWaiter
from bistro.ollama_client import OllamaClient
from bistro.tool_schemas import dispatch
from fsm_agent import ConversationAgent, STATE_CONFIRMED, STATE_CANCELLED, EDIT_ACTIONS, validate_action

COOKIE = 'bistro_browser'
LOG = logging.getLogger(__name__)


class APIError(ValueError):
    def __init__(self, code, message, status=400):
        self.code, self.status = code, status
        super().__init__(message)


def public(value):
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if k != 'session_id'}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


def error_code(text):
    text = text.lower()
    if 'stock' in text or ('unavailable' in text and 'menu' in text):
        return 'out_of_stock'
    if 'review' in text or 'confirmation' in text:
        return 'stale_review'
    if 'database' in text or 'operation failed' in text:
        return 'application_failure'
    return 'invalid_input'


class BrowserAgent(ConversationAgent):
    def execute(self, action, **kwargs):
        if self.session_id and action.get('action') in ('establish_session', 'new_session'):
            return {'ok': False, 'error': 'BrowserBinding', 'message': 'Use the table session controls to start a new order.', 'data': None}
        return super().execute(action, **kwargs)


class BrowserAPI:
    def __init__(self, settings, db_path=None, client_factory=None, acceptance_log_path=None):
        self.settings, self.db_path, self.client_factory = settings, db_path or DATABASE, client_factory
        self.events = AcceptanceLog(acceptance_log_path)

    def initialize(self):
        initialize_database(self.db_path)
        with connection(self.db_path, write=True) as con:
            con.execute('''CREATE TABLE IF NOT EXISTS browser_bindings (
                credential_hash TEXT PRIMARY KEY, session_id TEXT REFERENCES table_sessions(session_id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            con.execute('''CREATE TABLE IF NOT EXISTS browser_requests (
                credential_hash TEXT NOT NULL REFERENCES browser_bindings(credential_hash),
                request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, result_json TEXT,
                PRIMARY KEY(credential_hash, request_id))''')

    def identity(self, request):
        credential = request.cookies.get(COOKIE, '')
        if len(credential) != 64:
            raise APIError('session_required', 'Start or refresh your table session first.', 401)
        digest = hashlib.sha256(credential.encode()).hexdigest()
        with connection(self.db_path) as con:
            row = con.execute('SELECT * FROM browser_bindings WHERE credential_hash=?', (digest,)).fetchone()
        if row is None:
            raise APIError('session_required', 'This browser session is unknown. Refresh to begin.', 401)
        return digest, row['session_id']

    def agent(self, sid):
        agent = BrowserAgent(db_path=self.db_path, tax_rate=self.settings.tax_rate, service_rate=self.settings.service_rate)
        agent.session_id = sid
        return agent

    def snapshot(self, sid):
        if not sid:
            return {'state': 'STATE_GREETING', 'order': None, 'review': None, 'review_token': None, 'messages': []}
        with connection(self.db_path) as con:
            context = memory.load(con, sid)
            order = self.agent(sid)._cart(sid)
            rows = con.execute('SELECT role,content_json FROM conversation_messages WHERE session_id=? ORDER BY id DESC LIMIT 100', (sid,)).fetchall()
        review = context.get('review')
        messages = []
        for entry in reversed(rows):
            content = json.loads(entry['content_json'])
            text = content.get('text') or content.get('response')
            if text and entry['role'] in ('customer', 'assistant'):
                messages.append({'role': entry['role'], 'text': text})
        return public({'state': context['state'], 'order': order, 'review': review,
            'review_token': hashlib.sha256(memory.encode(review).encode()).hexdigest() if review else None,
            'messages': messages[-100:]})

    def menu(self, category=None):
        try:
            items = orders.browse_menu(category, db_path=self.db_path)
        except Exception:
            self.events.record('menu_availability', available=False)
            raise
        self.events.record('menu_availability', available=True, item_count=len(items),
                           available_count=sum(bool(i['available'] and i['stock_qty'] > 0) for i in items))
        return public(items)

    def ai_status(self):
        status = self._ai_status()
        self.events.record('ai_availability', available=status['available'], code=status['code'])
        return status

    def _ai_status(self):
        base = {'available': False, 'model': self.settings.model, 'endpoint': self.settings.ollama_url}
        if not self.settings.ai_enabled:
            return {**base, 'code': 'ai_disabled', 'message': 'AI is disabled. Use configure_ai.bat to enable it.'}
        try:
            with build_opener(ProxyHandler({})).open(self.settings.ollama_url + '/api/tags', timeout=2) as response:
                raw = response.read(1000001)
            if len(raw) > 1000000:
                raise ValueError()
            tags = json.loads(raw)
            if self.settings.model not in {m.get('name') or m.get('model') for m in tags['models']}:
                return {**base, 'code': 'model_missing', 'message': 'Selected model is not installed. Use configure_ai.bat.'}
            return {**base, 'available': True, 'code': 'ready', 'message': 'Local AI is ready.'}
        except (OSError, URLError):
            return {**base, 'code': 'ollama_unavailable', 'message': 'Cannot reach Ollama. Install/start Ollama, then retry AI status.'}
        except (ValueError, KeyError, TypeError, AttributeError):
            return {**base, 'code': 'ai_unavailable', 'message': 'Ollama returned an invalid status. Check its logs.'}

    async def body(self, request):
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise APIError('invalid_input', 'Send a JSON request.', 415)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 16384:
                raise APIError('invalid_input', 'Request is too large.', 413)
        try:
            value = json.loads(data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError, RecursionError):
            raise APIError('invalid_input', 'Request contains malformed JSON.')
        if type(value) is not dict:
            raise APIError('invalid_input', 'Request must be an object.')
        return value

    @staticmethod
    def fields(body, required, optional=()):
        if not set(required) <= body.keys() or body.keys() - set(required) - set(optional):
            raise APIError('invalid_input', 'Missing or unexpected request fields.')

    def validate(self, path, body):
        if path == 'session':
            self.fields(body, ('table_no',))
            if type(body['table_no']) is not int or not 1 <= body['table_no'] <= 2147483647:
                raise APIError('invalid_input', 'Enter a positive integer table number.')
        elif path == 'chat':
            self.fields(body, ('message',))
            if type(body['message']) is not str or not body['message'].strip() or len(body['message']) > 10000:
                raise APIError('invalid_input', 'Enter a message of 1–10000 characters.')
        elif path == 'action':
            self.fields(body, ('action', 'arguments'))
            try:
                name, args = validate_action(body)
            except ValueError as exc:
                raise APIError('invalid_input', str(exc))
            if name not in EDIT_ACTIONS | {'request_review', 'return_to_order', 'cancel_order'}:
                raise APIError('invalid_input', 'That browser action is not allowed.')
            types = {'item_name': str, 'qty': int, 'instructions': str, 'line_id': int}
            if any(type(v) is not types.get(k) for k, v in args.items()):
                raise APIError('invalid_input', 'Action arguments have invalid types.')
        elif path == 'confirm':
            self.fields(body, ('confirmed', 'review_token'))
            if body['confirmed'] is not True or type(body['review_token']) is not str or len(body['review_token']) != 64:
                raise APIError('stale_review', 'Explicitly confirm the displayed review with its token.')
        else:
            raise APIError('invalid_input', 'Unknown endpoint.', 404)

    def perform(self, path, body, digest, sid):
        if path == 'session':
            if sid and self.agent(sid).state not in (STATE_CONFIRMED, STATE_CANCELLED):
                raise APIError('session_active', 'Finish or cancel the current order before starting another table session.', 409)
            agent = ConversationAgent(db_path=self.db_path, tax_rate=self.settings.tax_rate, service_rate=self.settings.service_rate)
            result = agent.execute({'action': 'new_session', 'arguments': body})
            if not result['ok']:
                return result, sid
            sid = agent.session_id
            with connection(self.db_path, write=True) as con:
                con.execute('UPDATE browser_bindings SET session_id=? WHERE credential_hash=?', (sid, digest))
            return result, sid
        if not sid:
            raise APIError('session_required', 'Enter your table number first.', 401)
        agent = self.agent(sid)
        if path == 'action':
            return agent.execute(body), sid
        if path == 'confirm':
            if agent.state == STATE_CONFIRMED:
                return agent.execute({'action': 'confirm_order', 'arguments': {'confirmed': True}}), sid
            token = self.snapshot(sid)['review_token']
            if not token or not secrets.compare_digest(token, body['review_token']):
                raise APIError('stale_review', 'This review is outdated. Review your order again.', 409)
            result = dispatch(agent, {'name': 'confirm_order', 'arguments': body}, confirmation_allowed=True, review_token=token)
            if (result.get('data') or {}).get('review_invalidated'):
                result = {**result, 'ok': False, 'error': 'StaleReview'}
            return result, sid
        if agent.state in (STATE_CONFIRMED, STATE_CANCELLED):
            return {'ok': False, 'response': 'This order is closed. Your saved order is shown; use New table session for another order.'}, sid
        status = self.ai_status()
        if not status['available']:
            raise APIError(status['code'], status['message'], 503)
        client = self.client_factory() if self.client_factory else OllamaClient(
            self.settings.ollama_url, self.settings.model, timeout=os.getenv('BISTRO_OLLAMA_TIMEOUT', '120'))
        waiter = AIWaiter(agent=agent, client=client)
        waiter.structured = self.settings.ai_transport == 'structured'
        return waiter.respond(body['message']), sid

    def execute(self, path, body, digest, sid, request_id):
        fingerprint = hashlib.sha256(memory.encode({'path': path, 'body': body}).encode()).hexdigest()
        with connection(self.db_path, write=True) as con:
            prior = con.execute('SELECT * FROM browser_requests WHERE credential_hash=? AND request_id=?', (digest, request_id)).fetchone()
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise APIError('duplicate_request', 'Request ID was already used for different input.', 409)
                if prior['result_json'] is None:
                    raise APIError('request_pending', 'This request is pending or interrupted. Refresh your cart; restart the server if interrupted.', 409)
                response = json.loads(prior['result_json'])
                self.record_outcome(path, body, response, {}, replayed=True)
                return response
            if con.execute('SELECT 1 FROM browser_requests WHERE credential_hash=? AND result_json IS NULL', (digest,)).fetchone():
                raise APIError('request_pending', 'Another request is still running. Wait and refresh your cart.', 409)
            sid = con.execute('SELECT session_id FROM browser_bindings WHERE credential_hash=?', (digest,)).fetchone()[0]
            con.execute('INSERT INTO browser_requests VALUES (?,?,?,NULL)', (digest, request_id, fingerprint))
        before = {}
        try:
            if path != 'chat':
                with connection(self.db_path, write=True) as con:
                    before = self.audit_snapshot(sid)
                    result, sid = self.perform(path, body, digest, sid)
                    response = self.response(result, sid)
                    con.execute('UPDATE browser_requests SET result_json=? WHERE credential_hash=? AND request_id=?', (memory.encode(response), digest, request_id))
                self.record_outcome(path, body, response, before, result)
                return response
            before = self.audit_snapshot(sid)
            result, sid = self.perform(path, body, digest, sid)
            response = self.response(result, sid)
        except APIError as exc:
            response = {'ok': False, 'code': exc.code, 'message': str(exc), 'http_status': exc.status, 'snapshot': self.snapshot(sid)}
        except Exception:
            LOG.exception('Browser request failed')
            response = {'ok': False, 'code': 'application_failure', 'message': 'The application could not complete this request. Refresh your cart to check saved changes.', 'http_status': 500}
        with connection(self.db_path, write=True) as con:
            con.execute('UPDATE browser_requests SET result_json=? WHERE credential_hash=? AND request_id=?', (memory.encode(response), digest, request_id))
        self.record_outcome(path, body, response, before, locals().get('result'))
        return response

    def audit_snapshot(self, sid):
        # Observability failures never alter ordering behavior.
        try:
            return self.snapshot(sid)
        except Exception:
            return {}

    def record_outcome(self, path, body, response, before, result=None, *, replayed=False):
        source = 'ai' if path == 'chat' else 'controls'
        after = response.get('snapshot') or {}
        ok = response.get('ok') is True
        if not ok:
            self.events.record('workflow_error', code=response.get('code'), stage=path)
        if path == 'action' and body.get('action') in EDIT_ACTIONS:
            self.events.record('cart_action', action=body['action'], ok=ok, source=source, replayed=replayed)
        traces = (result or {}).get('tools', []) if path == 'chat' else []
        for trace in traces:
            name = trace.get('call', {}).get('name')
            action = 'add_item' if name == 'add_item_to_order' else name
            if action in EDIT_ACTIONS:
                self.events.record('cart_action', action=action, ok=trace.get('result', {}).get('ok') is True,
                                   source=source, replayed=replayed)
        if not replayed and (before.get('state') != after.get('state') or body.get('action') == 'request_review'):
            if 'STATE_REVIEW' in (before.get('state'), after.get('state')):
                self.events.record('review_transition', from_state=before.get('state'), to_state=after.get('state'), ok=ok, source=source)
        receipt_saved = bool((after.get('order') or {}).get('receipt_id'))
        if path == 'confirm' or any(t.get('call', {}).get('name') == 'confirm_order' for t in traces):
            self.events.record('checkout_result', ok=ok, receipt_saved=receipt_saved, source=source, replayed=replayed)
        if not replayed and 'order' in before and receipt_saved and not (before.get('order') or {}).get('receipt_id'):
            self.events.record('receipt_created', source=source)

    def response(self, result, sid):
        text = result.get('response') or result.get('message') or 'Request failed.'
        code = None if result['ok'] else error_code(text)
        if not result['ok'] and result.get('source') == 'application_error':
            code = 'ai_unavailable' if 'ollama' in text.lower() or 'model output' in text.lower() else code
        return {'ok': result['ok'], 'message': text, 'code': code,
            'partial_success': result.get('partial_success', False), 'snapshot': self.snapshot(sid)}


def install_routes(app, api):
    @app.get('/api/session')
    def session(request: Request):
        try:
            _, sid = api.identity(request)
            return api.snapshot(sid)
        except APIError:
            credential = secrets.token_hex(32)
            digest = hashlib.sha256(credential.encode()).hexdigest()
            with connection(api.db_path, write=True) as con:
                con.execute('INSERT INTO browser_bindings(credential_hash) VALUES (?)', (digest,))
            response = JSONResponse(api.snapshot(None))
            response.set_cookie(COOKIE, credential, httponly=True, samesite='strict', max_age=60*60*24*30, path='/')
            return response

    @app.get('/api/menu')
    def menu(category: str | None = None):
        return api.menu(category)

    @app.get('/api/ai-status')
    def ai_status():
        return api.ai_status()

    @app.get('/api/acceptance-report')
    def acceptance_report(request: Request, download: bool = False):
        api.identity(request)
        headers = {'Content-Disposition': 'attachment; filename="acceptance-report.json"'} if download else {}
        return JSONResponse(api.events.report(), headers=headers)

    @app.post('/api/{path}')
    async def mutate(path: str, request: Request):
        digest, sid = api.identity(request)
        request_id = request.headers.get('x-bistro-request', '')
        if not 16 <= len(request_id) <= 128 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in request_id):
            raise APIError('invalid_input', 'A valid request ID is required.')
        body = await api.body(request)
        api.validate(path, body)
        from starlette.concurrency import run_in_threadpool
        response = await run_in_threadpool(api.execute, path, body, digest, sid, request_id)
        return JSONResponse(response, status_code=response.get('http_status', 200))
