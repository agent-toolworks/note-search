---
name: Duplicate confirmation emails
service: notification-service
updated: 2026-07-03
---
# 2026-07-02: customers got two confirmations

The notification consumer restarted mid-batch during a deploy and re-sent the
first half of the batch. Fixed with an idempotency key per event id.
