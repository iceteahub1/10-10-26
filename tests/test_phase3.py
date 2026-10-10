"""Phase 3 functional tests: temporary SQLite only, no model prerequisites."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bistro.database import connection, initialize_database
from bistro import order_service as orders
from bistro.session_store import create_session
from fsm_agent import (ConversationAgent, STATE_GREETING, STATE_TAKING_ORDER,
                       STATE_REVIEW, STATE_CONFIRMED, STATE_CANCELLED)
from virtual_waiter_prototype import main as cli


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='phase3 space ')
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'restaurant.db'
        initialize_database(self.db)
        self.agent = ConversationAgent(db_path=self.db)

    def act(self, name, agent=None, **args):
        return (agent or self.agent).execute({'action': name, 'arguments': args})

    def start(self, table=3, agent=None, **args):
        result = self.act('establish_session', agent, table_no=table, **args)
        self.assertTrue(result['ok'], result)
        return result['session_id']

    def add(self, **args):
        result = self.act('add_item', item_name='Chicken Adobo', qty=2, **args)
        self.assertTrue(result['ok'], result)
        return result['data']['items'][0]['line_id']

    def review(self):
        result = self.act('request_review')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['state'], STATE_REVIEW)
        return result

    def stock(self):
        return orders.check_stock(1, db_path=self.db)['stock_qty']

    def test_valid_transition_and_persisted_receipt(self):
        self.assertEqual(self.agent.state, STATE_GREETING)
        self.start(); self.add(); self.review()
        result = self.act('confirm_order', confirmed=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['state'], STATE_CONFIRMED)
        self.assertEqual(result['data']['total'], '370.00')
        self.assertEqual(self.stock(), 28)
        self.assertEqual([t['to_state'] for t in self.agent.context()['transitions']],
                         [STATE_TAKING_ORDER, STATE_REVIEW, STATE_CONFIRMED])

    def test_invalid_state_transitions(self):
        for action, args in [('request_review', {}), ('confirm_order', {'confirmed': True}), ('cancel_order', {})]:
            self.assertFalse(self.act(action, **args)['ok'])
        sid = self.start()
        self.assertFalse(self.act('return_to_order')['ok'])
        self.add()
        self.assertFalse(self.act('confirm_order', confirmed=True)['ok'])
        with connection(self.db, write=True) as con:
            with self.assertRaises(ValueError):
                self.agent._transition(con, sid, STATE_GREETING)
        self.assertEqual(self.agent.state, STATE_TAKING_ORDER)
        self.assertEqual(self.stock(), 30)

    def test_table_validation(self):
        for table in (None, True, '3', 0, -1, 1.5, 2**100):
            with self.subTest(table=table):
                self.assertFalse(self.act('establish_session', table_no=table)['ok'])
        with connection(self.db) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM table_sessions').fetchone()[0], 0)

    def test_session_resume_and_same_agent_resumption(self):
        sid = self.start(); self.add()
        self.assertEqual(self.start(), sid)
        other = ConversationAgent(db_path=self.db)
        self.assertEqual(self.start(agent=other), sid)
        self.assertEqual(other.context()['order']['total'], '370.00')

    def test_memory_restart_fresh_process(self):
        sid = self.start(); self.add(instructions='less salt'); self.review()
        code = '''import json,sys
from fsm_agent import ConversationAgent
a=ConversationAgent(db_path=sys.argv[1])
a.execute({'action':'establish_session','arguments':{'table_no':3,'session_id':sys.argv[2]}})
print(json.dumps(a.context()))'''
        result = subprocess.run([sys.executable, '-c', code, str(self.db), sid], cwd=ROOT,
                                text=True, capture_output=True, check=True)
        context = json.loads(result.stdout)
        self.assertEqual(context['state'], STATE_REVIEW)
        self.assertEqual(context['order']['items'][0]['special_instructions'], 'less salt')
        self.assertGreaterEqual(len(context['messages']), 8)
        self.assertIn('pending_question', context['context'])

    def test_independent_tables_and_session_id_misuse(self):
        sid = self.start(); self.add()
        other = ConversationAgent(db_path=self.db)
        self.assertFalse(self.act('establish_session', other, table_no=4, session_id=sid)['ok'])
        self.assertFalse(self.act('establish_session', other, table_no=3, session_id='missing')['ok'])
        self.start(table=4, agent=other)
        self.assertEqual(other.context()['order']['items'], [])
        self.assertNotIn('Chicken Adobo', json.dumps(other.context()['messages']))
        self.assertFalse(self.act('view_order', session_id=other.session_id)['ok'])
        self.assertEqual(self.agent.session_id, sid)

    def test_menu_availability_and_allergen_context(self):
        self.start()
        self.assertEqual(len(self.act('browse_menu', category='Mains')['data']), 4)
        self.assertTrue(self.act('check_availability', item_id=1)['data']['available'])
        self.assertTrue(self.act('check_allergens', item_id=3, allergen='peanut')['data'])
        self.assertEqual(self.agent.context()['context']['last_menu_question']['action'], 'check_allergens')
        self.assertFalse(self.act('check_availability', item_id=999)['ok'])

    def test_ambiguous_reference_persisted_and_resolved(self):
        self.start()
        result = self.act('add_item', item_name='Rice', qty=1)
        self.assertFalse(result['ok'])
        self.assertIn('Which exact', result['message'])
        other = ConversationAgent(db_path=self.db)
        self.start(agent=other)
        self.assertIn('unresolved_action', other.context()['context'])
        self.assertTrue(self.act('add_item', other, item_name='Garlic Rice', qty=1)['ok'])
        self.assertNotIn('unresolved_action', other.context()['context'])

    def test_cart_edit_review_invalidation_and_removal(self):
        self.start(); line = self.add(); self.review()
        self.assertTrue(self.act('update_quantity', line_id=line, qty=3)['ok'])
        self.assertEqual(self.agent.state, STATE_TAKING_ORDER)
        self.assertIsNone(self.agent.context()['review'])
        self.assertFalse(self.act('confirm_order', confirmed=True)['ok'])
        self.assertTrue(self.act('update_instructions', line_id=line, instructions='no sauce')['ok'])
        self.assertEqual(self.agent.context()['order']['items'][0]['special_instructions'], 'no sauce')
        self.assertTrue(self.act('remove_item', line_id=line)['ok'])
        self.assertEqual(self.agent.context()['order']['items'], [])
        self.assertFalse(self.act('remove_item', line_id=line)['ok'])

    def test_return_from_review(self):
        self.start(); self.add(); self.review()
        result = self.act('return_to_order')
        self.assertTrue(result['ok'])
        self.assertEqual(result['state'], STATE_TAKING_ORDER)
        self.assertIsNone(self.agent.context()['review'])

    def test_external_cart_write_invalidates_review(self):
        sid = self.start(); line = self.add(); self.review()
        orders.change_quantity(sid, line, 1, db_path=self.db)
        self.assertEqual(self.agent.state, STATE_TAKING_ORDER)
        self.assertFalse(self.act('confirm_order', confirmed=True)['ok'])
        self.assertIsNone(self.agent.context()['review'])

    def test_price_change_requires_new_review(self):
        self.start(); self.add(); self.review()
        with connection(self.db, write=True) as con:
            con.execute('UPDATE menu_items SET price=200 WHERE id=1')
        result = self.act('confirm_order', confirmed=True)
        self.assertTrue(result['data']['review_invalidated'])
        self.assertEqual(result['state'], STATE_TAKING_ORDER)
        self.assertEqual(self.stock(), 30)
        self.review()
        self.assertEqual(self.act('confirm_order', confirmed=True)['data']['total'], '400.00')

    def test_explicit_confirmation_only(self):
        self.start(); self.add(); self.review()
        for confirmed in ('yes', 'true', 1, False, None, 'unrelated message'):
            self.assertFalse(self.act('confirm_order', confirmed=confirmed)['ok'])
        self.assertFalse(self.agent.execute({'action':'confirm_order','arguments':{}})['ok'])
        self.assertEqual(self.stock(), 30)
        self.assertEqual(self.agent.state, STATE_REVIEW)

    def test_empty_cart_cannot_review_or_confirm(self):
        self.start()
        self.assertFalse(self.act('request_review')['ok'])
        self.assertFalse(self.act('confirm_order', confirmed=True)['ok'])
        self.assertEqual(self.stock(), 30)

    def test_cancelled_session_preserved_no_deduction(self):
        sid = self.start(); self.add(); self.review()
        self.assertEqual(self.act('cancel_order')['state'], STATE_CANCELLED)
        self.assertEqual(self.stock(), 30)
        self.assertFalse(self.act('confirm_order', confirmed=True)['ok'])
        self.assertFalse(self.act('add_item', item_name='Chicken Adobo', qty=1)['ok'])
        with self.assertRaises(sqlite3.IntegrityError):
            orders.checkout(sid, db_path=self.db)
        self.assertEqual(self.stock(), 30)
        other = ConversationAgent(db_path=self.db)
        self.start(agent=other, session_id=sid)
        self.assertEqual(other.state, STATE_CANCELLED)
        self.assertIn('cancelled', other.context()['context'])
        self.assertTrue(self.act('new_session', table_no=3)['ok'])
        self.assertNotEqual(self.agent.session_id, sid)
        self.assertEqual(self.agent.context()['order']['items'], [])

    def test_repeated_confirmation_and_immutability(self):
        self.start(); line = self.add(); self.review()
        receipt = self.act('confirm_order', confirmed=True)['data']
        self.assertEqual(self.act('confirm_order', confirmed=True)['data'], receipt)
        self.assertEqual(self.act('view_order')['data'], receipt)
        for name, args in [('update_quantity', {'line_id':line,'qty':3}), ('remove_item', {'line_id':line}),
                           ('cancel_order', {}), ('request_review', {})]:
            self.assertFalse(self.act(name, **args)['ok'])
        self.assertEqual(self.stock(), 28)
        self.assertTrue(self.act('new_session', table_no=3)['ok'])

    def test_malformed_actions_no_side_effects(self):
        self.start()
        for action in (None, [], {}, 'hello', {'action': [], 'arguments': {}},
                       {'action':'missing','arguments':{}}, {'action':'view_order','arguments':[]},
                       {'action':'view_order','arguments':{},'extra':1},
                       {'action':'add_item','arguments':{'item_name':'Chicken Adobo','qty':float('nan')}}):
            with self.subTest(action=action):
                self.assertFalse(self.agent.execute(action)['ok'])
        for qty in (True, '1', 0, -1, 1.5, 10001):
            self.assertFalse(self.act('add_item', item_name='Chicken Adobo', qty=qty)['ok'])
        self.assertFalse(self.act('add_item', item_name='Invented food', qty=1)['ok'])
        self.assertFalse(self.act('add_item', item_name='Chicken Adobo', qty=31)['ok'])
        self.assertEqual(self.agent.context()['order']['items'], [])
        self.assertEqual(self.stock(), 30)

    def test_wrong_session_line_and_active_customer_switch(self):
        self.start(); line = self.add()
        other = ConversationAgent(db_path=self.db)
        self.start(table=4, agent=other)
        self.assertFalse(self.act('remove_item', other, line_id=line)['ok'])
        self.assertFalse(self.act('new_session', table_no=3)['ok'])
        self.assertFalse(self.act('establish_session', table_no=4)['ok'])

    def test_competing_agents_confirm_only_once(self):
        sid = self.start(); self.add(); self.review()
        other = ConversationAgent(db_path=self.db)
        self.start(agent=other, session_id=sid)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda agent: self.act('confirm_order', agent, confirmed=True), [self.agent,other]))
        self.assertTrue(all(r['ok'] for r in results))
        self.assertEqual(results[0]['data'], results[1]['data'])
        self.assertEqual(self.stock(), 28)

    def test_action_and_transcript_rollback_together(self):
        self.start()
        with connection(self.db, write=True) as con:
            con.execute("CREATE TRIGGER fail_history BEFORE INSERT ON conversation_messages BEGIN SELECT RAISE(ABORT,'test'); END")
        self.assertFalse(self.act('add_item', item_name='Chicken Adobo', qty=1)['ok'])
        self.assertEqual(self.agent.context()['order']['items'], [])

    def test_checkout_failure_preserves_review_cart_and_inventory(self):
        self.start(); self.add(); self.review()
        with connection(self.db, write=True) as con:
            con.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON receipt_line_items BEGIN SELECT RAISE(ABORT,'test'); END")
        self.assertFalse(self.act('confirm_order', confirmed=True)['ok'])
        self.assertEqual(self.agent.state, STATE_REVIEW)
        self.assertEqual(self.stock(), 30)
        with connection(self.db) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM receipts').fetchone()[0], 0)

    def test_phase2_finalized_session_resumes_receipt(self):
        create_session(9, 'old', db_path=self.db)
        orders.add_item('old', 'Chicken Adobo', 1, db_path=self.db)
        receipt = orders.checkout('old', db_path=self.db)
        self.start(table=9, session_id='old')
        self.assertEqual(self.agent.state, STATE_CONFIRMED)
        self.assertEqual(self.act('confirm_order', confirmed=True)['data'], receipt)

    def test_cli_restart_and_help(self):
        lines = iter(['help', 'bad json',
            '{"action":"establish_session","arguments":{"table_no":3}}',
            '{"action":"add_item","arguments":{"item_name":"Chicken Adobo","qty":1}}', 'quit'])
        output = []
        self.assertEqual(cli(['--db', str(self.db)], get_input=lambda _: next(lines), send_output=output.append), 0)
        self.assertTrue(any('valid JSON' in line for line in output))
        output = []
        lines = iter(['{"action":"establish_session","arguments":{"table_no":3}}', 'state',
                      '{"action":"request_review","arguments":{}}',
                      '{"action":"confirm_order","arguments":{"confirmed":true}}', 'quit'])
        self.assertEqual(cli(['--db', str(self.db)], get_input=lambda _: next(lines), send_output=output.append), 0)
        self.assertTrue(any('STATE_CONFIRMED' in line for line in output))
        self.assertEqual(self.stock(), 29)

    def test_keyboard_interrupt_preserves_session(self):
        self.start(); self.add()
        self.assertEqual(cli(['--db', str(self.db)], get_input=lambda _: (_ for _ in ()).throw(KeyboardInterrupt), send_output=lambda _: None), 0)
        other = ConversationAgent(db_path=self.db)
        self.start(agent=other)
        self.assertEqual(other.context()['order']['total'], '370.00')

    def test_versioned_migration_preserves_phase2_records_and_repeats(self):
        old = Path(self.temp.name) / 'v1.db'
        with sqlite3.connect(old) as con:
            con.executescript((ROOT / 'init_restaurant_db.sql').read_text())
        create_session(2, 'old-open', db_path=old)
        create_session(3, 'old-receipt', db_path=old)
        orders.add_item('old-open','Chicken Adobo',1,db_path=old)
        orders.add_item('old-receipt','Chicken Adobo',2,db_path=old)
        receipt = orders.checkout('old-receipt',db_path=old)
        initialize_database(old); initialize_database(old)
        self.assertEqual(orders.get_receipt('old-receipt',db_path=old), receipt)
        self.assertEqual(orders.view_cart('old-open',db_path=old)['total'], '185.00')
        self.assertEqual(orders.check_stock(1,db_path=old)['stock_qty'], 28)
        with connection(old) as con:
            self.assertEqual(con.execute("SELECT value FROM schema_metadata WHERE key='conversation_schema_version'").fetchone()[0], '1')
            self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_unknown_migration_version_preserves_data(self):
        self.start(); self.add()
        with connection(self.db, write=True) as con:
            con.execute("UPDATE schema_metadata SET value='99' WHERE key='conversation_schema_version'")
        with self.assertRaises(RuntimeError):
            initialize_database(self.db)
        self.assertEqual(self.agent.context()['order']['total'], '370.00')

    def test_migration_failure_rolls_back_extension(self):
        root = Path(self.temp.name) / 'broken-root'
        (root / 'migrations').mkdir(parents=True)
        (root / 'migrations' / '001_conversation_memory.sql').write_text('CREATE TABLE partial(id INTEGER);\nINVALID SQL;\n')
        old = Path(self.temp.name) / 'broken.db'
        with sqlite3.connect(old) as con:
            con.executescript((ROOT / 'init_restaurant_db.sql').read_text())
        with patch('bistro.database.ROOT', root), self.assertRaises(sqlite3.OperationalError):
            initialize_database(old)
        with connection(old) as con:
            self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='partial'").fetchone())
            self.assertEqual(con.execute('SELECT count(*) FROM menu_items').fetchone()[0], 12)

    def test_nested_transactions_reject_different_database(self):
        with connection(self.db, write=True):
            with self.assertRaises(RuntimeError):
                with connection(Path(self.temp.name) / 'other.db'):
                    pass


if __name__ == '__main__':
    unittest.main()
