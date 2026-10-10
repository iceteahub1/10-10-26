"""Mock Qwen selection; real disposable SQLite, FSM and strict dispatcher."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bistro.ai_engine import AIWaiter
from bistro.ai_request import RequestProgress
from bistro.ai_tool_policy import available_tools, LINE_EDITS, READS
from bistro.database import initialize_database, connection
from bistro.tool_schemas import dispatch
from fsm_agent import ConversationAgent, STATE_REVIEW


def native(name, **args):
    return {'content': '', 'tool_calls': [{'function': {'name': name, 'arguments': args}}]}


def structured(name, **args):
    return {'content': json.dumps({'calls': [{'name': name, 'arguments': args}]})}


def names(tools):
    return {tool['function']['name'] for tool in tools}


class ToolPolicyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name) / 'test.db'
        initialize_database(self.db)
        self.agent = ConversationAgent(db_path=self.db)
        self.client = Mock(model='qwen2.5:any-compatible-size')
        self.ai = AIWaiter(agent=self.agent, client=self.client)

    def bind(self):
        self.agent.execute({'action': 'establish_session', 'arguments': {'table_no': 3}})

    def add(self, item='Chicken Adobo', qty=2):
        self.assertTrue(dispatch(self.agent, {'name': 'add_item_to_order', 'arguments': {
            'session_id': self.agent.session_id, 'item_name': item, 'qty': qty, 'instructions': ''}})['ok'])

    def tools(self, progress=None, consent=False, token=None):
        return available_tools(self.agent.context(), progress or RequestProgress(None),
                               confirmation_allowed=consent, review_token=token)

    def test_exact_windows_update_selection_corrected_before_identity_binding(self):
        first = self.ai.respond('Dalawang Chicken Adobo po.')
        self.assertIn('table number', first['response'])
        self.client.chat.assert_not_called()
        self.client.chat.side_effect = [native('update_quantity', item_name='Chicken Adobo', qty=2),
            structured('add_item_to_order', item_name='Chicken Adobo', qty=2)]
        with patch('bistro.ai_engine.dispatch', wraps=dispatch) as execute:
            result = self.ai.respond('Table 3 po.')
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['order_mutation_succeeded'])
        self.assertFalse(result['partial_success'])
        self.assertEqual(self.agent.context()['order']['items'][0]['quantity'], 2)
        self.assertIn('Total: PHP 370.00.', result['response'])
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.args[1]['name'], 'add_item_to_order')
        self.assertEqual(self.client.chat.call_count, 2)  # No final acknowledgement call.
        first_tools = self.client.chat.call_args_list[0].args[1]
        self.assertEqual(first_tools[0]['function']['name'], 'add_item_to_order')
        self.assertEqual(names(first_tools), READS | {'add_item_to_order'})
        retry = self.client.chat.call_args_list[1]
        self.assertTrue(retry.kwargs['structured'])
        schema_message = next(m for m in retry.args[0] if 'Only these current tools' in m.get('content', ''))
        retry_tools = json.loads(schema_message['content'].split('Schemas: ', 1)[1])
        self.assertEqual(names(retry_tools), names(first_tools))
        feedback = '\n'.join(m.get('content', '') for m in retry.args[0])
        self.assertIn('unavailable', feedback)
        self.assertNotIn('Which exact cart portion', feedback)

    def test_repeated_inapplicable_edit_exhausts_retry_without_dispatch(self):
        self.ai.respond('Dalawang Chicken Adobo po.')
        bad = native('update_quantity', item_name='Chicken Adobo', qty=2)
        self.client.chat.side_effect = [bad, bad]
        with patch('bistro.ai_engine.dispatch', wraps=dispatch) as execute:
            result = self.ai.respond('Table 3 po.')
        execute.assert_not_called()
        self.assertFalse(result['ok'])
        self.assertFalse(result['partial_success'])
        self.assertFalse(result['order_mutation_succeeded'])
        self.assertEqual(result['tools'], [])
        self.assertEqual(self.agent.context()['order']['items'], [])
        self.assertIn('was NOT added', result['response'])
        self.assertIn('unavailable', result['response'])
        self.assertEqual(self.client.chat.call_count, 2)

    def test_wrong_update_recovery_for_other_pending_item_across_model_labels(self):
        for table, label in enumerate(('qwen2.5:3b', 'qwen2.5:7b', 'qwen2.5:14b'), start=4):
            with self.subTest(model=label):
                agent = ConversationAgent(db_path=self.db)
                client = Mock(model=label)
                ai = AIWaiter(agent=agent, client=client)
                ai.respond('Isang Garlic Rice po.')
                client.chat.assert_not_called()
                client.chat.side_effect = [native('update_quantity', item_name='Garlic Rice', qty=1),
                    structured('add_item_to_order', item_name='Garlic Rice', qty=1)]
                result = ai.respond(f'Table {table} po.')
                self.assertTrue(result['ok'], result)
                self.assertEqual(agent.context()['order']['items'][0]['name'], 'Garlic Rice')
                self.assertEqual(agent.context()['order']['items'][0]['quantity'], 1)
                self.assertEqual(client.chat.call_count, 2)

    def test_catalog_recomputed_after_partial_completion_without_stale_schemas(self):
        self.bind()
        self.client.chat.side_effect = [native('add_item_to_order', item_name='Chicken Adobo', qty=2),
                                       native('browse_menu')]
        result = self.ai.respond('Two Chicken Adobo and show the menu')
        self.assertTrue(result['ok'], result)
        self.assertIn('add_item_to_order', names(self.client.chat.call_args_list[0].args[1]))
        second = self.client.chat.call_args_list[1]
        self.assertEqual(names(second.args[1]), READS)
        schemas = [m for m in second.args[0] if 'Schemas: ' in m.get('content', '')]
        self.assertEqual(len(schemas), 1)
        self.assertEqual(names(json.loads(schemas[0]['content'].split('Schemas: ', 1)[1])), READS)

    def test_empty_cart_never_advertises_line_edits_or_checkout(self):
        self.bind()
        tools = self.tools()
        self.assertFalse(names(tools) & LINE_EDITS)
        self.assertFalse(names(tools) & {'request_review', 'confirm_order', 'establish_session'})
        self.assertTrue(READS <= names(tools))
        self.assertEqual(tools[0]['function']['name'], 'add_item_to_order')

    def test_existing_cart_edit_affordances_return_for_unspecified_intent(self):
        self.bind()
        self.add()
        self.assertTrue(LINE_EDITS <= names(self.tools()))
        self.assertIn('request_review', names(self.tools()))
        self.assertNotIn('confirm_order', names(self.tools()))

    def test_clearly_requested_addition_excludes_edits_even_with_existing_cart(self):
        self.bind()
        self.add()
        progress = RequestProgress([{'name': 'add_item_to_order', 'arguments': {
            'item_name': 'Garlic Rice', 'qty': 1, 'instructions': ''}}])
        tools = self.tools(progress)
        self.assertEqual(names(tools), READS | {'add_item_to_order'})
        self.assertEqual(tools[0]['function']['name'], 'add_item_to_order')

    def test_known_information_request_has_no_mutation_affordances(self):
        self.bind()
        self.add()
        progress = RequestProgress([{'name': 'browse_menu', 'arguments': {}}])
        self.assertEqual(names(self.tools(progress)), READS)

    def test_review_tools_require_cart_prior_review_and_direct_consent(self):
        self.bind()
        self.assertNotIn('confirm_order', names(self.tools(consent=True, token='fake')))
        self.add()
        self.assertNotIn('confirm_order', names(self.tools(consent=True, token='fake')))
        self.assertTrue(dispatch(self.agent, {'name': 'request_review', 'arguments': {}})['ok'])
        token = self.ai.snapshot()['review_token']
        self.assertEqual(self.agent.state, STATE_REVIEW)
        self.assertNotIn('confirm_order', names(self.tools(token=token)))
        self.assertNotIn('confirm_order', names(self.tools(consent=True)))
        self.assertIn('confirm_order', names(self.tools(consent=True, token=token)))
        self.assertEqual(names(self.tools(consent=True, token=token)), READS | {'confirm_order'})

    def test_closed_cart_exposes_only_receipt_view(self):
        self.bind()
        dispatch(self.agent, {'name': 'cancel_order', 'arguments': {}})
        self.assertEqual(names(self.tools()), {'view_current_order'})

    def test_structured_initial_request_has_the_same_restricted_schema(self):
        self.bind()
        self.ai.structured = True
        self.client.chat.side_effect = [structured('add_item_to_order', item_name='Garlic Rice', qty=1)]
        result = self.ai.respond('One Garlic Rice please.')
        self.assertTrue(result['ok'])
        request = self.client.chat.call_args
        self.assertIsNone(request.args[1])
        schema = next(m for m in request.args[0] if 'Only these current tools' in m.get('content', ''))
        self.assertEqual(names(json.loads(schema['content'].split('Schemas: ', 1)[1])), READS | {'add_item_to_order'})

    def test_varied_language_and_menu_items_still_require_qwen_proposed_arguments(self):
        # These are controlled interpretations, not measurements of model quality.
        cases = [("I'd like two Pork Sisig.", 'Pork Sisig', 2),
                 ('Isang Garlic Rice po.', 'Garlic Rice', 1),
                 ('Pakidagdag po ng tatlong Lumpiang Shanghai.', 'Lumpiang Shanghai', 3),
                 ('Can we get a Calamansi Juice, please?', 'Calamansi Juice', 1),
                 ('Order tayo ng dalawang Grilled Bangus, please.', 'Grilled Bangus', 2),
                 ('Pahingi po ng isang Leche Flan.', 'Leche Flan', 1)]
        self.bind()
        for label in ('qwen2.5:3b', 'qwen2.5:7b', 'qwen2.5:14b'):
            for text, item, qty in cases:
                with self.subTest(model=label, text=text):
                    self.client.model = label
                    self.client.chat.reset_mock()
                    self.client.chat.side_effect = [native('add_item_to_order', item_name=item, qty=qty)]
                    result = self.ai.respond(text)
                    self.assertTrue(result['ok'], result)
                    self.assertTrue(result['order_mutation_succeeded'])
                    self.assertEqual(result['tools'][0]['call']['arguments']['item_name'], item)
                    self.assertEqual(result['tools'][0]['call']['arguments']['qty'], qty)
                    self.assertEqual(self.client.chat.call_count, 1)
        items = {line['name']: line['quantity'] for line in self.agent.context()['order']['items']}
        self.assertEqual(items, {item: qty * 3 for _, item, qty in cases})

    def test_new_database_menu_item_needs_no_phrase_or_item_code_change(self):
        with connection(self.db, write=True) as con:
            con.execute('INSERT INTO menu_items(name,category,price,stock_qty,allergens) VALUES(?,?,?,?,?)',
                        ('Seasonal Tofu', 'Mains', 120, 10, 'soy'))
        self.bind()
        self.client.chat.side_effect = [native('add_item_to_order', item_name='Seasonal Tofu', qty=2, instructions='less salt')]
        result = self.ai.respond('Pwede pong mag-order ng tofu special ninyo? Two servings, less salt sana.')
        self.assertTrue(result['ok'], result)
        line = self.agent.context()['order']['items'][0]
        self.assertEqual((line['name'], line['quantity'], line['special_instructions']), ('Seasonal Tofu', 2, 'less salt'))

    def test_ambiguous_retained_request_does_not_invoke_or_dispatch_a_model(self):
        self.ai.respond('Maybe some rice, unless it contains my allergen.')
        with patch('bistro.ai_engine.dispatch', wraps=dispatch) as execute:
            result = self.ai.respond('Table 3 po.')
        execute.assert_not_called()
        self.client.chat.assert_not_called()
        self.assertIn('restate', result['response'])
        self.assertEqual(self.agent.context()['order']['items'], [])

    def test_invalid_mixed_batch_is_blocked_before_any_addition(self):
        self.bind()
        bad = native('update_quantity', item_name='Chicken Adobo', qty=2)
        bad['tool_calls'].append(native('add_item_to_order', item_name='Chicken Adobo', qty=2)['tool_calls'][0])
        self.client.chat.side_effect = [bad, bad]
        with patch('bistro.ai_engine.dispatch', wraps=dispatch) as execute:
            result = self.ai.respond('Two Chicken Adobo please.')
        execute.assert_not_called()
        self.assertFalse(result['ok'])
        self.assertEqual(self.agent.context()['order']['items'], [])

    def test_unknown_wording_wrong_edit_then_read_cannot_claim_order_completion(self):
        self.bind()
        self.client.chat.side_effect = [native('update_quantity', item_name='Pork Sisig', qty=2),
                                       native('view_current_order')]
        result = self.ai.respond('Pakidagdag po ng dalawang Pork Sisig.')
        self.assertFalse(result['ok'])
        self.assertFalse(result['order_mutation_succeeded'])
        self.assertFalse(result['partial_success'])
        self.assertIn('No requested order mutation was executed', result['response'])
        self.assertEqual(self.agent.context()['order']['items'], [])
        self.assertEqual(self.client.chat.call_count, 2)

    def test_wanting_customization_is_not_assumed_to_be_an_addition(self):
        self.bind()
        self.add()
        self.client.chat.side_effect = [native('update_instructions', item_name='Chicken Adobo', instructions='less salt')]
        result = self.ai.respond('I want less salt in my Chicken Adobo.')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.agent.context()['order']['items'][0]['quantity'], 2)
        self.assertEqual(self.agent.context()['order']['items'][0]['special_instructions'], 'less salt')
        self.assertEqual(self.client.chat.call_count, 1)

    def test_allergen_and_stock_lookups_remain_available_before_addition(self):
        self.bind()
        self.client.chat.side_effect = [native('check_item_allergen', item_name='Pork Sisig', allergen='egg'),
            native('check_stock', item_name='Pork Sisig'), native('add_item_to_order', item_name='Pork Sisig', qty=1)]
        result = self.ai.respond('One Pork Sisig please.')
        self.assertTrue(result['ok'], result)
        self.assertEqual([t['call']['name'] for t in result['tools']], ['check_item_allergen', 'check_stock', 'add_item_to_order'])
        self.assertEqual(self.client.chat.call_count, 3)
        self.assertEqual(self.agent.context()['order']['items'][0]['quantity'], 1)

    def test_general_acceptance_script_with_bound_taglish_and_other_item(self):
        import io
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
        import verify_phase4
        self.client.chat.side_effect = [native('add_item_to_order', item_name='Garlic Rice', qty=1),
            native('view_current_order'), native('request_review'), native('view_current_order'),
            native('confirm_order', confirmed=True)]
        with patch.object(verify_phase4, 'OllamaClient', return_value=self.client), patch('sys.stdout', new_callable=io.StringIO) as output:
            result = verify_phase4.main(['--model', 'qwen2.5:3b', '--bound', '--item', 'Garlic Rice',
                '--qty', '1', '--order-text', 'Pahingi po ng isang Garlic Rice, please.'])
        self.assertEqual(result, 0, output.getvalue())
        self.assertEqual(self.client.chat.call_count, 5)


if __name__ == '__main__':
    unittest.main()
