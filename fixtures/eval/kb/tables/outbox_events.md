---
name: outbox_events
tables: [outbox_events]
service: notification-service
owner: platform-team
updated: 2026-08-22
related: [adr-012-outbox-pattern]
---
# outbox_events

Events written in the same transaction as the change they describe, then sent
by consumers. Rows older than 7 days are deleted by the `outbox-janitor` job.
