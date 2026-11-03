# Notification service

Sends order confirmations and shipping emails through Mailjet. It consumes
`outbox_events` in batches of 100.

## Idempotency

Each email carries an idempotency key made from the event id, checked in Redis
before sending. Added after customers got the same confirmation twice when the
consumer restarted halfway through a batch.

## Owners

growth-team. Pager: #notify-oncall.
