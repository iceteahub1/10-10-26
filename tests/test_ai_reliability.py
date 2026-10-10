"""Mocked model outputs with real disposable SQLite; no real Qwen claims."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch, MagicMock
from bistro.ai_engine import AIWaiter
from bistro.ai_protocol import INTENT_TOOLS, parse_response, customer_table
from bistro.database import initialize_database, connection
from bistro.ollama_client import OllamaClient, OllamaError, MalformedResponse, ToolsUnsupported
from bistro import order_service as orders
from bistro.tool_schemas import dispatch
from fsm_agent import ConversationAgent, STATE_REVIEW, STATE_CONFIRMED

def intent(name, **args):
    return {'name': name, 'arguments': args}

def native(*calls):
    return {'role': 'assistant', 'content': '', 'tool_calls': [{'function': call} for call in calls]}

def final():
    return {'role':'assistant', 'content':'{"reply":"Thank you!"}'}

class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name)/'restaurant.db'
        initialize_database(self.db)
        self.agent = ConversationAgent(db_path=self.db)
        self.agent.execute({'action':'establish_session','arguments':{'table_no':3}})
        self.client = Mock(model='qwen2.5:arbitrary-compatible-tag')
        self.ai = AIWaiter(agent=self.agent, client=self.client)

    def respond(self, text, *outputs):
        self.client.chat.side_effect = outputs
        return self.ai.respond(text)

    def add(self, **extra):
        return intent('add_item_to_order', item_name='Chicken Adobo', qty=2, **extra)

    def cart(self):
        return self.agent.context()['order']

    def test_reported_fake_session_then_invalid_response_across_models(self):
        for model in ('qwen2.5:3b','qwen2.5:7b','qwen2.5:14b'):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as folder:
                db = Path(folder)/'test.db'
                initialize_database(db)
                agent = ConversationAgent(db_path=db)
                agent.execute({'action':'establish_session','arguments':{'table_no':3}})
                client = Mock(model=model)
                client.chat.side_effect = [native(self.add(session_id='made-up-session')), {'content':'not JSON'}, MalformedResponse('empty')]
                result = AIWaiter(agent=agent, client=client).respond('Dalawang Chicken Adobo po.')
                self.assertTrue(result['ok'])  # Completed action needs no model acknowledgement.
                self.assertFalse(result['partial_success'])
                self.assertTrue(result['order_mutation_succeeded'])
                self.assertIn('2 × Chicken Adobo', result['response'])
                self.assertIn('Total: PHP 370.00.', result['response'])
                self.assertEqual(result['source'], 'application_results')
                self.assertEqual(agent.context()['order']['items'][0]['quantity'],2)
                self.assertNotEqual(agent.session_id,'made-up-session')
                with connection(db) as con:
                    self.assertEqual(con.execute('SELECT COUNT(*) FROM table_sessions').fetchone()[0],1)
                self.assertEqual(client.chat.call_count,1)

    def test_protected_identifiers_absent_from_advertised_intents(self):
        for tool in INTENT_TOOLS:
            properties = tool['function']['parameters']['properties']
            self.assertFalse({'session_id','review_token','item_id','line_id'} & properties.keys())
            self.assertFalse(tool['function']['parameters']['additionalProperties'])

    def test_native_json_string_arguments_and_integer_quantity(self):
        call = {'name':'add_item_to_order','arguments':json.dumps({'item_name':'Chicken Adobo','qty':'2'})}
        result = self.respond('Dalawang Chicken Adobo po.', native(call), final())
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.cart()['items'][0]['quantity'],2)
        self.assertIn('Total: PHP 370.00.',result['response'])

    def test_supported_structured_shapes(self):
        shapes = [json.dumps({'calls':[self.add()]}), '```json\n'+json.dumps({'calls':[self.add()]})+'\n```',
                  json.dumps(self.add()), json.dumps([self.add()]), json.dumps({'tool_calls':[{'function':self.add()}]})]
        for shape in shapes:
            result = self.respond('add two',{'content':shape},final())
            self.assertTrue(result['ok'],result)
        self.assertEqual(self.cart()['items'][0]['quantity'],10)

    def test_unsupported_tools_fallback_uses_intent_schemas(self):
        result = self.respond('add two',ToolsUnsupported('not supported'),{'content':json.dumps({'calls':[self.add()]})},final())
        self.assertTrue(result['ok'])
        self.assertTrue(self.ai.structured)
        second = self.client.chat.call_args_list[1]
        self.assertTrue(second.kwargs['structured'])
        self.assertIsNone(second.args[1])
        self.assertIn('item_name',json.dumps(second.args[0]))

    def test_saved_structured_transport_has_schemas_on_first_request(self):
        self.ai.structured = True
        observed=[]
        def output(messages,*args,**kwargs):
            observed.append(json.loads(json.dumps(messages)))
            return {'content':json.dumps({'calls':[self.add()]})} if len(observed)==1 else final()
        self.client.chat.side_effect=output
        self.assertTrue(self.ai.respond('add two')['ok'])
        self.assertTrue(any('Schemas:' in m.get('content','') for m in observed[0]))

    def test_empty_and_malformed_retry_then_recovery_no_mutation_replay(self):
        result = self.respond('add two',{},native(self.add()),{'content':'```json\n{"reply":"Thank you!"}\n```'})
        self.assertTrue(result['ok'])
        self.assertEqual(self.cart()['items'][0]['quantity'],2)
        self.assertEqual(self.client.chat.call_count,2)

    def test_all_malformed_message_shapes_fail_bounded_without_crash(self):
        malformed = [None,[],{'content':'[]'}, {'content':'{"calls":[]}'}, {'tool_calls':[None]},
                     {'tool_calls':[{'function':None}]}, {'tool_calls':[{'function':{'name':'browse_menu','arguments':[]}}]},
                     {'content':'{"calls":[{"name":"browse_menu","arguments":{}}],"price":1}'},
                     {'content':'{"calls":[{"name":"browse_menu","arguments":{},"name":"cancel_order"}]}'},
                     {'content':'{"calls":[{"name":"add_item_to_order","arguments":{"item_name":"Chicken Adobo","qty":NaN}}]}'}]
        for bad in malformed:
            with self.subTest(bad=bad):
                self.client.chat.reset_mock()
                result = self.respond('hello',bad,bad)
                self.assertFalse(result['ok'])
                self.assertEqual(self.client.chat.call_count,2)
                self.assertEqual(self.cart()['items'],[])

    def test_price_inventory_state_and_unknown_tool_cannot_be_injected(self):
        for extra in ({'price':1},{'stock_qty':999},{'state':'STATE_CONFIRMED'},{'receipt_id':'forged'}):
            bad=native(self.add(**extra))
            self.assertFalse(self.respond('add',bad,bad)['ok'])
        bad=native(intent('execute_sql',sql='DELETE FROM menu_items'))
        self.assertFalse(self.respond('add',bad,bad)['ok'])
        self.assertEqual(self.cart()['items'],[])

    def test_session_fabrication_cannot_cross_customer_boundary(self):
        other=ConversationAgent(db_path=self.db)
        other.execute({'action':'establish_session','arguments':{'table_no':4}})
        result=self.respond('add two',native(self.add(session_id=other.session_id)),final())
        self.assertTrue(result['ok'])
        self.assertEqual(other.context()['order']['items'],[])
        self.assertEqual(self.cart()['items'][0]['quantity'],2)
        # Trusted direct dispatcher keeps its original strict isolation contract.
        self.assertFalse(dispatch(other,result['tools'][0]['call'])['ok'])

    def test_item_names_resolve_read_tools_and_cart_portions(self):
        result=self.respond('check soy and stock',native(intent('check_item_allergen',item_name='Chicken Adobo',allergen='soy'),intent('check_stock',item_name='Chicken Adobo')),final())
        self.assertTrue(result['ok'],result)
        self.respond('add two',native(self.add()),final())
        result=self.respond('one please',native(intent('update_quantity',item_name='Chicken Adobo',qty=1)),final())
        self.assertTrue(result['ok'])
        self.assertEqual(self.cart()['items'][0]['quantity'],1)
        result=self.respond('remove it',native(intent('remove_item',item_name='Chicken Adobo')),final())
        self.assertTrue(result['ok'])
        self.assertEqual(self.cart()['items'],[])

    def test_ambiguous_portions_and_forged_db_ids_do_not_guess(self):
        self.respond('add two',native(self.add()),final())
        self.respond('add customized two',native(self.add(instructions='less salt')),final())
        bad=native(intent('remove_item',item_name='Chicken Adobo'))
        result=self.respond('remove one',bad,bad)
        self.assertFalse(result['ok'])
        self.assertIn('Which exact cart portion',result['response'])
        self.assertEqual(len(self.cart()['items']),2)
        bad=native(intent('remove_item',line_id=9999))
        self.assertFalse(self.respond('remove',bad,bad)['ok'])
        result=self.respond('remove less salt',native(intent('remove_item',item_name='Chicken Adobo',current_instructions='less salt')),final())
        self.assertTrue(result['ok'])
        self.assertEqual(len(self.cart()['items']),1)

    def test_no_table_prompts_without_inference_or_fake_session(self):
        unbound=AIWaiter(agent=ConversationAgent(db_path=self.db),client=self.client)
        result=unbound.respond('Dalawang Chicken Adobo po.')
        self.assertTrue(result['ok'])
        self.assertIn('table number',result['response'])
        self.assertIsNone(unbound.agent.session_id)
        self.client.chat.assert_not_called()
        self.client.chat.side_effect=[native(self.add(session_id='fake')),final()]
        result=unbound.respond('Table 8 po.')
        self.assertTrue(result['ok'],result)
        self.assertEqual(unbound.agent.context()['table_no'],8)
        self.assertEqual(unbound.agent.context()['order']['items'][0]['quantity'],2)
        self.assertIn('Dalawang Chicken Adobo',json.dumps(unbound._context()['history']))
        self.assertIn('Dalawang Chicken Adobo', next(m['content'] for m in self.client.chat.call_args_list[0].args[0] if m['role']=='user'))
        self.assertEqual(self.client.chat.call_count,1)

    def test_table_reply_numeric_and_quantity_never_guessed_as_table(self):
        self.assertIsNone(customer_table('two Chicken Adobo'))
        self.assertIsNone(customer_table('2'))
        self.assertEqual(customer_table('2',awaiting=True),2)
        self.assertEqual(customer_table('mesa siyam'),9)
        for text in ('table 0','table 3 or table 4',"I'm not at table 3"):
            with self.assertRaises(ValueError):
                customer_table(text)

    def test_model_cannot_switch_bound_table(self):
        bad=native(intent('establish_session',table_no=44,session_id='madeup'))
        result=self.respond('hello',bad,bad)
        self.assertFalse(result['ok'])
        self.assertEqual(self.agent.context()['table_no'],3)

    def test_existing_open_table_resolved_by_python(self):
        unbound=AIWaiter(agent=ConversationAgent(db_path=self.db),client=self.client)
        self.client.chat.side_effect=[final()]
        result=unbound.respond('Table 3')
        self.assertTrue(result['ok'])
        self.assertEqual(unbound.agent.session_id,self.agent.session_id)

    def test_closed_session_new_explicit_table_keeps_existing_fsm_feature(self):
        previous=self.agent.session_id
        self.assertTrue(dispatch(self.agent,intent('cancel_order'))['ok'])
        result=self.respond('Table 4 po.',native(self.add()),final())
        self.assertTrue(result['ok'])
        self.assertNotEqual(self.agent.session_id,previous)
        self.assertEqual(self.agent.context()['table_no'],4)
        self.assertEqual(self.cart()['items'][0]['quantity'],2)

    def test_invalid_table_can_be_clarified_without_guessing(self):
        unbound=AIWaiter(agent=ConversationAgent(db_path=self.db),client=self.client)
        result=unbound.respond('table 8 or table 9')
        self.assertFalse(result['ok'])
        self.assertIsNone(unbound.agent.session_id)
        self.client.chat.assert_not_called()
        self.client.chat.side_effect=[final()]
        self.assertTrue(unbound.respond('9')['ok'])
        self.assertEqual(unbound.agent.context()['table_no'],9)

    def test_repeated_calls_with_changing_fake_ids_not_replayed(self):
        result=self.respond('Add two Chicken Adobo and show the menu',native(self.add(session_id='fakeA')),native(self.add(session_id='fakeB')))
        self.assertFalse(result['ok'])
        self.assertTrue(result['partial_success'])
        self.assertEqual(self.cart()['items'][0]['quantity'],2)

    def test_duplicate_same_batch_and_invalid_batch_have_no_effects(self):
        for bad in (native(self.add(),self.add()),native(self.add(),intent('shell',command='boom'))):
            result=self.respond('add',bad,bad)
            self.assertFalse(result['ok'])
            self.assertEqual(self.cart()['items'],[])

    def test_saved_action_then_timeout_is_reported_once(self):
        result=self.respond('Add two Chicken Adobo and show the menu',native(self.add()),OllamaError('model timed out'))
        self.assertFalse(result['ok'])
        self.assertTrue(result['partial_success'])
        self.assertIn('2 × Chicken Adobo',result['response'])
        self.assertIn('timed out',result['response'])
        self.assertEqual(self.client.chat.call_count,2)
        self.assertEqual(self.cart()['items'][0]['quantity'],2)

    def test_success_then_rejected_tool_reports_both(self):
        result=self.respond('add but too much',native(self.add(),intent('add_item_to_order',item_name='Chicken Adobo',qty=9999)),final())
        self.assertFalse(result['ok'])
        self.assertTrue(result['partial_success'])
        self.assertIn('Total: PHP 370.00.',result['response'])
        self.assertEqual(self.cart()['items'][0]['quantity'],2)

    def test_review_and_customer_consent_use_python_token(self):
        self.respond('add',native(self.add()),final())
        self.respond('review please',native(intent('request_review')),final())
        self.assertEqual(self.agent.state,STATE_REVIEW)
        result=self.respond('Yes, confirm my order.',native(intent('confirm_order',confirmed=True,review_token='fabricated')),final())
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.agent.state,STATE_CONFIRMED)
        self.assertIn('Saved receipt:',result['response'])
        self.assertEqual(orders.check_stock(1,db_path=self.db)['stock_qty'],28)

    def test_confirmation_intent_without_token_still_needs_direct_consent(self):
        self.respond('add',native(self.add()),final())
        self.respond('review',native(intent('request_review')),final())
        result=self.respond('what is the total?',native(intent('confirm_order',confirmed=True)),final())
        self.assertFalse(result['ok'])
        self.assertEqual(self.agent.state,STATE_REVIEW)
        self.assertEqual(orders.check_stock(1,db_path=self.db)['stock_qty'],30)

    def test_same_turn_review_or_edit_cannot_authorize_checkout(self):
        self.respond('add',native(self.add()),final())
        result=self.respond('Yes, confirm my order.',native(intent('request_review'),intent('confirm_order',confirmed=True)),final())
        self.assertFalse(result['ok'])
        self.assertEqual(result['tools'], [])
        self.assertIsNone(self.agent.context()['review'])
        self.assertEqual(self.cart()['items'][0]['quantity'],2)
        self.respond('review',native(intent('request_review')),final())
        result=self.respond('Yes, confirm my order.',native(self.add(),intent('confirm_order',confirmed=True)),final())
        self.assertFalse(result['ok'])
        self.assertEqual(result['tools'], [])
        self.assertEqual(self.agent.state,STATE_REVIEW)
        self.assertEqual(self.cart()['items'][0]['quantity'],2)
        self.assertTrue(self.respond('Add two Chicken Adobo',native(self.add()))['ok'])
        self.assertIsNone(self.agent.context()['review'])
        self.assertFalse(self.respond('Yes, confirm my order.',native(intent('confirm_order',confirmed=True)),final())['ok'])
        self.assertEqual(self.cart()['items'][0]['quantity'],4)
        self.assertEqual(orders.check_stock(1,db_path=self.db)['stock_qty'],30)

    def test_boolean_or_fractional_quantity_never_coerced(self):
        for qty in (True,2.5,'2.0'):
            bad=native(intent('add_item_to_order',item_name='Chicken Adobo',qty=qty))
            self.assertFalse(self.respond('add',bad,bad)['ok'])
        self.assertEqual(self.cart()['items'],[])

    def test_normalized_history_never_returns_fake_ids_or_argument_strings(self):
        captured=[]
        def output(messages,*args,**kwargs):
            captured.append(json.loads(json.dumps(messages)))
            if len(captured)==1:
                return native({'name':'add_item_to_order','arguments':json.dumps({'item_name':'Chicken Adobo','qty':2,'session_id':'fake'})})
            return native(intent('browse_menu'))
        self.client.chat.side_effect=output
        self.assertTrue(self.ai.respond('Add two Chicken Adobo and show the menu')['ok'])
        history=next(m for m in captured[1] if m['role']=='assistant')
        args=history['tool_calls'][0]['function']['arguments']
        self.assertIsInstance(args,dict)
        self.assertNotIn('session_id',args)
        self.assertNotIn('review_token',json.dumps(captured[1]))

    def test_feedback_read_failure_does_not_hide_saved_action_or_escape(self):
        with patch.object(self.ai,'_action_feedback',side_effect=OSError('unreadable')):
            result=self.respond('add',native(self.add()),OllamaError('timed out'))
        self.assertFalse(result['ok'])
        self.assertTrue(result['partial_success'])
        self.assertIn('Order updated',result['response'])
        self.assertEqual(self.cart()['items'][0]['quantity'],2)

    def test_repeat_review_in_confirmation_turn_invalidates_turn_permission(self):
        self.respond('add',native(self.add()),final())
        self.respond('review',native(intent('request_review')),final())
        result=self.respond('Yes, confirm my order.',native(intent('request_review'),intent('confirm_order',confirmed=True)),final())
        self.assertFalse(result['ok'])
        self.assertEqual(self.agent.state,STATE_REVIEW)
        self.assertEqual(orders.check_stock(1,db_path=self.db)['stock_qty'],30)

class TransportVariationTests(unittest.TestCase):
    def test_duplicate_native_argument_keys_rejected_at_http_boundary(self):
        client=OllamaClient(timeout=1)
        client.opener=MagicMock()
        client.opener.open.return_value.__enter__.return_value.read.return_value=b'{"done":true,"message":{"role":"assistant","tool_calls":[{"function":{"name":"add_item_to_order","arguments":{"qty":1,"qty":2}}}]}}'
        with self.assertRaises(MalformedResponse):
            client.chat([])
    def test_native_nullable_content_and_serialized_arguments(self):
        client=OllamaClient(model='qwen2.5:14b',timeout=1)
        client.opener=MagicMock()
        response=client.opener.open.return_value.__enter__.return_value
        response.read.return_value=json.dumps({'done':True,'message':{'role':'assistant','content':None,'tool_calls':[{'function':{'name':'browse_menu','arguments':'{}'}}]}}).encode()
        msg=client.chat([],INTENT_TOOLS)
        self.assertEqual(parse_response(msg)[0],[intent('browse_menu')])
        self.assertEqual(json.loads(client.opener.open.call_args.args[0].data)['model'],'qwen2.5:14b')

    def test_nullable_tools_on_nonempty_final_response(self):
        client=OllamaClient(timeout=1)
        client.opener=MagicMock()
        client.opener.open.return_value.__enter__.return_value.read.return_value=json.dumps({'done':True,'message':{'role':'assistant','content':'{"reply":"Thank you!"}','tool_calls':None}}).encode()
        self.assertEqual(client.chat([])['tool_calls'],[])

class AcceptanceScriptTests(unittest.TestCase):
    def test_windows_acceptance_flow_with_mock_model_and_real_disposable_sqlite(self):
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        import verify_phase4
        client=Mock(model='qwen2.5:14b')
        client.chat.side_effect=[native(intent('add_item_to_order',item_name='Chicken Adobo',qty=2)),
                                 native(intent('view_current_order')),
                                 native(intent('request_review')),
                                 native(intent('view_current_order')),
                                 native(intent('confirm_order',confirmed=True))]
        with patch.object(verify_phase4,'OllamaClient',return_value=client) as create, patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(verify_phase4.main(['--model','qwen2.5:14b','--transport','structured']),0)
        self.assertEqual(create.call_args.args[1],'qwen2.5:14b')
        self.assertIn('transport=structured',output.getvalue())
        self.assertIn('incomplete turns=0',output.getvalue())
        self.assertEqual(client.chat.call_count,5)  # No acknowledgement-only requests.

    def test_acceptance_rejects_read_then_empty_instead_of_pending_addition(self):
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        import verify_phase4
        client=Mock(model='qwen2.5:3b')
        client.chat.side_effect=[native(intent('view_current_order')), {'content':''}, {'content':''}]
        with patch.object(verify_phase4,'OllamaClient',return_value=client), patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(verify_phase4.main(['--model','qwen2.5:3b']),1)
        self.assertIn('was NOT added',output.getvalue())
        self.assertIn('Customer request incomplete',output.getvalue())
        self.assertNotIn('PASS: Real',output.getvalue())

if __name__=='__main__':
    unittest.main()
