---
name: Orders API
aka: [orders, OrderService]
service: orders-api
tables: [orders_v2, order_items]
owner: checkout-team
updated: 2026-10-14
related: [payment-gateway, orders_v2]
---
# Orders API

`OrderService` owns checkout: it creates the order, reserves stock in
`inventory-service` and charges through `payment-gateway`.

## Writes

`OrderService.createOrder` inserts into `orders_v2` and `order_items` in one
transaction. Nothing else writes to `orders_v2` except `refunds-worker`, which
updates `refund_state`.

## Retries

Payment calls are retried 3 times with jittered exponential backoff (base 200 ms,
cap 2 s) since 2026-10-14. Before that the retries were immediate, which caused
the retry storm in the October incident.

## Feature flags

`ORDERS_V2_DUAL_WRITE` wrote to both `orders` (v1) and `orders_v2` during the
migration. It was switched off on 2026-10-20.
