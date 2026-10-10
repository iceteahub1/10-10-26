"""Windows failure reproduction: mocked inference, real disposable SQLite/FSM."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from bistro.ai_engine import AIWaiter
from bistro.database import initialize_database
from bistro.ollama_client import OllamaError, MalformedResponse, ToolsUnsupported
from bistro import order_service as orders
from fsm_agent import ConversationAgent


def native(name, **arguments):
    return {'content': '', 'tool_calls': [{'function': {'name': name, 'arguments': arguments}}]}


def addition(qty=2, **extra):
    return native('add_item_to_order', item_name='Chicken Adobo', qty=qty, **extra)


class PendingRequestTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name) / 'test.db'
        initialize_database(self.db)
        self.agent = ConversationAgent(db_path=self.db)
        self.client = Mock(model='qwen2.5:any-compatible-size')
        self.engine = AIWaiter(agent=self.agent, client=self.client)

    def ask_table(self, text='Dalawang Chicken Adobo po.'):
        result = self.engine.respond(text)
        self.assertTrue(result['ok'])
        self.assertIn('table number', result['response'])
        self.client.chat.assert_not_called()
        self.assertIsNone(self.agent.session_id)

    def table(self, *responses):
        self.client.chat.side_effect = responses
        return self.engine.respond('Table 3 po.')

    def cart(self):
        return self.agent.context()['order']['items']

    def test_exact_windows_read_then_empty_reports_unfulfilled_addition(self):
        self.ask_table()
        result = self.table(native('view_current_order', session_id='invented'),
                            {'content': ''}, {'content': ''})
        self.assertFalse(result['ok'])
        self.assertFalse(result['partial_success'])
        self.assertFalse(result['order_mutation_succeeded'])
        self.assertEqual(self.agent.context()['table_no'], 3)
        self.assertEqual(self.cart(), [])
        self.assertIn('2 × Chicken Adobo was NOT added', result['response'])
        self.assertIn('cart view does not add food', result['response'])
        self.assertNotIn('remain saved', result['response'])
        self.assertNotIn('Expecting value', result['response'])
        self.assertEqual(self.client.chat.call_count, 3)
        self.assertEqual(self.engine.pending_request, 'Dalawang Chicken Adobo po.')
        self.assertEqual(orders.check_stock(1, db_path=self.db)['stock_qty'], 30)

    def test_read_then_matching_addition_completes_with_no_acknowledgement_query(self):
        self.ask_table()
        result = self.table(native('view_current_order'), addition(session_id='made-up'),
                            MalformedResponse('must never be requested'))
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['partial_success'])
        self.assertTrue(result['order_mutation_succeeded'])
        self.assertEqual(self.cart()[0]['quantity'], 2)
        self.assertIn('Total: PHP 370.00.', result['response'])
        self.assertEqual(self.client.chat.call_count, 2)
        self.assertIsNone(self.engine.pending_request)

    def test_read_empty_then_structured_retry_adds_once(self):
        self.ask_table()
        structured = {'content': json.dumps({'calls': [{'name': 'add_item_to_order', 'arguments':
                                       {'item_name': 'Chicken Adobo', 'qty': '2'}}]})}
        result = self.table(native('view_current_order'), {'content': ''}, structured)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.cart()[0]['quantity'], 2)
        self.assertTrue(self.engine.structured)
        self.assertTrue(self.client.chat.call_args_list[2].kwargs['structured'])
        self.assertIsNone(self.client.chat.call_args_list[2].args[1])
        retry_messages = self.client.chat.call_args_list[2].args[0]
        self.assertFalse(any(m.get('tool_calls') or m['role'] == 'tool' for m in retry_messages))
        self.assertTrue(any('saved_tool_result' in m.get('content', '') for m in retry_messages))
        self.assertNotIn('session_id', json.dumps(retry_messages))
        self.assertEqual(self.client.chat.call_count, 3)

    def test_non_json_followup_cannot_claim_pending_addition_succeeded(self):
        self.ask_table()
        prose = {'content': 'Your order has been added!'}
        result = self.table(native('view_current_order'), prose, prose)
        self.assertFalse(result['ok'])
        self.assertFalse(result['partial_success'])
        self.assertEqual(self.cart(), [])
        self.assertIn('was NOT added', result['response'])
        self.assertNotIn('Your order has been added', result['response'])
        self.assertNotIn('Expecting value', result['response'])

    def test_unsupported_native_tools_retains_request_in_structured_mode(self):
        self.ask_table()
        structured = {'content': json.dumps({'name': 'add_item_to_order', 'arguments':
                                            {'item_name': 'Chicken Adobo', 'qty': 2}})}
        result = self.table(ToolsUnsupported('unsupported'), structured)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.cart()[0]['quantity'], 2)
        self.assertEqual(self.client.chat.call_count, 2)

    def test_read_then_timeout_is_not_partial_order_success(self):
        self.ask_table()
        result = self.table(native('view_current_order'), OllamaError('timed out'))
        self.assertFalse(result['ok'])
        self.assertFalse(result['partial_success'])
        self.assertIn('was NOT added', result['response'])
        self.assertEqual(self.cart(), [])
        self.assertEqual(self.client.chat.call_count, 2)

    def test_read_then_false_acknowledgement_does_not_fulfill_addition(self):
        self.ask_table()
        ack = {'content': '{"reply":"Thank you!","facts":[]}'}
        result = self.table(native('view_current_order'), ack, ack)
        self.assertFalse(result['ok'])
        self.assertFalse(result['partial_success'])
        self.assertEqual(self.cart(), [])
        self.assertIn('was NOT added', result['response'])

    def test_incorrect_quantity_or_other_mutation_cannot_replace_request(self):
        for response in (addition(qty=1), addition(instructions='no onions'), native('cancel_order')):
            with self.subTest(response=response):
                if self.agent.session_id is None:
                    self.ask_table()
                self.client.chat.side_effect = [response, response]
                result = self.engine.respond('Table 3 po.' if self.agent.session_id is None else 'Dalawang Chicken Adobo po.')
                self.assertFalse(result['ok'])
                self.assertEqual(self.cart(), [])

    def test_ambiguous_or_conditional_retained_order_requires_restatement(self):
        for text in ('If it has no soy, two Chicken Adobo please.',
                     'Maybe two Chicken Adobo.', 'Do I already have two Chicken Adobo?',
                     "Don't add two Chicken Adobo.", 'Two Chicken Adobo, one without onions.'):
            with self.subTest(text=text):
                engine = AIWaiter(agent=ConversationAgent(db_path=self.db), client=self.client)
                engine.respond(text)
                result = engine.respond('Table 3 po.')
                self.assertEqual(result['source'], 'application_prompt')
                self.assertIn('restate', result['response'])
                self.assertEqual(engine.agent.context()['order']['items'], [])
        self.client.chat.assert_not_called()

    def test_table_reply_with_changed_intent_never_executes_old_order(self):
        self.ask_table()
        self.client.chat.side_effect = [addition()]
        result = self.engine.respond('Table 3 po. Actually make it one, not two.')
        self.assertEqual(result['source'], 'application_prompt')
        self.client.chat.assert_not_called()
        self.assertEqual(self.cart(), [])

    def test_correction_while_waiting_replaces_retained_request(self):
        self.ask_table()
        self.engine.respond('One Chicken Adobo please.')
        result = self.table(addition(qty=1))
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.cart()[0]['quantity'], 1)

    def test_read_on_later_turn_does_not_replay_retained_failed_addition(self):
        self.ask_table()
        self.table(native('view_current_order'), {'content': ''}, {'content': ''})
        self.client.chat.reset_mock()
        self.client.chat.side_effect = [native('view_current_order')]
        result = self.engine.respond('What is my total?')
        self.assertTrue(result['ok'])
        self.assertFalse(result['order_mutation_succeeded'])
        self.assertFalse(result['partial_success'])
        self.assertEqual(self.cart(), [])
        self.assertEqual(self.engine.pending_request, 'Dalawang Chicken Adobo po.')
        self.assertEqual(self.client.chat.call_count, 1)

    def test_successful_read_alone_needs_no_model_acknowledgement(self):
        self.agent.execute({'action': 'establish_session', 'arguments': {'table_no': 3}})
        self.client.chat.side_effect = [native('view_current_order'), {'content': ''}]
        result = self.engine.respond('View my cart')
        self.assertTrue(result['ok'])
        self.assertFalse(result['partial_success'])
        self.assertFalse(result['order_mutation_succeeded'])
        self.assertEqual(self.client.chat.call_count, 1)

    def test_successful_mutation_does_not_query_again_across_model_labels(self):
        self.ask_table()
        for label in ('qwen2.5:3b', 'qwen2.5:7b', 'qwen2.5:14b', 'qwen2.5:custom'):
            with self.subTest(model=label):
                self.client.model = label
                self.client.chat.reset_mock()
                self.client.chat.side_effect = [addition(), OllamaError('unused acknowledgement timeout')]
                result = self.engine.respond('Table 3 po.' if self.agent.session_id is None else 'Dalawang Chicken Adobo po.')
                self.assertTrue(result['ok'], result)
                self.assertEqual(self.client.chat.call_count, 1)
        self.assertEqual(self.cart()[0]['quantity'], 8)

    def test_partial_mutation_then_read_failure_preserves_only_completed_portions(self):
        self.ask_table('Two Chicken Adobo and one Garlic Rice please.')
        result = self.table(addition(), native('view_current_order'), {'content': ''}, {'content': ''})
        self.assertFalse(result['ok'])
        self.assertTrue(result['partial_success'])
        self.assertTrue(result['order_mutation_succeeded'])
        self.assertEqual(self.cart()[0]['quantity'], 2)
        self.assertEqual(len(self.cart()), 1)
        self.assertIn('was NOT added', result['response'])
        self.assertIn('remain saved', result['response'])

    def test_current_complex_addition_is_not_fulfilled_by_a_read(self):
        self.agent.execute({'action': 'establish_session', 'arguments': {'table_no': 3}})
        self.client.chat.side_effect = [native('view_current_order'), {'content': ''}, {'content': ''}]
        result = self.engine.respond('Add two Chicken Adobo with no onions please.')
        self.assertFalse(result['ok'])
        self.assertFalse(result['partial_success'])
        self.assertEqual(self.cart(), [])
        self.assertIn('addition was NOT executed', result['response'])

    def test_explicit_clarification_keeps_addition_unfulfilled(self):
        self.ask_table()
        result = self.table(native('view_current_order'), {'content': json.dumps({
            'reply': 'What special instructions would you like?', 'facts': [],
            'pending_question': 'What special instructions would you like?'})})
        self.assertFalse(result['ok'])
        self.assertFalse(result['partial_success'])
        self.assertIn('was NOT added', result['response'])
        self.assertEqual(self.cart(), [])
        self.assertEqual(self.engine._context()['context']['pending_question'], 'What special instructions would you like?')


if __name__ == '__main__':
    unittest.main()
