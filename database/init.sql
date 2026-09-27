-- Schema and Dummy Data for SlowTrace Testing & Performance Benchmarks
-- Safe for PostgreSQL 14+

-- 1. Enable pg_stat_statements extension (required by SlowTrace)
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- 2. Drop existing tables if re-running
DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS audit_logs CASCADE;

-- 3. Create tables

-- Users table
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    full_name VARCHAR(150),
    country VARCHAR(100) DEFAULT 'US',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Products table (intentionally without an index on category to simulate slow unindexed queries)
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    sku VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(100) NOT NULL,
    price NUMERIC(10, 2) NOT NULL,
    stock_quantity INT DEFAULT 0,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Orders table
CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE CASCADE,
    order_status VARCHAR(50) DEFAULT 'pending',
    total_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    shipping_address TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Order items table (many-to-many join)
CREATE TABLE order_items (
    id SERIAL PRIMARY KEY,
    order_id INT REFERENCES orders(id) ON DELETE CASCADE,
    product_id INT REFERENCES products(id) ON DELETE RESTRICT,
    quantity INT NOT NULL DEFAULT 1,
    unit_price NUMERIC(10, 2) NOT NULL
);

-- Audit logs table (large sequential table useful for testing table bloat, slow scans, vacuum analyze)
CREATE TABLE audit_logs (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL,
    entity_name VARCHAR(50) NOT NULL,
    entity_id INT,
    payload JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 4. Seed Dummy Data

-- Seed 1,000 Users
INSERT INTO users (username, email, full_name, country, is_active, created_at)
SELECT
    'user_' || g || '_' || floor(random() * 100000)::text,
    'user_' || g || '@example.com',
    'Customer ' || g,
    (ARRAY['US', 'UK', 'CA', 'DE', 'FR', 'PK', 'IN', 'JP', 'AU'])[floor(random() * 9 + 1)],
    (random() > 0.1),
    NOW() - (random() * interval '365 days')
FROM generate_series(1, 1000) AS g;

-- Seed 5,000 Products across several categories
INSERT INTO products (sku, name, category, price, stock_quantity, description, created_at)
SELECT
    'SKU-' || lpad(g::text, 6, '0'),
    'Product Item ' || g,
    (ARRAY['Electronics', 'Books', 'Clothing', 'Home & Kitchen', 'Sports', 'Toys'])[floor(random() * 6 + 1)],
    (random() * 500 + 5)::numeric(10, 2),
    floor(random() * 200)::int,
    'Comprehensive description for product ' || g || ' with extra details to increase row size.',
    NOW() - (random() * interval '180 days')
FROM generate_series(1, 5000) AS g;

-- Seed 10,000 Orders
INSERT INTO orders (user_id, order_status, total_amount, shipping_address, created_at)
SELECT
    floor(random() * 1000 + 1)::int,
    (ARRAY['pending', 'processing', 'shipped', 'delivered', 'cancelled'])[floor(random() * 5 + 1)],
    (random() * 1000 + 20)::numeric(12, 2),
    floor(random() * 999 + 1)::text || ' Dummy Street, City, Country',
    NOW() - (random() * interval '90 days')
FROM generate_series(1, 10000) AS g;

-- Seed 25,000 Order Items
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT
    floor(random() * 10000 + 1)::int,
    floor(random() * 5000 + 1)::int,
    floor(random() * 5 + 1)::int,
    (random() * 200 + 10)::numeric(10, 2)
FROM generate_series(1, 25000) AS g;

-- Seed 20,000 Audit Logs
INSERT INTO audit_logs (event_type, entity_name, entity_id, payload, created_at)
SELECT
    (ARRAY['LOGIN', 'PURCHASE', 'LOGOUT', 'UPDATE_PROFILE', 'PASSWORD_RESET', 'REFUND'])[floor(random() * 6 + 1)],
    (ARRAY['orders', 'users', 'products', 'session'])[floor(random() * 4 + 1)],
    floor(random() * 5000 + 1)::int,
    jsonb_build_object(
        'ip_address', '192.168.1.' || floor(random() * 254 + 1)::text,
        'user_agent', 'Mozilla/5.0 (Dummy Agent)',
        'meta', 'Test log entry for slow trace benchmark'
    ),
    NOW() - (random() * interval '30 days')
FROM generate_series(1, 20000) AS g;

-- Analyze to update statistics for planner
ANALYZE users;
ANALYZE products;
ANALYZE orders;
ANALYZE order_items;
ANALYZE audit_logs;
