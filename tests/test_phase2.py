"""Phase 2 tests use only temporary SQLite databases."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from bistro.database import connection, initialize_database
from bistro.session_store import create_session, get_session
from bistro import order_service as orders
import db_tools
import setup_environment as setup


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bistro database space ')
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'data' / 'restaurant.db'
        initialize_database(self.db)
        create_session(1, 's1', db_path=self.db)

    def sql(self, statement, args=()):
        with connection(self.db, write=True) as con:
            return con.execute(statement, args).fetchall()

    def add(self, name='Chicken Adobo', qty=1, instructions='', session='s1'):
        return db_tools.add_item_to_order(session, name, qty, instructions, db_path=self.db)

    def stock(self, item=1):
        return orders.check_stock(item, db_path=self.db)['stock_qty']

    def test_required_schema_and_seed(self):
        expected = {'menu_items': {'id','name','category','price','stock_qty','allergens'},
                    'table_sessions': {'session_id','table_no','status'},
                    'order_line_items': {'session_id','item_id','quantity','special_instructions'}}
        for table, columns in expected.items():
            self.assertTrue(columns <= {r['name'] for r in self.sql(f'PRAGMA table_info({table})')})
        self.assertEqual(len(orders.browse_menu(db_path=self.db)), 12)
        self.assertEqual(self.sql('PRAGMA user_version')[0][0], 1)
        self.assertEqual(self.sql('PRAGMA foreign_key_check'), [])

    def test_reinitialization_preserves_all_data_and_does_not_reseed(self):
        self.add(qty=2)
        receipt = orders.checkout('s1', db_path=self.db)
        self.sql('DELETE FROM menu_items WHERE id=12')
        self.sql("UPDATE menu_items SET price=190 WHERE id=1")
        initialize_database(self.db)
        initialize_database(self.db)
        self.assertEqual(self.stock(), 28)
        self.assertEqual(len(orders.browse_menu(db_path=self.db)), 11)
        self.assertEqual(orders.get_receipt('s1', db_path=self.db), receipt)
        self.assertEqual(get_session('s1', db_path=self.db)['status'], 'checked_out')
        self.assertEqual(db_tools.get_menu_by_category('mains', db_path=self.db)[0]['price'], '190.00')

    def test_initialization_atomic_on_bad_sql(self):
        bad = Path(self.temp.name) / 'bad-schema'
        bad.mkdir()
        (bad / 'init_restaurant_db.sql').write_text('CREATE TABLE partial(id INTEGER);\nINVALID SQL;\n')
        target = Path(self.temp.name) / 'bad.db'
        with patch('bistro.database.ROOT', bad), self.assertRaises(sqlite3.OperationalError):
            initialize_database(target)
        with sqlite3.connect(target) as con:
            self.assertEqual(con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])

    def test_reject_unknown_database_without_data_loss(self):
        target = Path(self.temp.name) / 'legacy.db'
        with sqlite3.connect(target) as con:
            con.execute('CREATE TABLE evidence(value TEXT)')
            con.execute("INSERT INTO evidence VALUES ('keep me')")
        with self.assertRaises(RuntimeError):
            initialize_database(target)
        with sqlite3.connect(target) as con:
            self.assertEqual(con.execute('SELECT value FROM evidence').fetchone()[0], 'keep me')
        self.sql('PRAGMA user_version=999')
        with self.assertRaises(RuntimeError):
            initialize_database(self.db)

    def test_concurrent_initialization(self):
        target = Path(self.temp.name) / 'concurrent.db'
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: initialize_database(target), range(4)))
        self.assertEqual(len(orders.browse_menu(db_path=target)), 12)

    def test_schema_constraints_and_foreign_keys(self):
        for statement in ("UPDATE menu_items SET stock_qty=-1 WHERE id=1",
                          "UPDATE menu_items SET stock_qty=1.5 WHERE id=1",
                          "UPDATE menu_items SET price=1.234 WHERE id=1",
                          "UPDATE menu_items SET price='invalid' WHERE id=1",
                          "INSERT INTO order_line_items(session_id,item_id,quantity) VALUES ('missing',1,1)",
                          "INSERT INTO order_line_items(session_id,item_id,quantity) VALUES ('s1',1,0)"):
            with self.subTest(sql=statement), self.assertRaises(sqlite3.IntegrityError):
                self.sql(statement)

    def test_menu_category_and_allergen_queries(self):
        self.assertEqual(len(db_tools.get_menu_by_category(' MAINS ', db_path=self.db)), 4)
        self.assertEqual(db_tools.get_menu_by_category("Mains' OR 1=1 --", db_path=self.db), [])
        self.assertTrue(db_tools.check_item_allergen(3, ' PEANUT ', db_path=self.db))
        self.assertFalse(db_tools.check_item_allergen(3, 'pea', db_path=self.db))
        self.assertFalse(db_tools.check_item_allergen(6, 'milk', db_path=self.db))
        with self.assertRaises(orders.OrderError):
            db_tools.check_item_allergen(999, 'milk', db_path=self.db)

    def test_session_persistence_and_validation(self):
        self.assertEqual(create_session(1, 's1', db_path=self.db), get_session('s1', db_path=self.db))
        for table in (0, -1, True, '1'):
            with self.assertRaises(ValueError):
                create_session(table, db_path=self.db)
        with self.assertRaises(ValueError):
            create_session(2, 's1', db_path=self.db)
        with self.assertRaises(ValueError):
            create_session(1, '', db_path=self.db)
        with self.assertRaises(ValueError):
            get_session('missing', db_path=self.db)
        result = subprocess.run(
            [sys.executable, '-c',
             'from bistro.session_store import get_session; import sys; '
             'print(get_session("s1", db_path=sys.argv[1])["table_no"])', str(self.db)],
            cwd=ROOT, text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip(), '1')

    def test_invalid_names_quantities_and_instructions(self):
        for name in ('Rice', 'nonexistent', "%' OR 1=1 --", ''):
            with self.subTest(name=name), self.assertRaises(orders.OrderError):
                self.add(name=name)
        for quantity in (0, -1, True, 1.5, '2', None, 10001):
            with self.subTest(qty=quantity), self.assertRaises(orders.OrderError):
                self.add(qty=quantity)
        for instructions in (None, 'x' * 501):
            with self.assertRaises(orders.OrderError):
                self.add(instructions=instructions)
        with self.assertRaises(orders.OrderError):
            self.add(session='missing')
        self.assertEqual(orders.view_cart('s1', db_path=self.db)['items'], [])

    def test_cart_add_modify_customize_remove_and_exact_totals(self):
        cart = self.add(qty=2, instructions='no sauce')
        line = cart['items'][0]['line_id']
        self.assertEqual(self.add(qty=1, instructions='no sauce')['items'][0]['quantity'], 3)
        self.add(name='Garlic Rice', qty=2)
        cart = orders.change_quantity('s1', line, 2, db_path=self.db)
        self.assertEqual(cart['subtotal_cents'], 46000)
        cart = orders.customize_item('s1', line, 'less salt', db_path=self.db)
        self.assertEqual(cart['items'][0]['special_instructions'], 'less salt')
        self.assertEqual(db_tools.view_current_order('s1', db_path=self.db), cart)
        cart = orders.view_cart('s1', db_path=self.db, tax_rate=Decimal('.12'), service_rate='.10')
        self.assertEqual((cart['subtotal'], cart['tax'], cart['service'], cart['total']), ('460.00','55.20','46.00','561.20'))
        self.assertEqual(self.stock(), 30)  # Cart does not reserve/deduct inventory.
        self.assertEqual(orders.remove_item('s1', line, db_path=self.db)['total'], '90.00')

    def test_customization_variants_aggregate_stock_and_ownership(self):
        first = self.add(qty=20)['items'][0]['line_id']
        cart = self.add(qty=10, instructions='no sauce')
        second = cart['items'][1]['line_id']
        with self.assertRaises(orders.OrderError):
            self.add(instructions='extra sauce')
        with self.assertRaises(orders.OrderError):
            orders.change_quantity('s1', second, 11, db_path=self.db)
        with self.assertRaises(orders.OrderError):
            orders.customize_item('s1', second, '', db_path=self.db)
        create_session(2, 's2', db_path=self.db)
        for action in (lambda: orders.remove_item('s2', first, db_path=self.db),
                       lambda: orders.change_quantity('s2', first, 2, db_path=self.db),
                       lambda: orders.customize_item('s2', first, 'x', db_path=self.db)):
            with self.assertRaises(orders.OrderError):
                action()
        self.assertEqual(len(orders.view_cart('s1', db_path=self.db)['items']), 2)

    def test_unavailable_and_out_of_stock(self):
        self.sql('UPDATE menu_items SET available=0 WHERE id=1')
        with self.assertRaises(orders.OrderError):
            self.add()
        self.assertFalse(orders.check_stock(1, db_path=self.db)['available'])
        self.sql('UPDATE menu_items SET available=1, stock_qty=0 WHERE id=1')
        with self.assertRaises(orders.OrderError):
            self.add()
        with self.assertRaises(orders.OrderError):
            orders.check_stock(999, db_path=self.db)

    def test_empty_checkout_and_invalid_rates(self):
        with self.assertRaises(orders.OrderError):
            orders.checkout('s1', db_path=self.db)
        self.add()
        for rate in ('NaN', 'Infinity', '-.1', '1.01', 'invalid'):
            with self.assertRaises(orders.OrderError):
                orders.checkout('s1', db_path=self.db, tax_rate=rate)
        self.assertEqual(self.stock(), 30)
        self.assertEqual(get_session('s1', db_path=self.db)['status'], 'open')

    def test_checkout_current_prices_snapshots_and_duplicate(self):
        self.add(qty=2, instructions='less salt')
        self.sql('UPDATE menu_items SET price=190.25 WHERE id=1')
        receipt = orders.checkout('s1', db_path=self.db, tax_rate='.12', service_rate='.10')
        self.assertEqual(receipt['subtotal_cents'], 38050)
        self.assertEqual(receipt['total_cents'], 46421)
        self.assertEqual(receipt['items'][0]['unit_price'], '190.25')
        self.assertEqual(receipt['items'][0]['special_instructions'], 'less salt')
        self.assertEqual(self.stock(), 28)
        self.sql("UPDATE menu_items SET price=999, name='Changed' WHERE id=1")
        self.assertEqual(orders.checkout('s1', db_path=self.db), receipt)
        self.assertEqual(orders.view_cart('s1', db_path=self.db), receipt)
        self.assertEqual(self.stock(), 28)
        with self.assertRaises(orders.OrderError):
            self.add(name='Changed')
        with self.assertRaises(orders.OrderError):
            orders.remove_item('s1', receipt['items'][0]['line_id'], db_path=self.db)

    def test_stock_recheck_rolls_back_earlier_deduction(self):
        self.add(qty=2)
        self.add(name='Garlic Rice', qty=3)
        self.sql('UPDATE menu_items SET stock_qty=1 WHERE id=6')
        with self.assertRaises(orders.OrderError):
            orders.checkout('s1', db_path=self.db)
        self.assertEqual(self.stock(), 30)
        self.assertEqual(self.stock(6), 1)
        self.assertEqual(self.sql('SELECT * FROM receipts'), [])
        self.assertEqual(get_session('s1', db_path=self.db)['status'], 'open')
        self.assertEqual(len(orders.view_cart('s1', db_path=self.db)['items']), 2)

    def test_receipt_failure_rolls_back_stock_and_session(self):
        self.add(qty=2)
        self.sql("CREATE TRIGGER fail_receipt BEFORE INSERT ON receipt_line_items BEGIN SELECT RAISE(ABORT, 'forced receipt failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            orders.checkout('s1', db_path=self.db)
        self.assertEqual(self.stock(), 30)
        self.assertEqual(self.sql('SELECT * FROM receipts'), [])
        self.assertEqual(get_session('s1', db_path=self.db)['status'], 'open')
        self.sql('DROP TRIGGER fail_receipt')
        self.assertEqual(orders.checkout('s1', db_path=self.db)['total'], '370.00')

    def test_concurrent_duplicate_checkout(self):
        self.add(qty=2)
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(lambda _: orders.checkout('s1', db_path=self.db), range(4)))
        self.assertTrue(all(r == receipts[0] for r in receipts))
        self.assertEqual(self.stock(), 28)
        self.assertEqual(len(self.sql('SELECT * FROM receipts')), 1)

    def test_competing_sessions_cannot_oversell(self):
        create_session(2, 's2', db_path=self.db)
        self.add(qty=20)
        self.add(qty=20, session='s2')
        def attempt(session):
            try:
                return orders.checkout(session, db_path=self.db)
            except orders.OrderError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ['s1','s2']))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(self.stock(), 10)
        self.assertEqual(len(self.sql('SELECT * FROM receipts')), 1)

    def test_unavailable_after_add_and_fractional_peso_rounding(self):
        self.add()
        self.sql('UPDATE menu_items SET available=0 WHERE id=1')
        with self.assertRaises(orders.OrderError):
            orders.checkout('s1', db_path=self.db)
        self.sql('UPDATE menu_items SET available=1, price=1.05 WHERE id=1')
        receipt = orders.checkout('s1', db_path=self.db, tax_rate='.10')
        self.assertEqual(receipt['tax_cents'], 11)
        self.assertEqual(receipt['total'], '1.16')

    def test_setup_initializes_temporary_database_and_preserves_on_repeat(self):
        root = Path(self.temp.name) / 'fresh setup'
        root.mkdir()
        self.db = root / 'data' / 'restaurant.db'
        (root / 'requirements.txt').write_text('')
        marker = root / 'runtime' / 'setup.json'
        marker.parent.mkdir()
        with patch.object(setup, 'ROOT', root), patch.object(setup, 'MARKER', marker), \
             patch.object(setup, 'require_venv'), patch.object(setup.subprocess, 'run'), \
             patch.object(setup, 'ensure_ollama', return_value=({'models':[{'name':'qwen2.5:7b'}]}, None)), \
             patch('bistro.paths.prepare_directories'), patch('sys.argv', ['setup','--inside']):
            self.assertEqual(setup.main(), 0)
            create_session(1, 's1', db_path=self.db)
            self.add(qty=2)
            orders.checkout('s1', db_path=self.db)
            self.assertEqual(setup.main(), 0)
        self.assertTrue(marker.exists())
        self.assertEqual(self.stock(), 28)

    def test_project_relative_path_from_other_working_directory(self):
        previous = Path.cwd()
        try:
            os.chdir(self.temp.name)
            with patch('bistro.database.DATABASE', self.db):
                self.assertEqual(len(db_tools.get_menu_by_category('Drinks')), 3)
                self.assertEqual(db_tools.view_current_order('s1')['total'], '0.00')
        finally:
            os.chdir(previous)


if __name__ == '__main__':
    unittest.main()
