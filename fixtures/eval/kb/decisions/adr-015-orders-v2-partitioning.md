---
name: ADR-015 orders_v2 partitioned by month
tables: [orders_v2]
updated: 2026-04-20
---
# ADR-015: move orders to a table partitioned by month

The v1 `orders` table had grown to 900 million rows and vacuum could not keep
up. orders_v2 is partitioned by month on `created_at`, so old months can be
detached and archived.
