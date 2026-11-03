---
name: Database disk alarm
service: notification-service
tables: [outbox_events]
updated: 2026-08-22
---
# 2026-08-20: disk alarm on the primary database

## What happened

The primary's disk reached 92%. Most of the growth was `outbox_events`.

## Cause

The `outbox-janitor` job had been failing silently for three weeks after a
permissions change, so sent events were never deleted.

## Conclusion

The janitor now alerts when it deletes nothing for 2 days.
