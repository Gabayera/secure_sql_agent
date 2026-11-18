-- ============================================================
--  Demo schema — E-commerce store
--  For demonstrating the Secure SQL AI Agent.
-- ============================================================

DROP TABLE IF EXISTS inventory_movements, shipments, reviews,
                     payments, order_items, orders,
                     addresses, products, categories, customers CASCADE;

-- Customers ---------------------------------------------------
CREATE TABLE customers (
    id          SERIAL PRIMARY KEY,
    email       VARCHAR(255) NOT NULL UNIQUE,
    full_name   VARCHAR(150) NOT NULL,
    phone       VARCHAR(30),
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Addresses ---------------------------------------------------
CREATE TABLE addresses (
    id           SERIAL PRIMARY KEY,
    customer_id  INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    address_type VARCHAR(20) NOT NULL CHECK (address_type IN ('shipping','billing')),
    street       VARCHAR(255) NOT NULL,
    city         VARCHAR(100) NOT NULL,
    postal_code  VARCHAR(20),
    country      VARCHAR(60) NOT NULL,
    is_default   BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_addresses_customer ON addresses(customer_id);

-- Categories (self-referencing tree) --------------------------
CREATE TABLE categories (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(100) NOT NULL UNIQUE,
    slug       VARCHAR(100) NOT NULL UNIQUE,
    parent_id  INT REFERENCES categories(id)
);

-- Products ----------------------------------------------------
CREATE TABLE products (
    id              SERIAL PRIMARY KEY,
    sku             VARCHAR(40) NOT NULL UNIQUE,
    name            VARCHAR(200) NOT NULL,
    description     TEXT,
    category_id     INT REFERENCES categories(id),
    price           NUMERIC(10,2) NOT NULL CHECK (price >= 0),
    cost            NUMERIC(10,2) CHECK (cost >= 0),
    stock_quantity  INT NOT NULL DEFAULT 0,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_products_category ON products(category_id);

-- Orders ------------------------------------------------------
CREATE TABLE orders (
    id                   SERIAL PRIMARY KEY,
    order_number         VARCHAR(30) NOT NULL UNIQUE,
    customer_id          INT NOT NULL REFERENCES customers(id),
    shipping_address_id  INT REFERENCES addresses(id),
    status               VARCHAR(20) NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','paid','shipped','delivered','cancelled','refunded')),
    total_amount         NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_at           TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_orders_customer ON orders(customer_id);
CREATE INDEX ix_orders_status   ON orders(status);
CREATE INDEX ix_orders_created  ON orders(created_at DESC);

-- Order items ------------------------------------------------
CREATE TABLE order_items (
    id          SERIAL PRIMARY KEY,
    order_id    INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id  INT NOT NULL REFERENCES products(id),
    quantity    INT NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(10,2) NOT NULL CHECK (unit_price >= 0),
    subtotal    NUMERIC(12,2) NOT NULL
);
CREATE INDEX ix_order_items_order   ON order_items(order_id);
CREATE INDEX ix_order_items_product ON order_items(product_id);

-- Payments ---------------------------------------------------
CREATE TABLE payments (
    id         SERIAL PRIMARY KEY,
    order_id   INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    method     VARCHAR(30) NOT NULL,
    status     VARCHAR(20) NOT NULL CHECK (status IN ('pending','succeeded','failed','refunded')),
    amount     NUMERIC(12,2) NOT NULL,
    paid_at    TIMESTAMP
);
CREATE INDEX ix_payments_order ON payments(order_id);

-- Reviews ----------------------------------------------------
CREATE TABLE reviews (
    id           SERIAL PRIMARY KEY,
    product_id   INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    customer_id  INT NOT NULL REFERENCES customers(id),
    rating       SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment      TEXT,
    created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (product_id, customer_id)
);

-- Shipments --------------------------------------------------
CREATE TABLE shipments (
    id              SERIAL PRIMARY KEY,
    order_id        INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    carrier         VARCHAR(60) NOT NULL,
    tracking_number VARCHAR(80),
    status          VARCHAR(20) NOT NULL CHECK (status IN ('preparing','in_transit','delivered','returned')),
    shipped_at      TIMESTAMP
);

-- Inventory movements (audit trail) --------------------------
CREATE TABLE inventory_movements (
    id          SERIAL PRIMARY KEY,
    product_id  INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    change_qty  INT NOT NULL,
    reason      VARCHAR(60) NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_inventory_product ON inventory_movements(product_id);

-- ============================================================
--  Seed data
-- ============================================================

INSERT INTO categories (name, slug, parent_id) VALUES
    ('Electronics', 'electronics', NULL),
    ('Books',       'books',       NULL),
    ('Clothing',    'clothing',    NULL),
    ('Laptops',     'laptops',     1),
    ('Smartphones', 'smartphones', 1);

INSERT INTO customers (email, full_name, phone, is_active, created_at) VALUES
    ('alice@example.com',  'Alice Johnson', '555-0101', TRUE,  NOW() - INTERVAL '180 days'),
    ('bob@example.com',    'Bob Smith',     '555-0102', TRUE,  NOW() - INTERVAL '150 days'),
    ('carol@example.com',  'Carol Davis',   '555-0103', TRUE,  NOW() - INTERVAL '120 days'),
    ('dave@example.com',   'Dave Wilson',   '555-0104', TRUE,  NOW() - INTERVAL '90 days'),
    ('eve@example.com',    'Eve Martinez',  '555-0105', TRUE,  NOW() - INTERVAL '60 days'),
    ('frank@example.com',  'Frank Lee',     '555-0106', FALSE, NOW() - INTERVAL '365 days'),
    ('grace@example.com',  'Grace Kim',     '555-0107', TRUE,  NOW() - INTERVAL '30 days'),
    ('henry@example.com',  'Henry Brown',   '555-0108', TRUE,  NOW() - INTERVAL '20 days'),
    ('ivy@example.com',    'Ivy Chen',      '555-0109', TRUE,  NOW() - INTERVAL '10 days'),
    ('jack@example.com',   'Jack Taylor',   '555-0110', TRUE,  NOW() - INTERVAL '5 days');

INSERT INTO addresses (customer_id, address_type, street, city, postal_code, country, is_default) VALUES
    (1, 'shipping', '101 Oak St',     'Berlin',   '10115', 'Germany', TRUE),
    (2, 'shipping', '202 Elm Ave',    'Munich',   '80331', 'Germany', TRUE),
    (3, 'shipping', '303 Pine Rd',    'Hamburg',  '20095', 'Germany', TRUE),
    (4, 'shipping', '404 Maple Ln',   'Cologne',  '50667', 'Germany', TRUE),
    (5, 'shipping', '505 Cedar Blvd', 'Frankfurt','60311', 'Germany', TRUE),
    (7, 'shipping', '707 Birch St',   'Stuttgart','70173', 'Germany', TRUE),
    (8, 'shipping', '808 Willow Way', 'Dresden',  '01067', 'Germany', TRUE),
    (9, 'shipping', '909 Aspen Ct',   'Leipzig',  '04103', 'Germany', TRUE),
    (10,'shipping', '1010 Poplar Dr', 'Bremen',   '28195', 'Germany', TRUE);

INSERT INTO products (sku, name, description, category_id, price, cost, stock_quantity, is_active) VALUES
    ('LAP-001', 'ThinkPad X1 Carbon',   'Ultra-light business laptop',   4, 1499.00, 1200.00, 25, TRUE),
    ('LAP-002', 'MacBook Pro 14"',      'Apple laptop with M3 Pro',      4, 2199.00, 1800.00, 15, TRUE),
    ('LAP-003', 'Dell XPS 15',          'Windows laptop, 32GB RAM',      4, 1799.00, 1450.00, 10, TRUE),
    ('PHN-001', 'iPhone 15 Pro',        'Apple smartphone, 128GB',       5, 1099.00,  900.00, 40, TRUE),
    ('PHN-002', 'Samsung Galaxy S24',   'Android flagship, 256GB',       5,  999.00,  800.00, 30, TRUE),
    ('PHN-003', 'Google Pixel 8',       'Android phone, excellent camera',5,  799.00,  650.00, 20, TRUE),
    ('BOK-001', 'Clean Code',           'Robert C. Martin',              2,   35.00,   20.00, 50, TRUE),
    ('BOK-002', 'The Pragmatic Programmer','Hunt & Thomas',              2,   40.00,   22.00, 45, TRUE),
    ('BOK-003', 'Designing Data-Intensive Applications','Martin Kleppmann',2,50.00, 28.00, 30, TRUE),
    ('BOK-004', 'Refactoring',          'Martin Fowler',                 2,   45.00,   25.00, 35, TRUE),
    ('CLT-001', 'Basic T-Shirt',        'Cotton, unisex',                3,   20.00,    8.00,100, TRUE),
    ('CLT-002', 'Denim Jeans',          'Classic blue',                  3,   60.00,   25.00, 60, TRUE),
    ('CLT-003', 'Hoodie',               'Fleece-lined',                  3,   45.00,   18.00, 80, TRUE);

INSERT INTO orders (order_number, customer_id, shipping_address_id, status, total_amount, created_at) VALUES
    ('ORD-2024-0001', 1, 1, 'delivered', 1499.00, NOW() - INTERVAL '160 days'),
    ('ORD-2024-0002', 2, 2, 'delivered', 1099.00, NOW() - INTERVAL '140 days'),
    ('ORD-2024-0003', 3, 3, 'delivered',   75.00, NOW() - INTERVAL '110 days'),
    ('ORD-2024-0004', 1, 1, 'delivered', 2199.00, NOW() - INTERVAL '80 days'),
    ('ORD-2024-0005', 4, 4, 'delivered',  799.00, NOW() - INTERVAL '50 days'),
    ('ORD-2024-0006', 5, 5, 'shipped',    120.00, NOW() - INTERVAL '40 days'),
    ('ORD-2024-0007', 2, 2, 'shipped',     50.00, NOW() - INTERVAL '30 days'),
    ('ORD-2024-0008', 7, 6, 'paid',       999.00, NOW() - INTERVAL '20 days'),
    ('ORD-2024-0009', 1, 1, 'paid',        35.00, NOW() - INTERVAL '15 days'),
    ('ORD-2024-0010', 8, 7, 'paid',        80.00, NOW() - INTERVAL '10 days'),
    ('ORD-2024-0011', 9, 8, 'pending',    1799.00, NOW() - INTERVAL '5 days'),
    ('ORD-2024-0012', 10,9, 'pending',      40.00, NOW() - INTERVAL '3 days');

INSERT INTO order_items (order_id, product_id, quantity, unit_price, subtotal) VALUES
    (1,  1, 1, 1499.00, 1499.00),
    (2,  4, 1, 1099.00, 1099.00),
    (3,  7, 1,   35.00,   35.00),
    (3,  8, 1,   40.00,   40.00),
    (4,  2, 1, 2199.00, 2199.00),
    (5,  6, 1,  799.00,  799.00),
    (6, 12, 2,   60.00,  120.00),
    (7,  9, 1,   50.00,   50.00),
    (8,  5, 1,  999.00,  999.00),
    (9,  7, 1,   35.00,   35.00),
    (10, 8, 2,   40.00,   80.00),
    (11, 3, 1, 1799.00, 1799.00),
    (12, 7, 1,   35.00,   35.00);

INSERT INTO payments (order_id, method, status, amount, paid_at) VALUES
    (1,  'credit_card', 'succeeded', 1499.00, NOW() - INTERVAL '160 days'),
    (2,  'credit_card', 'succeeded', 1099.00, NOW() - INTERVAL '140 days'),
    (3,  'paypal',      'succeeded',   75.00, NOW() - INTERVAL '110 days'),
    (4,  'credit_card', 'succeeded', 2199.00, NOW() - INTERVAL '80 days'),
    (5,  'bank_transfer','succeeded', 799.00, NOW() - INTERVAL '50 days'),
    (6,  'paypal',      'succeeded',  120.00, NOW() - INTERVAL '40 days'),
    (7,  'credit_card', 'succeeded',   50.00, NOW() - INTERVAL '30 days'),
    (8,  'credit_card', 'succeeded',  999.00, NOW() - INTERVAL '20 days'),
    (9,  'paypal',      'succeeded',   35.00, NOW() - INTERVAL '15 days'),
    (10, 'credit_card', 'succeeded',   80.00, NOW() - INTERVAL '10 days');

INSERT INTO shipments (order_id, carrier, tracking_number, status, shipped_at) VALUES
    (1, 'DHL',     'DHL-2024-0001', 'delivered',  NOW() - INTERVAL '158 days'),
    (2, 'UPS',     'UPS-2024-0002', 'delivered',  NOW() - INTERVAL '138 days'),
    (3, 'DHL',     'DHL-2024-0003', 'delivered',  NOW() - INTERVAL '108 days'),
    (4, 'FedEx',   'FDX-2024-0004', 'delivered',  NOW() - INTERVAL '78 days'),
    (5, 'DHL',     'DHL-2024-0005', 'delivered',  NOW() - INTERVAL '48 days'),
    (6, 'UPS',     'UPS-2024-0006', 'in_transit', NOW() - INTERVAL '38 days'),
    (7, 'DHL',     'DHL-2024-0007', 'in_transit', NOW() - INTERVAL '28 days');

INSERT INTO reviews (product_id, customer_id, rating, comment, created_at) VALUES
    (1, 1, 5, 'Excellent laptop, very fast!',         NOW() - INTERVAL '150 days'),
    (4, 2, 5, 'Best phone I have ever owned.',         NOW() - INTERVAL '130 days'),
    (7, 3, 4, 'Great book for clean code practices.',  NOW() - INTERVAL '100 days'),
    (8, 3, 5, 'Essential reading for developers.',     NOW() - INTERVAL '100 days'),
    (2, 1, 5, 'Amazing performance.',                  NOW() - INTERVAL '70 days'),
    (6, 4, 4, 'Good camera, solid battery.',           NOW() - INTERVAL '40 days'),
    (5, 7, 5, 'Fast, smooth, excellent display.',      NOW() - INTERVAL '15 days');

INSERT INTO inventory_movements (product_id, change_qty, reason, created_at) VALUES
    (1, 25, 'initial_stock', NOW() - INTERVAL '200 days'),
    (2, 15, 'initial_stock', NOW() - INTERVAL '200 days'),
    (3, 10, 'initial_stock', NOW() - INTERVAL '200 days'),
    (4, 40, 'initial_stock', NOW() - INTERVAL '200 days'),
    (5, 30, 'initial_stock', NOW() - INTERVAL '200 days'),
    (6, 20, 'initial_stock', NOW() - INTERVAL '200 days'),
    (7, 50, 'initial_stock', NOW() - INTERVAL '200 days'),
    (8, 45, 'initial_stock', NOW() - INTERVAL '200 days'),
    (9, 30, 'initial_stock', NOW() - INTERVAL '200 days'),
    (10,35, 'initial_stock', NOW() - INTERVAL '200 days'),
    (11,100,'initial_stock', NOW() - INTERVAL '200 days'),
    (12,60, 'initial_stock', NOW() - INTERVAL '200 days'),
    (13,80, 'initial_stock', NOW() - INTERVAL '200 days');
