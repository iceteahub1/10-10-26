"""SQLite connections and atomic, non-destructive schema initialization."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from bistro.paths import DATABASE, ROOT


@contextmanager
def connection(db_path=None, *, write=False):
    """Open an existing database; writes take the SQLite reserved writer lock."""
    path = Path(db_path) if db_path is not None else DATABASE
    con = sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True,
                          timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    try:
        con.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def initialize_database(db_path=None):
    """Create/seed a fresh DB or validate v1. Never reset an existing database.

    Unversioned nonempty databases and future versions are rejected: migration
    needs an explicit design rather than silently replacing user records.
    """
    path = Path(db_path) if db_path is not None else DATABASE
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        con.execute('PRAGMA foreign_keys = ON')
        # executescript commits any prior transaction; put BEGIN in the script.
        con.executescript('BEGIN IMMEDIATE;')
        version = con.execute('PRAGMA user_version').fetchone()[0]
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if version not in (0, 1) or (version == 0 and tables):
            raise RuntimeError('Unsupported existing database schema; no data was reset.')
        if version == 1:
            required = {
                'menu_items': {'id', 'name', 'category', 'price', 'stock_qty', 'allergens', 'available'},
                'table_sessions': {'session_id', 'table_no', 'status', 'created_at'},
                'order_line_items': {'line_id', 'session_id', 'item_id', 'quantity', 'special_instructions'},
                'receipts': {'receipt_id', 'session_id', 'table_no', 'currency', 'subtotal_cents', 'tax_cents', 'service_cents', 'total_cents', 'tax_rate', 'service_rate', 'created_at'},
                'receipt_line_items': {'receipt_id', 'line_id', 'item_id', 'name', 'quantity', 'special_instructions', 'unit_price_cents', 'line_total_cents'},
                'schema_metadata': {'key', 'value'},
            }
            for table, columns in required.items():
                actual = {r[1] for r in con.execute(f'PRAGMA table_info({table})')}
                if not columns <= actual:
                    raise RuntimeError(f'Incompatible {table} schema; no data was reset.')
            if con.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('Existing database has invalid foreign keys.')
            con.commit()
        else:
            statement = ''
            for line in (ROOT / 'init_restaurant_db.sql').read_text(encoding='utf-8').splitlines(True):
                statement += line
                if sqlite3.complete_statement(statement):
                    con.execute(statement)
                    statement = ''
            if statement.strip():
                raise RuntimeError('Incomplete schema SQL.')
            con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
    return path
