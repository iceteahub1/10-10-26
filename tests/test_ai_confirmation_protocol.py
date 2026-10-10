"""Mock Ollama responses; checkout uses disposable real SQLite/FSM."""
import json
import unittest
from unittest.mock import MagicMock, patch
from tests import test_ai_semantic_routing as fixtures

tool = fixtures.tool
from bistro.ai_protocol import action_response_schema
from bistro.ollama_client import OllamaClient, ToolsUnsupported
from bistro.tool_schemas import dispatch
from bistro import order_service as orders
from fsm_agent import STATE_REVIEW, STATE_TAKING_ORDER


class ConfirmationProtocolTests(unittest.TestCase):
    setUp = fixtures.SemanticRoutingTests.setUp
    route = fixtures.SemanticRoutingTests.route

    def review(self):
        self.route('Please review my order.', 'request_review', tool('request_review'))
        self.client.chat.reset_mock()

    def test_reported_invalid_envelope_retries_with_constrained_schema(self):
        self.review()
        malformed = {'content': '', 'tool_calls': [{'function': {'name': 'confirm_order', 'parameters': {'confirmed': True}}}]}
        self.client.chat.side_effect = [malformed, {'content': json.dumps({'calls': [{'name': 'confirm_order', 'arguments': {'confirmed': True}}]})}]
        result = self.ai.respond('Yes, confirm my order.')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.client.chat.call_count, 2)
        schema = self.client.chat.call_args.kwargs['response_schema']
        call = schema['anyOf'][0]['properties']['calls']['items']['anyOf'][0]
        self.assertEqual(call['properties']['name']['enum'], ['confirm_order'])
        self.assertEqual(call['properties']['arguments']['required'], ['confirmed'])
        self.assertNotIn('review_token', call['properties']['arguments']['properties'])
        self.assertEqual(orders.check_stock(1, db_path=self.db)['stock_qty'], 28)
        self.assertIn('Saved receipt:', result['response'])
        repeated = self.ai.respond('Yes, confirm my order.')
        self.assertTrue(repeated['ok'])
        self.assertFalse(repeated['order_mutation_succeeded'])
        self.assertEqual(self.client.chat.call_count, 2)

    def test_malformed_responses_never_confirm(self):
        self.review()
        for content in ('', '{', '{"confirmed":true}', '{"name":"confirm_order","parameters":{"confirmed":true}}', '{"calls":[{"name":"confirm_order","arguments":{"confirmed":"true"}}]}'):
            with self.subTest(content=content), patch('bistro.ai_engine.dispatch', wraps=dispatch) as execute:
                self.client.chat.side_effect = [{'content': content}, {'content': content}]
                result = self.ai.respond('Yes, confirm my order.')
                self.assertFalse(result['ok'])
                self.assertEqual(result['tools'], [])
                self.assertFalse(result['order_mutation_succeeded'])
                execute.assert_not_called()
                self.assertEqual(self.agent.state, STATE_REVIEW)
                self.assertIsNone(self.ai.snapshot()['receipt'])
                self.assertEqual(orders.check_stock(1, db_path=self.db)['stock_qty'], 30)

    def test_ambiguous_consent_cannot_be_granted_by_model(self):
        self.review()
        result = self.route('Maybe, is everything ready?', 'confirm_order', tool('confirm_order', confirmed=True), tool('confirm_order', confirmed=True))
        self.assertFalse(result['ok'])
        self.assertEqual(self.agent.state, STATE_REVIEW)
        self.assertEqual(orders.check_stock(1, db_path=self.db)['stock_qty'], 30)

    def test_missing_review_never_infers(self):
        result = self.ai.respond('Yes, confirm my order.')
        self.assertFalse(result['ok'])
        self.client.chat.assert_not_called()

    def test_stale_review_is_not_reported_as_checkout_success(self):
        self.review()
        cart = self.agent.context()['order']
        orders.customize_item(self.agent.session_id, cart['items'][0]['line_id'], 'Less salt', db_path=self.db)
        self.client.chat.side_effect = [tool('confirm_order', confirmed=True)]
        result = self.ai.respond('Yes, confirm my order.')
        self.assertFalse(result['ok'], result)
        self.assertFalse(result['order_mutation_succeeded'])
        self.assertEqual(self.agent.state, STATE_TAKING_ORDER)
        self.assertIsNone(self.ai.snapshot()['receipt'])
        self.assertEqual(orders.check_stock(1, db_path=self.db)['stock_qty'], 30)

    def test_native_unavailable_uses_same_structured_confirmation(self):
        self.review()
        self.client.chat.side_effect = [ToolsUnsupported('unsupported'), {'content': '{"calls":[{"name":"confirm_order","arguments":{"confirmed":true}}]}'}]
        result = self.ai.respond('Yes, confirm my order.')
        self.assertTrue(result['ok'], result)
        self.assertIn('response_schema', self.client.chat.call_args.kwargs)
        self.assertEqual(len(result['tools']), 1)

    def test_client_sends_schema_as_ollama_format(self):
        schema = action_response_schema([])
        client = OllamaClient(model='qwen2.5:14b')
        client.opener = MagicMock()
        client.opener.open.return_value.__enter__.return_value.read.return_value = b'{"done":true,"message":{"role":"assistant","content":"{}"}}'
        client.chat([], structured=True, response_schema=schema)
        payload = json.loads(client.opener.open.call_args.args[0].data)
        self.assertEqual(payload['format'], schema)
        self.assertNotIn('tools', payload)
