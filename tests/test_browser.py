"""Real local HTTP + disposable SQLite/FSM; only Ollama inference is mocked."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.cookiejar import CookieJar
import json
import hashlib
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler, Request
from uuid import uuid4
import uvicorn
from app import create_app
from bistro.config import load_settings
from bistro.database import connection
from bistro import order_service as orders, conversation_store as memory
from bistro.ollama_client import OllamaError
from fsm_agent import ConversationAgent


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'restaurant.db'
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
        self.settings = replace(load_settings(), port=port)
        self.client = Mock(model=self.settings.model)
        self.client.classify_request.return_value = 'general'
        self.app = create_app(settings=self.settings, db_path=self.db, client_factory=lambda: self.client)
        self.api = self.app.state.browser_api
        self.server = uvicorn.Server(uvicorn.Config(self.app, host='127.0.0.1', port=port, log_level='critical'))
        self.thread = threading.Thread(target=self.server.run, daemon=True); self.thread.start()
        self.url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 5
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.server.started)
        self.browser, self.cookies = self.new_browser()
        self.request('session')
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.should_exit = True; self.thread.join(5); self.temp.cleanup()

    def new_browser(self):
        jar = CookieJar()
        return build_opener(ProxyHandler({}), HTTPCookieProcessor(jar)), jar

    def request(self, path, body=None, *, browser=None, request_id=None, headers=None, raw=None):
        data = json.dumps(body).encode() if body is not None else raw
        defaults = {'Origin': self.url, 'Content-Type': 'application/json', 'X-Bistro-Request': request_id or uuid4().hex}
        if headers:
            defaults.update(headers)
        request = Request(self.url + '/api/' + path, data=data, headers=defaults)
        try:
            response = (browser or self.browser).open(request, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())

    def start(self, table=3, browser=None, request_id=None):
        status, result = self.request('session', {'table_no': table}, browser=browser, request_id=request_id)
        self.assertEqual(status, 200, result); self.assertTrue(result['ok'], result)
        return result['snapshot']

    def action(self, name, browser=None, request_id=None, **args):
        return self.request('action', {'action': name, 'arguments': args}, browser=browser, request_id=request_id)

    def add(self, qty=2):
        status, result = self.action('add_item', item_name='Chicken Adobo', qty=qty, instructions='less salt')
        self.assertEqual(status, 200); self.assertTrue(result['ok'], result)
        return result['snapshot']

    def review(self):
        self.action('request_review')
        return self.request('session')[1]['review_token']

    def confirm(self, token, request_id=None):
        return self.request('confirm', {'confirmed': True, 'review_token': token}, request_id=request_id)

    def test_session_cookie_refresh_restart_and_isolation(self):
        self.start(); snapshot = self.add()
        self.assertNotIn('session_id', json.dumps(snapshot))
        cookie = next(iter(self.cookies))
        self.assertIn('HttpOnly', cookie._rest); self.assertEqual(cookie._rest['SameSite'], 'strict')
        self.assertEqual(self.request('session')[1]['order'], snapshot['order'])
        with connection(self.db) as con:
            digest = con.execute('SELECT credential_hash FROM browser_bindings').fetchone()[0]
            self.assertNotEqual(digest, cookie.value)
        self.api.initialize()  # repeated initialization must preserve all data
        other, _ = self.new_browser(); self.request('session', browser=other); self.start(3, other)
        self.assertEqual(self.request('session', browser=other)[1]['order']['items'], [])
        line = snapshot['order']['items'][0]['line_id']
        result = self.action('remove_item', browser=other, line_id=line)[1]
        self.assertFalse(result['ok'])
        self.assertEqual(self.request('session')[1]['order']['items'][0]['quantity'], 2)
        self.assertEqual(self.request('action', {'action': 'establish_session', 'arguments': {'table_no': 3, 'session_id': 'guessed'}})[0], 400)

    def test_session_creation_request_retry_and_no_table_takeover(self):
        key = uuid4().hex
        first = self.start(request_id=key); second = self.start(request_id=key)
        self.assertEqual(first, second)
        self.assertEqual(self.request('session', {'table_no': 5})[0], 409)
        self.assertEqual(self.request('session', {'table_no': 3, 'session_id': 'arbitrary'})[0], 400)
        with connection(self.db) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM table_sessions').fetchone()[0], 1)

    def test_live_menu_prices_stock_allergens(self):
        status, menu = self.request('menu'); self.assertEqual(status, 200)
        self.assertEqual(menu, orders.browse_menu(db_path=self.db))
        item = next(i for i in menu if i['name'] == 'Chicken Adobo')
        self.assertEqual(item['price_cents'], int(float(item['price']) * 100)); self.assertIn('soy', item['allergens'])
        self.assertTrue(all(i['category'] == 'Mains' for i in self.request('menu?category=Mains')[1]))
        with connection(self.db, write=True) as con:
            con.execute('UPDATE menu_items SET stock_qty=0, available=0 WHERE id=?', (item['id'],))
        changed = next(i for i in self.request('menu')[1] if i['id'] == item['id'])
        self.assertEqual(changed['stock_qty'], 0); self.assertEqual(changed['available'], 0)

    def test_cart_quantity_instructions_remove_and_review_invalidation(self):
        self.start(); cart = self.add(); line = cart['order']['items'][0]
        token = self.review(); self.assertEqual(len(token), 64)
        changed = self.action('update_quantity', line_id=line['line_id'], qty=3)[1]
        self.assertTrue(changed['ok']); self.assertIsNone(changed['snapshot']['review_token'])
        self.assertEqual(changed['snapshot']['order']['total_cents'], line['unit_price_cents'] * 3)
        self.assertEqual(self.confirm(token)[0], 409)
        changed = self.action('update_instructions', line_id=line['line_id'], instructions='no extra sauce')[1]
        self.assertEqual(changed['snapshot']['order']['items'][0]['special_instructions'], 'no extra sauce')
        self.assertTrue(self.action('remove_item', line_id=line['line_id'])[1]['ok'])
        self.assertEqual(self.request('session')[1]['order']['items'], [])
        self.assertFalse(self.action('request_review')[1]['ok'])

    def test_explicit_checkout_duplicate_receipt_and_stock(self):
        self.start(); cart = self.add(); item = cart['order']['items'][0]
        self.assertEqual(self.confirm('a' * 64)[0], 409)
        token = self.review()
        self.assertEqual(self.request('confirm', {'confirmed': 'true', 'review_token': token})[0], 400)
        self.assertEqual(self.request('confirm', {'confirmed': False, 'review_token': token})[0], 400)
        key = uuid4().hex
        first = self.confirm(token, key)[1]; self.assertTrue(first['ok'], first)
        again = self.confirm(token, key)[1]; self.assertEqual(first, again)
        receipt = first['snapshot']['order']; self.assertTrue(receipt['receipt_id'])
        self.assertEqual(self.confirm(token)[1]['snapshot']['order'], receipt)
        self.assertEqual(self.request('session')[1]['order'], receipt)
        with connection(self.db) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT stock_qty FROM menu_items WHERE id=?', (item['item_id'],)).fetchone()[0], item['stock_qty'] - 2)
        self.assertFalse(self.action('add_item', item_name='Chicken Adobo', qty=1)[1]['ok'])
        self.start(4)
        self.assertEqual(self.request('session')[1]['order']['items'], [])

    def test_stock_changed_and_stale_menu_review_never_confirm(self):
        self.start(); self.add(); token = self.review()
        with connection(self.db, write=True) as con:
            con.execute("UPDATE menu_items SET stock_qty=0 WHERE name='Chicken Adobo'")
        result = self.confirm(token)[1]
        self.assertFalse(result['ok']); self.assertEqual(result['code'], 'stale_review')
        self.assertIsNone(result['snapshot']['review_token'])
        with connection(self.db) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM receipts').fetchone()[0], 0)
        result = self.action('add_item', item_name='Chicken Adobo', qty=1)[1]
        self.assertEqual(result['code'], 'out_of_stock')

    def test_duplicate_add_and_request_id_conflict(self):
        self.start(); key = uuid4().hex
        first = self.action('add_item', request_id=key, item_name='Chicken Adobo', qty=1)[1]
        second = self.action('add_item', request_id=key, item_name='Chicken Adobo', qty=1)[1]
        self.assertEqual(first, second)
        self.assertEqual(self.action('add_item', request_id=key, item_name='Chicken Adobo', qty=2)[0], 409)
        self.assertEqual(self.request('session')[1]['order']['items'][0]['quantity'], 1)

    def test_ai_chat_shared_engine_and_cart_sync_taglish(self):
        self.start()
        self.client.chat.return_value = {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'add_item_to_order', 'arguments': {'item_name': 'Chicken Adobo', 'qty': 2, 'instructions': ''}}}]}
        key = uuid4().hex
        status, result = self.request('chat', {'message': 'Dalawang Chicken Adobo po.'}, request_id=key)
        self.assertEqual(status, 200); self.assertTrue(result['ok'], result)
        self.assertEqual(result['snapshot']['order']['items'][0]['quantity'], 2)
        self.assertIn('Order updated', result['message'])
        self.assertIn('Dalawang Chicken Adobo po.', json.dumps(result['snapshot']['messages']))
        self.request('chat', {'message': 'Dalawang Chicken Adobo po.'}, request_id=key)
        self.assertEqual(self.client.chat.call_count, 1)
        self.assertEqual(self.request('session')[1]['order']['items'][0]['quantity'], 2)

    def test_english_ai_and_review_confirmation_integration(self):
        self.start()
        self.client.chat.return_value = {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'add_item_to_order', 'arguments': {'item_name': 'Chicken Adobo', 'qty': 1, 'instructions': ''}}}]}
        self.assertTrue(self.request('chat', {'message': "I'd like one Chicken Adobo."})[1]['ok'])
        self.client.chat.return_value = {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'request_review', 'arguments': {}}}]}
        review = self.request('chat', {'message': 'Can I review my order?'})[1]
        self.assertTrue(review['ok'], review); self.assertTrue(review['snapshot']['review_token'])
        self.client.chat.return_value = {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'confirm_order', 'arguments': {'confirmed': True}}}]}
        confirmed = self.request('chat', {'message': 'Yes, confirm my order.'})[1]
        self.assertTrue(confirmed['ok'], confirmed); self.assertTrue(confirmed['snapshot']['order']['receipt_id'])

    def test_unexpected_adapter_failure_rolls_back_deterministic_action(self):
        self.start()
        with patch.object(self.api, 'snapshot', side_effect=OSError('test failure')):
            result = self.action('add_item', item_name='Chicken Adobo', qty=1)
        self.assertEqual(result[0], 500); self.assertEqual(result[1]['code'], 'application_failure')
        self.assertEqual(self.request('session')[1]['order']['items'], [])

    def test_server_restart_retains_cookie_receipt_and_recovers_interrupted_claim(self):
        self.start(); self.add(); self.confirm(self.review())
        receipt = self.request('session')[1]['order']
        digest = hashlib.sha256(next(iter(self.cookies)).value.encode()).hexdigest()
        body = {'message': 'hi'}; key = uuid4().hex
        fingerprint = hashlib.sha256(memory.encode({'path': 'chat', 'body': body}).encode()).hexdigest()
        with connection(self.db, write=True) as con:
            con.execute('INSERT INTO browser_requests VALUES (?,?,?,NULL)', (digest, key, fingerprint))
        self.server.should_exit = True; self.thread.join(5)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host='127.0.0.1', port=self.settings.port, log_level='critical'))
        self.thread = threading.Thread(target=self.server.run, daemon=True); self.thread.start()
        deadline = time.monotonic() + 5
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.server.started)
        self.assertEqual(self.request('session')[1]['order'], receipt)
        status, result = self.request('chat', body, request_id=key)
        self.assertEqual(status, 409); self.assertEqual(result['code'], 'request_interrupted')
        self.client.chat.assert_not_called()

    def test_ai_model_failure_and_unavailable_keep_ordering(self):
        self.start(); self.client.chat.side_effect = OllamaError('Cannot reach local Ollama.')
        result = self.request('chat', {'message': 'Dalawang Chicken Adobo po.'})[1]
        self.assertFalse(result['ok']); self.assertEqual(result['code'], 'ai_unavailable')
        self.assertEqual(result['snapshot']['order']['items'], [])
        self.api.client_factory = None
        with patch('bistro.browser_api.build_opener') as opener:
            opener.return_value.open.side_effect = OSError()
            status = self.request('ai-status')[1]; self.assertEqual(status['code'], 'ollama_unavailable')
            self.assertEqual(self.request('chat', {'message': 'Hello'})[0], 503)
        self.add(); self.assertTrue(self.confirm(self.review())[1]['ok'])

    def test_ai_missing_disabled_and_bad_status(self):
        self.api.client_factory = None
        with patch('bistro.browser_api.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b'{"models":[]}'
            self.assertEqual(self.request('ai-status')[1]['code'], 'model_missing')
            response.read.return_value = b'not json'
            self.assertEqual(self.request('ai-status')[1]['code'], 'ai_unavailable')
        self.api.settings = replace(self.settings, ai_enabled=False)
        self.assertEqual(self.request('ai-status')[1]['code'], 'ai_disabled')

    def test_ai_cannot_rebind_closed_or_other_table(self):
        self.start(); self.add(); self.confirm(self.review())
        result = self.request('chat', {'message': 'table 9; order Chicken Adobo'})[1]
        self.assertFalse(result['ok']); self.client.chat.assert_not_called()
        self.assertEqual(self.request('session')[1]['order']['table_no'], 3)
        with connection(self.db) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM table_sessions').fetchone()[0], 1)

    def test_api_security_and_malformed_inputs(self):
        self.start()
        for origin in ('https://evil.example', 'null', ''):
            self.assertEqual(self.request('session', {'table_no': 3}, headers={'Origin': origin})[0], 403)
        self.assertEqual(self.request('menu', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('session', {'table_no': 3}, headers={'X-Bistro-Request': ''})[0], 400)
        self.assertEqual(self.request('session', raw=b'not json')[0], 400)
        self.assertEqual(self.request('session', raw=b'{"table_no":NaN}')[0], 400)
        self.assertEqual(self.request('session', raw=b'x'*17000)[0], 413)
        self.assertEqual(self.request('session', {'table_no': 3}, headers={'Content-Type': 'text/plain'})[0], 415)
        for body in ({'table_no': True}, {'table_no': -1}, {'table_no': '3'}, {'table_no': 3, 'session_id': 'evil'}):
            self.assertEqual(self.request('session', body)[0], 400)
        for body in ({'message': []}, {'message': ''}, {'message': 'a'*10001}, {'message': 'hi', 'session_id': 'evil'}):
            self.assertEqual(self.request('chat', body)[0], 400)
        self.assertEqual(self.action('add_item', item_name='Chicken Adobo', qty=True)[0], 400)
        anonymous, _ = self.new_browser()
        self.assertEqual(self.request('session', {'table_no': 3}, browser=anonymous)[0], 401)
        self.assertEqual(self.request('session', {'table_no': 3}, headers={'Cookie': 'bistro_browser=' + 'a'*64})[0], 401)
        with self.browser.open(self.url) as response:
            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            html = response.read().decode(); self.assertIn('Review before you confirm', html)
        with self.browser.open(self.url + '/static/js/dashboard.js') as response:
            script = response.read().decode(); self.assertNotIn('innerHTML', script); self.assertIn('textContent', script)

    def test_concurrent_duplicate_checkout(self):
        self.start(); item = self.add()['order']['items'][0]; token = self.review(); key = uuid4().hex
        cookie = next(iter(self.cookies)).value
        def confirm(_):
            client, _ = self.new_browser()
            return self.request('confirm', {'confirmed': True, 'review_token': token}, browser=client, request_id=key, headers={'Cookie': 'bistro_browser=' + cookie})
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(confirm, range(2)))
        self.assertTrue(any(r[1].get('ok') for r in results))
        with connection(self.db) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT stock_qty FROM menu_items WHERE name='Chicken Adobo'").fetchone()[0], item['stock_qty'] - 2)

    def test_pending_request_blocks_replay_and_other_writes(self):
        self.start()
        digest = hashlib.sha256(next(iter(self.cookies)).value.encode()).hexdigest()
        body = {'message': 'hi'}; key = uuid4().hex
        fingerprint = hashlib.sha256(memory.encode({'path': 'chat', 'body': body}).encode()).hexdigest()
        with connection(self.db, write=True) as con:
            con.execute('INSERT INTO browser_requests VALUES (?,?,?,NULL)', (digest, key, fingerprint))
        self.assertEqual(self.request('chat', body, request_id=key)[0], 409)
        self.assertEqual(self.action('add_item', item_name='Chicken Adobo', qty=1)[0], 409)
        self.assertEqual(self.request('session')[1]['order']['items'], [])

    def test_cli_shares_receipt_and_invalidates_browser_review(self):
        self.start(); self.add(); token = self.review()
        with connection(self.db) as con:
            sid = con.execute('SELECT session_id FROM browser_bindings').fetchone()[0]
        cli = ConversationAgent(db_path=self.db)
        cli.execute({'action': 'establish_session', 'arguments': {'table_no': 3, 'session_id': sid}})
        cli.execute({'action': 'add_item', 'arguments': {'item_name': 'Chicken Adobo', 'qty': 1}})
        self.assertEqual(self.confirm(token)[0], 409)
        cli.execute({'action': 'request_review', 'arguments': {}})
        result = cli.execute({'action': 'confirm_order', 'arguments': {'confirmed': True}})
        self.assertTrue(result['ok'])
        self.assertEqual(self.request('session')[1]['order']['receipt_id'], result['data']['receipt_id'])


if __name__ == '__main__':
    unittest.main()
