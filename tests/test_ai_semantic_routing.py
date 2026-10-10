"""Mock semantic/action inference; real disposable FSM/SQLite, never real Qwen."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from bistro.ai_engine import AIWaiter
from bistro.database import initialize_database
from bistro.ollama_client import OllamaClient, OllamaError, MalformedResponse
from bistro.tool_schemas import dispatch
from bistro import order_service as orders
from fsm_agent import ConversationAgent, STATE_REVIEW, STATE_CONFIRMED, STATE_TAKING_ORDER


def tool(name, **args):
    return {'content': '', 'tool_calls': [{'function': {'name': name, 'arguments': args}}]}


class SemanticRoutingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name) / 'test.db'
        initialize_database(self.db)
        self.agent = ConversationAgent(db_path=self.db)
        self.agent.execute({'action': 'establish_session', 'arguments': {'table_no': 3}})
        dispatch(self.agent, {'name': 'add_item_to_order', 'arguments': {
            'session_id': self.agent.session_id, 'item_name': 'Chicken Adobo', 'qty': 2, 'instructions': ''}})
        self.client = Mock(model='qwen2.5:arbitrary-compatible-tag')
        self.ai = AIWaiter(agent=self.agent, client=self.client)

    def route(self, text, intent, *outputs):
        self.client.classify_request.return_value = intent
        self.client.chat.side_effect = outputs
        return self.ai.respond(text)

    def test_total_wrong_menu_then_cart_recovers_without_mutation(self):
        result = self.route('Magkano na total?', 'view_current_order', tool('browse_menu'), tool('view_current_order'))
        self.assertTrue(result['ok'], result)
        self.assertIn('Total: PHP 370.00.', result['response'])
        self.assertFalse(result['order_mutation_succeeded'])
        self.assertFalse(result['partial_success'])
        self.assertEqual([t['call']['name'] for t in result['tools']], ['view_current_order'])
        self.assertEqual(self.client.classify_request.call_count, 1)
        self.assertEqual(self.client.chat.call_count, 2)
        self.assertEqual({t['function']['name'] for t in self.client.chat.call_args_list[0].args[1]}, {'view_current_order'})

    def test_review_wrong_menu_then_real_review_enters_review_state(self):
        result = self.route('Can I review my order?', 'request_review', tool('browse_menu'), tool('request_review'))
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.agent.state, STATE_REVIEW)
        self.assertTrue(self.ai.snapshot()['review_token'])
        self.assertIn('Total: PHP 370.00.', result['response'])
        self.assertEqual([t['call']['name'] for t in result['tools']], ['request_review'])
        self.assertEqual(self.client.chat.call_count, 2)

    def test_repeated_wrong_tool_is_not_successful_fulfillment(self):
        for intent in ('view_current_order', 'request_review'):
            with self.subTest(intent=intent), patch('bistro.ai_engine.dispatch', wraps=dispatch) as execute:
                result = self.route('Current customer request', intent, tool('browse_menu'), tool('browse_menu'))
                self.assertFalse(result['ok'])
                self.assertFalse(result['partial_success'])
                self.assertEqual(result['tools'], [])
                execute.assert_not_called()
                self.assertEqual(self.agent.state, STATE_TAKING_ORDER)

    def test_invalid_confirmation_without_displayed_review_never_infers_checkout(self):
        result = self.ai.respond('Yes, confirm my order.')
        self.assertFalse(result['ok'])
        self.assertIn('request and read an order review', result['response'])
        self.client.chat.assert_not_called()
        self.client.classify_request.assert_not_called()
        self.assertEqual(orders.check_stock(1, db_path=self.db)['stock_qty'], 30)
        self.assertEqual(self.agent.state, STATE_TAKING_ORDER)

    def test_valid_confirmation_wrong_menu_then_checkout_and_repeat_are_safe(self):
        self.route('Can I review my order?', 'request_review', tool('request_review'))
        self.client.chat.reset_mock()
        self.client.chat.side_effect = [tool('browse_menu'), tool('confirm_order', confirmed=True)]
        result = self.ai.respond('Yes, confirm my order.')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.agent.state, STATE_CONFIRMED)
        self.assertEqual(orders.check_stock(1, db_path=self.db)['stock_qty'], 28)
        self.assertEqual([t['call']['name'] for t in result['tools']], ['confirm_order'])
        self.assertIn('Saved receipt:', result['response'])
        self.client.chat.reset_mock()
        repeated = self.ai.respond('Yes, confirm my order.')
        self.assertTrue(repeated['ok'])
        self.assertFalse(repeated['order_mutation_succeeded'])
        self.assertFalse(repeated['inference_verified'])
        self.assertIn('Saved receipt:', repeated['response'])
        self.client.chat.assert_not_called()
        self.assertEqual(orders.check_stock(1, db_path=self.db)['stock_qty'], 28)

    def test_model_classification_cannot_grant_confirmation_consent(self):
        self.route('Please display my cart for approval.', 'request_review', tool('request_review'))
        result = self.route('What is my total?', 'confirm_order', tool('confirm_order', confirmed=True), tool('confirm_order', confirmed=True))
        self.assertFalse(result['ok'])
        self.assertEqual(self.agent.state, STATE_REVIEW)
        self.assertEqual(orders.check_stock(1, db_path=self.db)['stock_qty'], 30)

    def test_varied_language_uses_model_classification_across_sizes(self):
        cases = [('How much is the order so far?', 'view_current_order'),
                 ('Magkano lahat ng inorder natin?', 'view_current_order'),
                 ('Total natin please?', 'view_current_order'),
                 ('Please show the order for my approval.', 'request_review'),
                 ('Patingin po ng order bago ko kumpirmahin.', 'request_review'),
                 ('Review muna natin yung order, please.', 'request_review')]
        for model in ('qwen2.5:3b', 'qwen2.5:7b', 'qwen2.5:14b'):
            for text, intent in cases:
                with self.subTest(model=model, text=text):
                    self.client.model = model
                    self.client.chat.reset_mock()
                    result = self.route(text, intent, tool(intent))
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(result['tools'][0]['call']['name'], intent)
                    self.assertEqual(self.client.chat.call_count, 1)  # No acknowledgement-only inference.

    def test_classifier_failures_retry_bounded_before_any_action(self):
        self.client.classify_request.side_effect = [MalformedResponse('invalid routing'), 'view_current_order']
        self.client.chat.side_effect = [tool('view_current_order')]
        self.assertTrue(self.ai.respond('Magkano na total?')['ok'])
        self.assertEqual(self.client.classify_request.call_count, 2)
        self.assertIn('invalid routing', self.client.classify_request.call_args.kwargs['feedback'])

    def test_classifier_timeout_and_invalid_values_never_dispatch(self):
        for outputs in ([OllamaError('timeout')], ['invented', 'invented']):
            with self.subTest(outputs=outputs), patch('bistro.ai_engine.dispatch', wraps=dispatch) as execute:
                self.client.classify_request.side_effect = outputs
                result = self.ai.respond('Show me what I owe.')
                self.assertFalse(result['ok'])
                execute.assert_not_called()
                self.assertEqual(self.agent.context()['order']['items'][0]['quantity'], 2)
        self.client.chat.assert_not_called()

    def test_ambiguous_classification_only_allows_clarification(self):
        reply = {'content': json.dumps({'reply': 'Which portion would you like changed?', 'facts': [],
                                       'pending_question': 'Which portion would you like changed?'})}
        result = self.route('Do that one maybe?', 'clarify', reply)
        self.assertFalse(result['ok'])
        self.assertEqual(result['tools'], [])
        self.assertEqual(self.client.chat.call_args.args[1], [])

    def test_structured_route_excludes_unrelated_tools_too(self):
        self.ai.structured = True
        result = self.route('Total please?', 'view_current_order', {'content': json.dumps({
            'calls': [{'name': 'view_current_order', 'arguments': {}}]})})
        self.assertTrue(result['ok'])
        messages = self.client.chat.call_args.args[0]
        schema = next(m for m in messages if 'Only these current tools' in m.get('content', ''))
        tools = json.loads(schema['content'].split('Schemas: ', 1)[1])
        self.assertEqual([t['function']['name'] for t in tools], ['view_current_order'])

    def test_real_client_method_uses_one_classification_and_one_action_request(self):
        client = OllamaClient(model='qwen2.5:3b')
        client.chat = Mock(side_effect=[{'content': '{"intent":"view_current_order"}'}, tool('view_current_order')])
        ai = AIWaiter(agent=self.agent, client=client)
        result = ai.respond('Magkano na total?')
        self.assertTrue(result['ok'], result)
        self.assertEqual(client.chat.call_count, 2)
        self.assertTrue(client.chat.call_args_list[0].kwargs['structured'])
        self.assertEqual(len(client.chat.call_args_list[0].args), 1)
        self.assertEqual([t['function']['name'] for t in client.chat.call_args_list[1].args[1]], ['view_current_order'])
        self.assertNotIn('session_id', json.dumps(client.chat.call_args_list[0].args[0]))


class ClassifierProtocolTests(unittest.TestCase):
    def test_same_selected_client_structured_classification_has_no_tool_execution(self):
        client = OllamaClient(model='qwen2.5:14b')
        client.chat = Mock(return_value={'content': '{"intent":"request_review"}'})
        self.assertEqual(client.classify_request('Let me approve the cart.', {'state': STATE_TAKING_ORDER}), 'request_review')
        self.assertTrue(client.chat.call_args.kwargs['structured'])
        self.assertEqual(len(client.chat.call_args.args), 1)  # No action tools offered.
        self.assertIn('Let me approve the cart.', client.chat.call_args.args[0][1]['content'])
        for message in ({'content': '{"intent":"execute_sql"}'}, {'content': '{"intent":"request_review","confirmed":true}'},
                        {'content': '{"intent":"request_review"}', 'tool_calls': [{'function': {'name': 'confirm_order', 'arguments': {}}}]}):
            client.chat.return_value = message
            with self.assertRaises(MalformedResponse):
                client.classify_request('Anything', {})


if __name__ == '__main__':
    unittest.main()
