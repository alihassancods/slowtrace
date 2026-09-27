# Dummy Database & Performance Benchmark Setup

This directory contains schemas, dummy data generation, and sample workload queries specifically created for testing SlowTrace with PostgreSQL.

## Files

- [`init.sql`](file:///home/shaheer/python/slowtrace/database/init.sql): Schema definition and seed script containing:
  - `users` (1,000 records)
  - `products` (5,000 records)
  - `orders` (10,000 records)
  - `order_items` (25,000 records)
  - `audit_logs` (20,000 JSONB records)
  - Automatically enables `pg_stat_statements`

- [`slow_queries.sql`](file:///home/shaheer/python/slowtrace/database/slow_queries.sql): Workload queries formulated to trigger SlowTrace's diagnostic checks (Sequential Scan recommendations, index suggestions, high-variance plans).

## How to Load into PostgreSQL

### 1. Using Docker (matches `docker-compose.yml`)

If your database container is running:
```bash
docker compose exec -T postgres psql -U slowtrace -d slowtrace < database/init.sql
```

### 2. Direct psql

```bash
psql -h localhost -p 5432 -U slowtrace -d slowtrace -f database/init.sql
```

### 3. Generate Slow Query Traffic for SlowTrace

To give SlowTrace slow queries to analyze, run the workload queries a few times:
```bash
docker compose exec -T postgres psql -U slowtrace -d slowtrace < database/slow_queries.sql
```
