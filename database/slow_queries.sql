-- Sample queries designed to trigger SlowTrace diagnostic checks:
-- 1. Unindexed sequential scans on large tables (Fix Wizard: Rule 1)
-- 2. Heavy aggregation across joined tables
-- 3. Slow jsonb inspection on unindexed audit logs

-- Query 1: Unindexed category search & sorting (Triggers Seq Scan recommendation)
SELECT category, COUNT(*), AVG(price) 
FROM products 
WHERE category = 'Electronics' 
GROUP BY category;

-- Query 2: Heavy multi-table join without selective indexing
SELECT 
    u.id AS user_id,
    u.full_name,
    COUNT(o.id) AS total_orders,
    SUM(oi.quantity * oi.unit_price) AS total_spent
FROM users u
JOIN orders o ON u.id = o.user_id
JOIN order_items oi ON o.id = oi.order_id
WHERE o.order_status = 'delivered'
GROUP BY u.id, u.full_name
ORDER BY total_spent DESC
LIMIT 50;

-- Query 3: Full table scan with regex / jsonb payload filtering
SELECT * 
FROM audit_logs 
WHERE payload->>'ip_address' LIKE '192.168.1.1%'
ORDER BY created_at DESC 
LIMIT 100;

-- Query 4: Subquery causing repetitive scans
SELECT * 
FROM products 
WHERE id NOT IN (
    SELECT DISTINCT product_id 
    FROM order_items 
    WHERE quantity > 3
);
