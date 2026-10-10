-- Phase 2 schema. price is Philippine pesos; receipt amounts are integer centavos.
-- Executed inside one transaction by bistro.database.initialize_database.
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS menu_items (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    category TEXT NOT NULL COLLATE NOCASE,
    price NUMERIC NOT NULL CHECK (typeof(price) IN ('integer', 'real') AND price BETWEEN 0 AND 1000000 AND price = round(price, 2)),
    stock_qty INTEGER NOT NULL CHECK (typeof(stock_qty) = 'integer' AND stock_qty >= 0),
    allergens TEXT NOT NULL DEFAULT '',
    available INTEGER NOT NULL DEFAULT 1 CHECK (available IN (0, 1))
);
CREATE TABLE IF NOT EXISTS table_sessions (
    session_id TEXT PRIMARY KEY,
    table_no INTEGER NOT NULL CHECK (typeof(table_no) = 'integer' AND table_no > 0),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'checked_out')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS order_line_items (
    line_id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES table_sessions(session_id),
    item_id INTEGER NOT NULL REFERENCES menu_items(id),
    quantity INTEGER NOT NULL CHECK (typeof(quantity) = 'integer' AND quantity BETWEEN 1 AND 10000),
    special_instructions TEXT NOT NULL DEFAULT '' CHECK (length(special_instructions) <= 500),
    UNIQUE (session_id, item_id, special_instructions)
);
CREATE TABLE IF NOT EXISTS receipts (
    receipt_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE REFERENCES table_sessions(session_id),
    table_no INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'PHP' CHECK (currency = 'PHP'),
    subtotal_cents INTEGER NOT NULL CHECK (subtotal_cents >= 0),
    tax_cents INTEGER NOT NULL CHECK (tax_cents >= 0),
    service_cents INTEGER NOT NULL CHECK (service_cents >= 0),
    total_cents INTEGER NOT NULL CHECK (total_cents = subtotal_cents + tax_cents + service_cents),
    tax_rate TEXT NOT NULL,
    service_rate TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS receipt_line_items (
    receipt_id TEXT NOT NULL REFERENCES receipts(receipt_id),
    line_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    special_instructions TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    line_total_cents INTEGER NOT NULL CHECK (line_total_cents = quantity * unit_price_cents),
    PRIMARY KEY (receipt_id, line_id)
);
CREATE INDEX IF NOT EXISTS menu_category_idx ON menu_items(category);
CREATE INDEX IF NOT EXISTS cart_session_idx ON order_line_items(session_id);
-- Seed exactly once. Repeated setup never replenishes stock or restores deleted rows.
INSERT INTO menu_items (id, name, category, price, stock_qty, allergens)
SELECT 1, 'Chicken Adobo', 'Mains', 185.00, 30, 'soy,wheat' WHERE NOT EXISTS (SELECT 1 FROM schema_metadata WHERE key='menu_seed_v1');
INSERT INTO menu_items (id, name, category, price, stock_qty, allergens)
SELECT 2, 'Pork Sisig', 'Mains', 220.00, 25, 'egg,soy' WHERE NOT EXISTS (SELECT 1 FROM schema_metadata WHERE key='menu_seed_v1');
INSERT INTO menu_items (id, name, category, price, stock_qty, allergens)
SELECT 3, 'Vegetable Kare-Kare', 'Mains', 195.00, 20, 'peanut' WHERE NOT EXISTS (SELECT 1 FROM schema_metadata WHERE key='menu_seed_v1');
INSERT INTO menu_items (id, name, category, price, stock_qty, allergens)
SELECT 4, 'Grilled Bangus', 'Mains', 245.00, 18, 'fish' WHERE NOT EXISTS (SELECT 1 FROM schema_metadata WHERE key='menu_seed_v1');
INSERT INTO menu_items (id, name, category, price, stock_qty, allergens)
SELECT 5, 'Lumpiang Shanghai', 'Starters', 125.00, 40, 'wheat,egg,soy' WHERE NOT EXISTS (SELECT 1 FROM schema_metadata WHERE key='menu_seed_v1');
INSERT INTO menu_items (id, name, category, price, stock_qty, allergens)
SELECT 6, 'Garlic Rice', 'Sides', 45.00, 60, '' WHERE NOT EXISTS (SELECT 1 FROM schema_metadata WHERE key='menu_seed_v1');
INSERT INTO menu_items (id, name, category, price, stock_qty, allergens)
SELECT 7, 'Steamed Rice', 'Sides', 35.00, 80, '' WHERE NOT EXISTS (SELECT 1 FROM schema_metadata WHERE key='menu_seed_v1');
INSERT INTO menu_items (id, name, category, price, stock_qty, allergens)
SELECT 8, 'Halo-Halo', 'Desserts', 135.00, 25, 'milk' WHERE NOT EXISTS (SELECT 1 FROM schema_metadata WHERE key='menu_seed_v1');
INSERT INTO menu_items (id, name, category, price, stock_qty, allergens)
SELECT 9, 'Leche Flan', 'Desserts', 95.00, 20, 'milk,egg' WHERE NOT EXISTS (SELECT 1 FROM schema_metadata WHERE key='menu_seed_v1');
INSERT INTO menu_items (id, name, category, price, stock_qty, allergens)
SELECT 10, 'Calamansi Juice', 'Drinks', 65.00, 50, '' WHERE NOT EXISTS (SELECT 1 FROM schema_metadata WHERE key='menu_seed_v1');
INSERT INTO menu_items (id, name, category, price, stock_qty, allergens)
SELECT 11, 'Bistro Iced Tea', 'Drinks', 55.00, 50, '' WHERE NOT EXISTS (SELECT 1 FROM schema_metadata WHERE key='menu_seed_v1');
INSERT INTO menu_items (id, name, category, price, stock_qty, allergens)
SELECT 12, 'Brewed Coffee', 'Drinks', 75.00, 40, '' WHERE NOT EXISTS (SELECT 1 FROM schema_metadata WHERE key='menu_seed_v1');
INSERT INTO schema_metadata (key, value) VALUES ('menu_seed_v1', 'complete') ON CONFLICT(key) DO NOTHING;
PRAGMA user_version = 1;
