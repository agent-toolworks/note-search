---
name: Runbook - database failover
updated: 2026-03-30
---
# Database failover

Promote the replica with `pgctl promote orders-db-replica`, then point
`ORDERS_DB_HOST` at it. Expect 30 to 60 seconds of write errors.
