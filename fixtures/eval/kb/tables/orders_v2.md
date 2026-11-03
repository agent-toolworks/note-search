---
name: orders_v2
aka: [orders table]
tables: [orders_v2]
service: orders-api
owner: checkout-team
updated: 2026-10-20
related: [adr-015-orders-v2-partitioning, orders-api]
---
# orders_v2

One row per order, partitioned by month on `created_at`.

## Columns

`id`, `customer_id`, `status`, `total_minor`, `currency`, `refund_state`,
`created_at`.

## Who writes

`orders-api` (`OrderService.createOrder`) inserts; `refunds-worker` updates
`refund_state`.

## Known problems

Lock timeouts during the month-end finance export: the export held a long
transaction on the primary. The export now reads from the replica.
