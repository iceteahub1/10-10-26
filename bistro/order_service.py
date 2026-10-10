"""Real SQLite menu/cart/checkout service. No FSM or model calls.

Money is returned as integer centavos and fixed-decimal PHP strings. Carts are
unreserved: checkout is the final stock/price authority. One receipt per session
makes retries idempotent. A new order requires a new session.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4
from bistro.database import connection
from bistro.session_store import validate_session_id


class OrderError(ValueError):
    """Invalid restaurant operation; the transaction leaves data unchanged."""


def _text(value, label, limit=500, allow_empty=False):
    if not isinstance(value, str) or len(value) > limit or (not allow_empty and not value.strip()):
        raise OrderError(f'{label} must be text of at most {limit} characters.')
    return value.strip()


def _quantity(value):
    if type(value) is not int or not 1 <= value <= 10000:
        raise OrderError('Quantity must be an integer between 1 and 10000; use remove_item to delete.')
    return value


def _id(value):
    if type(value) is not int or value <= 0:
        raise OrderError('Item/line ID must be a positive integer.')
    return value


def _cents(price):
    amount = Decimal(str(price))
    if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal('0.01')):
        raise OrderError('Invalid database price.')
    return int(amount * 100)


def _rate(value):
    try:
        rate = Decimal(str(value))
    except InvalidOperation as exc:
        raise OrderError('Rates must be decimal fractions between zero and one.') from exc
    if not rate.is_finite() or not 0 <= rate <= 1:
        raise OrderError('Rates must be decimal fractions between zero and one.')
    return rate


def _totals(subtotal, tax_rate=0, service_rate=0):
    tax_rate, service_rate = _rate(tax_rate), _rate(service_rate)
    tax = int((subtotal * tax_rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    service = int((subtotal * service_rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    result = {'currency': 'PHP', 'subtotal_cents': subtotal, 'tax_cents': tax,
              'service_cents': service, 'total_cents': subtotal + tax + service,
              'tax_rate': str(tax_rate), 'service_rate': str(service_rate)}
    for name in ('subtotal', 'tax', 'service', 'total'):
        result[name] = f"{Decimal(result[name + '_cents']) / 100:.2f}"
    return result


def _session(con, session_id, *, editable=False):
    validate_session_id(session_id)
    row = con.execute('SELECT * FROM table_sessions WHERE session_id=?', (session_id,)).fetchone()
    if row is None:
        raise OrderError('Unknown session; create a table session first.')
    if editable and row['status'] != 'open':
        raise OrderError('This session has already checked out.')
    return row


def _menu_row(row):
    result = dict(row)
    result['price_cents'] = _cents(row['price'])
    result['price'] = f"{Decimal(result['price_cents']) / 100:.2f}"
    result['allergens'] = [x.strip() for x in row['allergens'].split(',') if x.strip()]
    return result


def browse_menu(category=None, *, db_path=None):
    with connection(db_path) as con:
        if category is None:
            rows = con.execute('SELECT * FROM menu_items ORDER BY category, name').fetchall()
        else:
            category = _text(category, 'Category', 100)
            rows = con.execute('SELECT * FROM menu_items WHERE category=? COLLATE NOCASE ORDER BY name', (category,)).fetchall()
        return [_menu_row(row) for row in rows]


def check_stock(item_id, *, db_path=None):
    with connection(db_path) as con:
        row = con.execute('SELECT * FROM menu_items WHERE id=?', (_id(item_id),)).fetchone()
        if row is None:
            raise OrderError('Unknown menu item.')
        return {'item_id': item_id, 'stock_qty': row['stock_qty'],
                'available': bool(row['available'] and row['stock_qty'] > 0)}


def check_allergen(item_id, allergen, *, db_path=None):
    allergen = _text(allergen, 'Allergen', 100).casefold()
    with connection(db_path) as con:
        row = con.execute('SELECT allergens FROM menu_items WHERE id=?', (_id(item_id),)).fetchone()
        if row is None:
            raise OrderError('Unknown menu item.')
        return allergen in {x.strip().casefold() for x in row['allergens'].split(',')}


def _resolve(con, name):
    name = _text(name, 'Item name', 100)
    rows = con.execute('SELECT * FROM menu_items WHERE name=? COLLATE NOCASE', (name,)).fetchall()
    if not rows:
        # Literal substring: SQL wildcard characters are never interpreted.
        rows = con.execute('SELECT * FROM menu_items WHERE instr(lower(name), lower(?)) > 0', (name,)).fetchall()
    if not rows:
        raise OrderError('Unknown menu item.')
    if len(rows) != 1:
        raise OrderError('Ambiguous item name; use an exact menu name.')
    return rows[0]


def _validate_stock(con, session_id, item_id, quantity, excluded_line=None):
    row = con.execute('SELECT * FROM menu_items WHERE id=?', (item_id,)).fetchone()
    if row is None or not row['available']:
        raise OrderError('Menu item is unavailable.')
    other = con.execute('SELECT COALESCE(sum(quantity),0) FROM order_line_items WHERE session_id=? AND item_id=? AND (? IS NULL OR line_id != ?)',
                        (session_id, item_id, excluded_line, excluded_line)).fetchone()[0]
    if quantity + other > row['stock_qty']:
        raise OrderError('Insufficient stock for all customizations of this item.')


def _cart(con, session_id, tax_rate=0, service_rate=0):
    session = _session(con, session_id)
    rows = con.execute('''SELECT l.*, m.name, m.price, m.stock_qty, m.available
                          FROM order_line_items l JOIN menu_items m ON m.id=l.item_id
                          WHERE l.session_id=? ORDER BY l.line_id''', (session_id,)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item['unit_price_cents'] = _cents(item.pop('price'))
        item['line_total_cents'] = item['quantity'] * item['unit_price_cents']
        item['unit_price'] = f"{Decimal(item['unit_price_cents']) / 100:.2f}"
        item['line_total'] = f"{Decimal(item['line_total_cents']) / 100:.2f}"
        items.append(item)
    return {'session_id': session_id, 'table_no': session['table_no'], 'status': session['status'],
            'items': items, **_totals(sum(i['line_total_cents'] for i in items), tax_rate, service_rate)}


def view_cart(session_id, *, db_path=None, tax_rate=0, service_rate=0):
    with connection(db_path) as con:
        session = _session(con, session_id)
        if session['status'] == 'checked_out':
            return _receipt(con, session_id)
        return _cart(con, session_id, tax_rate, service_rate)


def add_item(session_id, item_name, qty, instructions='', *, db_path=None):
    qty = _quantity(qty)
    instructions = _text(instructions, 'Instructions', allow_empty=True)
    with connection(db_path, write=True) as con:
        _session(con, session_id, editable=True)
        item = _resolve(con, item_name)
        _validate_stock(con, session_id, item['id'], qty)
        existing = con.execute('SELECT * FROM order_line_items WHERE session_id=? AND item_id=? AND special_instructions=?',
                               (session_id, item['id'], instructions)).fetchone()
        if existing:
            _quantity(existing['quantity'] + qty)
            con.execute('UPDATE order_line_items SET quantity=quantity+? WHERE line_id=?', (qty, existing['line_id']))
        else:
            con.execute('INSERT INTO order_line_items(session_id,item_id,quantity,special_instructions) VALUES (?,?,?,?)',
                        (session_id, item['id'], qty, instructions))
        return _cart(con, session_id)


def _line(con, session_id, line_id):
    _session(con, session_id, editable=True)
    row = con.execute('SELECT * FROM order_line_items WHERE session_id=? AND line_id=?', (session_id, _id(line_id))).fetchone()
    if row is None:
        raise OrderError('Unknown cart line for this session.')
    return row


def change_quantity(session_id, line_id, qty, *, db_path=None):
    qty = _quantity(qty)
    with connection(db_path, write=True) as con:
        line = _line(con, session_id, line_id)
        _validate_stock(con, session_id, line['item_id'], qty, line_id)
        con.execute('UPDATE order_line_items SET quantity=? WHERE line_id=?', (qty, line_id))
        return _cart(con, session_id)


def customize_item(session_id, line_id, instructions, *, db_path=None):
    instructions = _text(instructions, 'Instructions', allow_empty=True)
    with connection(db_path, write=True) as con:
        line = _line(con, session_id, line_id)
        other = con.execute('SELECT line_id FROM order_line_items WHERE session_id=? AND item_id=? AND special_instructions=? AND line_id!=?',
                            (session_id, line['item_id'], instructions, line_id)).fetchone()
        if other:
            raise OrderError('That customization already has a cart line; change its quantity instead.')
        con.execute('UPDATE order_line_items SET special_instructions=? WHERE line_id=?', (instructions, line_id))
        return _cart(con, session_id)


def remove_item(session_id, line_id, *, db_path=None):
    with connection(db_path, write=True) as con:
        _line(con, session_id, line_id)
        con.execute('DELETE FROM order_line_items WHERE line_id=?', (line_id,))
        return _cart(con, session_id)


def _receipt(con, session_id):
    receipt = con.execute('SELECT * FROM receipts WHERE session_id=?', (session_id,)).fetchone()
    if receipt is None:
        raise OrderError('No receipt for this session.')
    result = dict(receipt)
    result['status'] = 'checked_out'
    result['items'] = [dict(r) for r in con.execute('SELECT * FROM receipt_line_items WHERE receipt_id=? ORDER BY line_id', (receipt['receipt_id'],))]
    for item in result['items']:
        item['unit_price'] = f"{Decimal(item['unit_price_cents']) / 100:.2f}"
        item['line_total'] = f"{Decimal(item['line_total_cents']) / 100:.2f}"
    for name in ('subtotal', 'tax', 'service', 'total'):
        result[name] = f"{Decimal(result[name + '_cents']) / 100:.2f}"
    return result


def get_receipt(session_id, *, db_path=None):
    with connection(db_path) as con:
        _session(con, session_id)
        return _receipt(con, session_id)


def checkout(session_id, *, db_path=None, tax_rate=0, service_rate=0):
    """Commit a caller-confirmed order; conversation confirmation belongs to Phase 3.

    BEGIN IMMEDIATE serializes checkout and cart writes, including other processes.
    Stock, price snapshots, receipt and session status commit or roll back together.
    """
    with connection(db_path, write=True) as con:
        session = _session(con, session_id)
        if session['status'] == 'checked_out':
            return _receipt(con, session_id)
        cart = _cart(con, session_id, tax_rate, service_rate)
        if not cart['items']:
            raise OrderError('Cannot check out an empty cart.')
        totals = {}
        for item in cart['items']:
            totals[item['item_id']] = totals.get(item['item_id'], 0) + item['quantity']
        for item_id, quantity in totals.items():
            cursor = con.execute('UPDATE menu_items SET stock_qty=stock_qty-? WHERE id=? AND available=1 AND stock_qty>=?',
                                 (quantity, item_id, quantity))
            if cursor.rowcount != 1:
                raise OrderError('Stock changed or an item became unavailable; review your cart.')
        receipt_id = uuid4().hex
        con.execute('''INSERT INTO receipts(receipt_id,session_id,table_no,subtotal_cents,tax_cents,service_cents,total_cents,tax_rate,service_rate)
                       VALUES (?,?,?,?,?,?,?,?,?)''',
                    (receipt_id, session_id, session['table_no'], cart['subtotal_cents'], cart['tax_cents'], cart['service_cents'], cart['total_cents'], cart['tax_rate'], cart['service_rate']))
        for item in cart['items']:
            con.execute('INSERT INTO receipt_line_items VALUES (?,?,?,?,?,?,?,?)',
                        (receipt_id, item['line_id'], item['item_id'], item['name'], item['quantity'], item['special_instructions'], item['unit_price_cents'], item['line_total_cents']))
        con.execute("UPDATE table_sessions SET status='checked_out' WHERE session_id=?", (session_id,))
        return _receipt(con, session_id)
