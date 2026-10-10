"""Controlled mock inference, real temporary SQLite; never evidence of Qwen quality."""
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch
from urllib.error import HTTPError, URLError
from bistro.ai_engine import AIWaiter, factual_strings
from bistro.ollama_client import OllamaClient, OllamaError, MalformedResponse, ToolsUnsupported
from bistro.tool_schemas import TOOLS, REGISTRY, dispatch, validate_call
from bistro.database import initialize_database, connection
from bistro import order_service as orders
from fsm_agent import ConversationAgent, STATE_REVIEW, STATE_CONFIRMED


def calls(*values):
    return {'role': 'assistant', 'content': '', 'tool_calls': [{'function': v} for v in values]}


def call(name, **args):
    return {'name': name, 'arguments': args}


def reply(text='Salamat po!', facts=None, **extra):
    return {'role': 'assistant', 'content': json.dumps({'reply': text, 'facts': facts or [], **extra})}


class Phase4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'test.db'
        initialize_database(self.db)
        self.agent = ConversationAgent(db_path=self.db)
        self.agent.execute({'action': 'establish_session', 'arguments': {'table_no': 3}})
        self.sid = self.agent.session_id
        self.client = Mock(model='qwen2.5:7b')
        self.client.classify_request.return_value = 'general'  # Mock semantic routing; existing action/fault fixtures stay unchanged.
        self.ai = AIWaiter(agent=self.agent, client=self.client)

    def run_model(self, text, *outputs):
        self.client.chat.side_effect = outputs
        return self.ai.respond(text)

    def add_call(self, qty=2, name='Chicken Adobo'):
        return call('add_item_to_order', session_id=self.sid, item_name=name, qty=qty, instructions='')

    def add(self):
        self.assertTrue(dispatch(self.agent, self.add_call())['ok'])

    def review(self):
        self.add()
        self.assertTrue(dispatch(self.agent, call('request_review'))['ok'])
        return self.ai.snapshot()['review_token']

    def test_schemas_and_four_required_tools(self):
        self.assertEqual(len(TOOLS), len(REGISTRY))
        for t in TOOLS:
            p = t['function']['parameters']
            self.assertFalse(p['additionalProperties'])
            self.assertTrue(set(p['required']) <= set(p['properties']))
        self.assertTrue(dispatch(self.agent, call('get_menu_by_category', category='Mains'))['ok'])
        self.assertTrue(dispatch(self.agent, call('check_item_allergen', item_id=1, allergen='soy'))['ok'])
        self.assertTrue(dispatch(self.agent, self.add_call())['ok'])
        self.assertTrue(dispatch(self.agent, call('view_current_order', session_id=self.sid))['ok'])

    def test_invalid_and_unknown_tools(self):
        for c in [call('execute_sql', sql='DELETE FROM menu_items'), call('remove_item', line_id=True),
                  call('remove_item', line_id=1, unexpected=True), call('add_item_to_order', qty=1),
                  {'name': [], 'arguments': {}}, None]:
            self.assertFalse(dispatch(self.agent, c)['ok'])
        self.assertEqual(self.agent.context()['order']['items'], [])

    def test_session_isolation(self):
        other = ConversationAgent(db_path=self.db)
        other.execute({'action': 'establish_session', 'arguments': {'table_no': 4}})
        self.assertFalse(dispatch(other, self.add_call())['ok'])
        self.add()
        line = self.agent.context()['order']['items'][0]['line_id']
        self.assertFalse(dispatch(other, call('remove_item', line_id=line))['ok'])
        self.assertNotIn(self.sid, json.dumps(AIWaiter(agent=other, client=self.client)._context()))

    def test_english_taglish_real_sqlite_mock_qwen(self):
        for text in ["I'd like two Chicken Adobo.", 'Dalawang Chicken Adobo po.']:
            result = self.run_model(text, calls(self.add_call()), reply())
            self.assertTrue(result['ok'], result)
            self.assertTrue(result['tools'][0]['result']['ok'])
        self.assertEqual(self.agent.context()['order']['items'][0]['quantity'], 4)

    def test_tool_result_feedback(self):
        observed = []
        def model(messages, *args, **kwargs):
            observed.append(json.loads(json.dumps(messages)))
            return calls(self.add_call()) if len(observed) == 1 else calls(call('browse_menu'))
        self.client.chat.side_effect = model
        result = self.ai.respond('Add two Chicken Adobo and show the menu')
        self.assertTrue(result['ok'])
        feedback = next(m for m in observed[1] if m['role'] == 'tool')
        self.assertEqual(json.loads(feedback['content'])['data']['total'], '370.00')

    def test_hallucinated_item_and_substring_rejected(self):
        for name in ['Dragon Soup', 'Chicken', 'Rice']:
            self.assertFalse(dispatch(self.agent, self.add_call(name=name))['ok'])
        self.assertEqual(self.agent.context()['order']['items'], [])

    def test_grounding_facts_and_false_success(self):
        bad = reply(facts=['Dragon Soup costs PHP 1.00'])
        result = self.run_model('menu', bad, bad)
        self.assertFalse(result['ok'])
        self.assertEqual(self.client.chat.call_count, 2)
        good = factual_strings(orders.browse_menu(db_path=self.db))[0]
        self.assertTrue(self.run_model('menu', reply(facts=[good]))['ok'])
        self.assertFalse(self.run_model('hello', reply('Order confirmed!'), reply('Order confirmed!'))['ok'])

    def test_memory_and_pending_clarification(self):
        self.assertTrue(self.run_model('make one less salt', reply('Which portion would you like changed?', pending_question='Which portion would you like changed?'))['ok'])
        restarted = AIWaiter(agent=self.agent, client=self.client)
        ctx = restarted._context()
        self.assertEqual(ctx['context']['pending_question'], 'Which portion would you like changed?')
        self.assertIn('make one less salt', json.dumps(ctx['history']))
        for i in range(30):
            restarted._record('assistant', {'text': str(i)})
        self.assertLessEqual(len(restarted._context()['history']), 24)

    def test_greeting_capture_and_persistence(self):
        unbound = AIWaiter(agent=ConversationAgent(db_path=self.db), client=self.client)
        self.client.chat.side_effect = [calls(call('establish_session', table_no=9)), reply()]
        self.assertTrue(unbound.respond('Hello table nine po')['ok'])
        self.assertIn('Hello table nine po', json.dumps(unbound._context()))

    def test_explicit_confirmation_and_duplicate_checkout(self):
        token = self.review()
        c = call('confirm_order', confirmed=True, review_token=token)
        self.assertFalse(dispatch(self.agent, c)['ok'])
        result = self.run_model('Yes, confirm my order.', calls(c), reply())
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.agent.state, STATE_CONFIRMED)
        self.assertEqual(orders.check_stock(1, db_path=self.db)['stock_qty'], 28)
        self.assertTrue(dispatch(self.agent, c, confirmation_allowed=True, review_token=token)['ok'])
        self.assertEqual(orders.check_stock(1, db_path=self.db)['stock_qty'], 28)

    def test_unrelated_message_cannot_confirm(self):
        token = self.review()
        result = self.run_model('How much is my total?', calls(call('confirm_order', confirmed=True, review_token=token)), reply())
        self.assertFalse(result['ok'])
        self.assertEqual(result['tools'], [])  # Rejected before dispatch.
        self.assertEqual(self.agent.state, STATE_REVIEW)

    def test_same_turn_review_and_confirmation_blocked(self):
        self.add()
        result = self.run_model('confirm', calls(call('request_review'), call('confirm_order', confirmed=True, review_token='invented')), reply())
        self.assertFalse(result['ok'])
        self.assertEqual(result['tools'], [])  # Entire invalid batch has no effects.
        self.assertEqual(self.agent.state, 'STATE_TAKING_ORDER')
        self.assertIsNone(self.agent.context()['review'])

    def test_edit_invalidates_review_and_token(self):
        token = self.review()
        result = self.run_model('Yes, confirm my order.', calls(self.add_call(), call('confirm_order', confirmed=True, review_token=token)), reply())
        self.assertFalse(result['ok'])
        self.assertEqual(result['tools'], [])  # Confirmation never authorizes an edit.
        self.assertEqual(self.agent.state, STATE_REVIEW)
        self.assertEqual(self.agent.context()['order']['items'][0]['quantity'], 2)
        self.assertTrue(self.run_model('Add two Chicken Adobo', calls(self.add_call()))['ok'])
        self.assertIsNone(self.agent.context()['review'])
        result = self.run_model('Yes, confirm my order.', calls(call('confirm_order', confirmed=True, review_token=token)), reply())
        self.assertFalse(result['ok'])
        self.assertEqual(self.agent.context()['order']['items'][0]['quantity'], 4)
        self.assertEqual(orders.check_stock(1, db_path=self.db)['stock_qty'], 30)

    def test_empty_and_cancelled_guards(self):
        self.assertFalse(dispatch(self.agent, call('request_review'))['ok'])
        self.assertTrue(dispatch(self.agent, call('cancel_order'))['ok'])
        self.assertFalse(dispatch(self.agent, self.add_call())['ok'])

    def test_bad_batch_no_partial_mutation(self):
        bad = calls(self.add_call(), call('shell', command='boom'))
        self.assertFalse(self.run_model('add', bad, bad)['ok'])
        self.assertEqual(self.agent.context()['order']['items'], [])

    def test_repeated_mutation_not_retried(self):
        result = self.run_model('Add two Chicken Adobo and show the menu', calls(self.add_call()), calls(self.add_call()))
        self.assertFalse(result['ok'])
        self.assertEqual(self.agent.context()['order']['items'][0]['quantity'], 2)

    def test_loop_limit(self):
        self.ai.max_calls = 1
        bad = calls(call('browse_menu'), call('request_review'))
        self.assertFalse(self.run_model('menu', bad, bad)['ok'])
        self.assertEqual(self.client.chat.call_count, 2)

    def test_bounded_malformed_output(self):
        bad = {'role': 'assistant', 'content': 'not json'}
        self.assertFalse(self.run_model('hello', bad, bad)['ok'])
        self.assertEqual(self.client.chat.call_count, 2)

    def test_invalid_final_response_does_not_replay_mutation(self):
        bad = {'role': 'assistant', 'content': 'not json'}
        self.assertFalse(self.run_model('Add two Chicken Adobo and show the menu', calls(self.add_call()), bad, bad)['ok'])
        self.assertEqual(self.agent.context()['order']['items'][0]['quantity'], 2)

    def test_failed_database_operation(self):
        with connection(self.db, write=True) as con:
            con.execute("CREATE TRIGGER fail_cart BEFORE INSERT ON order_line_items BEGIN SELECT RAISE(ABORT,'failure'); END")
        result = self.run_model('add', calls(self.add_call()), reply())
        self.assertFalse(result['tools'][0]['result']['ok'])
        self.assertEqual(self.agent.context()['order']['items'], [])

    def test_out_of_stock(self):
        self.assertFalse(dispatch(self.agent, self.add_call(qty=31))['ok'])

    def test_transport_failure_no_fallback(self):
        result = self.run_model('add', OllamaError('Cannot reach local Ollama'))
        self.assertFalse(result['ok'])
        self.assertFalse(result['inference_verified'])
        self.assertEqual(result['source'], 'application_error')
        self.assertEqual(self.agent.context()['order']['items'], [])

    def test_structured_fallback(self):
        structured = {'role': 'assistant', 'content': json.dumps({'calls': [self.add_call()]})}
        self.assertTrue(self.run_model('add', ToolsUnsupported('unsupported'), structured, reply())['ok'])
        self.assertTrue(self.ai.structured)

    def test_taglish_followup_customization_removal_total(self):
        self.assertTrue(self.run_model('Dalawang Chicken Adobo po.', calls(self.add_call()), reply())['ok'])
        line = self.agent.context()['order']['items'][0]['line_id']
        # Controlled Qwen splits a customized portion into its own real cart line.
        customize = call('add_item_to_order', session_id=self.sid, item_name='Chicken Adobo', qty=1, instructions='no onions')
        self.assertTrue(self.run_model('Walang sibuyas yung isa.', calls(call('update_quantity', line_id=line, qty=1), customize), reply())['ok'])
        cart = self.agent.context()['order']
        self.assertEqual(len(cart['items']), 2)
        self.assertEqual(cart['total'], '370.00')
        self.assertTrue(self.run_model('Tanggalin yung walang sibuyas.', calls(call('remove_item', line_id=cart['items'][1]['line_id'])), reply())['ok'])
        self.assertTrue(self.run_model('Magkano na total?', calls(call('view_current_order', session_id=self.sid)), reply(facts=['Total: PHP 185.00.']))['ok'])

    def test_ai_cli_and_interrupt(self):
        from virtual_waiter_prototype import main
        lines = iter(['Dalawang Chicken Adobo po.', 'quit'])
        output = []
        with patch('bistro.ollama_client.OllamaClient', return_value=self.client):
            self.client.chat.side_effect = [calls(self.add_call()), reply()]
            self.assertEqual(main(['--ai', '--table', '3', '--db', str(self.db)], get_input=lambda _: next(lines), send_output=output.append), 0)
        self.assertTrue(any('AI mode: local Ollama' in line for line in output))
        self.assertEqual(self.agent.context()['order']['total'], '370.00')

    def test_hallucinated_question_blocked(self):
        bad = reply('Would you like Dragon Soup?')
        self.assertFalse(self.run_model('menu', bad, bad)['ok'])

    def test_closed_order_edits_blocked(self):
        token = self.review()
        self.assertTrue(dispatch(self.agent, call('confirm_order', confirmed=True, review_token=token), confirmation_allowed=True, review_token=token)['ok'])
        self.assertFalse(dispatch(self.agent, self.add_call())['ok'])

    def test_stale_fact_after_edit_blocked(self):
        self.add()
        bad = reply(facts=['Total: PHP 370.00.'])
        self.assertFalse(self.run_model('Add two Chicken Adobo and show the menu', calls(self.add_call()), bad, bad)['ok'])
        self.assertEqual(self.agent.context()['order']['total'], '740.00')


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.client = OllamaClient(timeout=1)
        self.client.opener = MagicMock()

    def test_actual_payload_and_verified_response(self):
        response = self.client.opener.open.return_value.__enter__.return_value
        response.read.return_value = json.dumps({'done': True, 'message': reply()}).encode()
        self.assertEqual(self.client.chat([{'role': 'user', 'content': 'hello'}], TOOLS)['role'], 'assistant')
        req = self.client.opener.open.call_args.args[0]
        data = json.loads(req.data)
        self.assertEqual(data['model'], 'qwen2.5:7b')
        self.assertFalse(data['stream'])
        self.assertEqual(req.full_url, 'http://127.0.0.1:11434/api/chat')

    def test_connection_timeout_missing_model(self):
        for error, expected in [(URLError('refused'), 'Cannot reach'), (socket.timeout(), 'timed out'),
                                (HTTPError('url',404,'missing',{},io.BytesIO(b'missing')), 'ollama pull qwen2.5:7b')]:
            self.client.opener.open.side_effect = error
            with self.assertRaisesRegex(OllamaError, expected):
                self.client.chat([])

    def test_empty_malformed_incomplete_response(self):
        response = self.client.opener.open.return_value.__enter__.return_value
        for raw in [b'bad', b'[]', b'{}', b'{"done":true,"message":{"role":"assistant","content":""}}', b'{"done":false,"message":{"role":"assistant","content":"hello"}}']:
            response.read.return_value = raw
            with self.assertRaises(MalformedResponse):
                self.client.chat([])

    def test_configuration_validation(self):
        for kwargs in [{'url':'https://example.com'}, {'model':'qwen2.5'}, {'timeout':0}, {'timeout':float('nan')}]:
            with self.assertRaises(ValueError):
                OllamaClient(**kwargs)


if __name__ == '__main__':
    unittest.main()
