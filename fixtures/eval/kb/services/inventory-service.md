---
name: Inventory service
service: inventory-service
tables: [inventory_levels, reservations]
owner: fulfilment-team
updated: 2026-06-20
related: [2026-06-15-oversell]
---
# Inventory service

Keeps stock counts per warehouse in `inventory_levels` and holds stock for open
checkouts in `reservations`.

## Reservations

A reservation lasts 15 minutes. The `inventory-sweeper` cron releases expired
reservations every 5 minutes. Since the June incident the sweeper compares
against database time, not the host clock.
