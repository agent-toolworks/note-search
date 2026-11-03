---
name: ADR-012 transactional outbox
tables: [outbox_events]
updated: 2026-01-15
---
# ADR-012: transactional outbox for events

Events are written to `outbox_events` in the same database transaction as the
change, so an order is never saved without its event or the other way round.
